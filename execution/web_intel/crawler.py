"""Multi-page public crawl engine — UCIP is external; this only executes authorized jobs."""
from __future__ import annotations

import hashlib
import json
import time
import urllib.request
from typing import Optional
from urllib.parse import urlparse, urljoin

from execution.web_intel.url_norm import normalize_url, same_domain
from execution.web_intel.safety import is_url_allowed
from execution.web_intel.robots import fetch_robots, RobotsRules
from execution.web_intel.sitemap import fetch_sitemap_urls
from execution.web_intel.extract import extract_html


def _normalize_text_for_sim(text: str) -> str:
    import re
    s = (text or "").lower()
    s = re.sub(r"\s+", " ", s).strip()
    return s[:5000]


def _text_similarity(a: str, b: str) -> float:
    """Bounded Jaccard on word shingles — no vector DB."""
    wa = set(_normalize_text_for_sim(a).split())
    wb = set(_normalize_text_for_sim(b).split())
    if not wa or not wb:
        return 0.0
    inter = len(wa & wb)
    union = len(wa | wb)
    return inter / union if union else 0.0


# per-host last request time for crawl-delay
_HOST_LAST: dict[str, float] = {}
_MAX_CRAWL_DELAY = 30.0  # hard upper bound — malicious robots cannot hang forever


def _pace_host(host: str, delay: float) -> None:
    import time as _t
    delay = min(max(0.0, delay), _MAX_CRAWL_DELAY)
    if delay <= 0:
        return
    last = _HOST_LAST.get(host, 0.0)
    wait = delay - (_t.time() - last)
    if wait > 0:
        # interruptible wait for cancel
        end = _t.time() + wait
        while _t.time() < end:
            _t.sleep(min(0.25, end - _t.time()))
    _HOST_LAST[host] = _t.time()

from execution.web_intel.store import (
    create_crawl, get_crawl, update_crawl, emit_event, upsert_page,
    claim_queued_pages, list_pages,
)

# in-process cancel flags
_CANCEL: set[str] = set()


def request_cancel(crawl_id: str) -> None:
    _CANCEL.add(crawl_id)


def is_cancelled(crawl_id: str) -> bool:
    return crawl_id in _CANCEL


def _safe_fetch(
    url: str,
    *,
    timeout: float,
    max_bytes: int,
    force_refresh: bool = False,
) -> tuple[Optional[bytes], Optional[int], Optional[str], Optional[str], dict]:
    """Returns body, http_status, content_type, final_url_or_error, provenance.

    provenance.cache_status in FRESH/CACHE_HIT, REVALIDATED, FETCHED, FAILED, BLOCKED
    """
    from execution.web_intel import cache as web_cache

    ok, reason = is_url_allowed(url)
    if not ok:
        return None, None, None, f"blocked:{reason}", {"cache_status": "BLOCKED", "error": reason}

    if not force_refresh:
        hit = web_cache.lookup(url)
        if hit["status"] == "FRESH" and hit["entry"] and hit["entry"].get("body") is not None:
            e = hit["entry"]
            prov = web_cache.provenance(e, cache_status="CACHE_HIT")
            try:
                from execution.web_intel.store import emit_event
            except Exception:
                pass
            return (
                e["body"],
                e.get("http_status") or 200,
                e.get("content_type"),
                e.get("final_url") or url,
                prov,
            )

    headers = {"User-Agent": "DevOS-WebIntel/1.0 (+public-research)"}
    stale = web_cache.lookup(url) if not force_refresh else {"status": "MISSING", "entry": None}
    entry = stale.get("entry")
    if entry and not force_refresh:
        if entry.get("etag"):
            headers["If-None-Match"] = entry["etag"]
        if entry.get("last_modified"):
            headers["If-Modified-Since"] = entry["last_modified"]

    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            final = resp.geturl()
            ok2, reason2 = is_url_allowed(final)
            if not ok2:
                return None, None, None, f"redirect_blocked:{reason2}", {"cache_status": "BLOCKED", "error": reason2}
            status = getattr(resp, "status", None) or resp.getcode()
            if status == 304 and entry and entry.get("body") is not None:
                updated = web_cache.touch_validated(url)
                e = updated or entry
                prov = web_cache.provenance(e, cache_status="REVALIDATED")
                return e.get("body"), 304, e.get("content_type"), e.get("final_url") or final, prov
            raw = resp.read(max_bytes + 1)
            if len(raw) > max_bytes:
                raw = raw[:max_bytes]
            ctype = resp.headers.get("Content-Type")
            etag = resp.headers.get("ETag")
            last_mod = resp.headers.get("Last-Modified")
            web_cache.put(
                url,
                body=raw,
                content_type=ctype,
                http_status=status,
                final_url=final,
                etag=etag,
                last_modified=last_mod,
                headers={"etag": etag, "last_modified": last_mod},
            )
            e2 = web_cache.lookup(url)["entry"]
            prov = web_cache.provenance(e2 or {}, cache_status="FETCHED")
            return raw, status, ctype, final, prov
    except Exception as e:
        # stale fallback is optional — only if STALE and body present and not force
        if entry and entry.get("body") is not None and not force_refresh:
            prov = web_cache.provenance(entry, cache_status="STALE")
            return entry["body"], entry.get("http_status") or 200, entry.get("content_type"), entry.get("final_url") or url, prov
        return None, None, None, f"{type(e).__name__}:{str(e)[:180]}", {"cache_status": "FAILED", "error": str(e)[:180]}


def enqueue_url(
    crawl: dict,
    url: str,
    *,
    depth: int,
    parent_page_id: Optional[str] = None,
    priority_note: str = "link",
) -> Optional[dict]:
    nu = normalize_url(url)
    if not nu:
        return None
    root = crawl["normalized_root_url"]
    if crawl.get("same_domain_only") and not same_domain(
        nu, root, include_subdomains=bool(crawl.get("include_subdomains"))
    ):
        return None
    if depth > int(crawl["max_depth"]):
        return None
    ok, reason = is_url_allowed(nu)
    if not ok:
        return upsert_page({
            "crawl_id": crawl["crawl_id"], "url": url, "normalized_url": nu,
            "depth": depth, "parent_page_id": parent_page_id, "status": "BLOCKED",
            "error": reason, "trace_id": crawl.get("trace_id"),
        })
    return upsert_page({
        "crawl_id": crawl["crawl_id"], "url": url, "normalized_url": nu,
        "depth": depth, "parent_page_id": parent_page_id, "status": "QUEUED",
        "trace_id": crawl.get("trace_id"),
    })


def start_crawl(
    *,
    user_id: str,
    root_url: str,
    project_id: Optional[str] = None,
    persona_id: str = "nuha",
    mission_id: Optional[str] = None,
    max_depth: int = 2,
    max_pages: int = 30,
    max_bytes: int = 5_000_000,
    max_requests: int = 50,
    timeout: float = 15.0,
    same_domain_only: bool = True,
    include_subdomains: bool = False,
    obey_robots: bool = True,
    sitemap_enabled: bool = True,
    trace_id: Optional[str] = None,
) -> dict:
    nu = normalize_url(root_url)
    crawl = create_crawl({
        "user_id": user_id,
        "project_id": project_id,
        "persona_id": persona_id,
        "mission_id": mission_id,
        "root_url": root_url,
        "normalized_root_url": nu,
        "max_depth": max_depth,
        "max_pages": max_pages,
        "max_bytes": max_bytes,
        "max_requests": max_requests,
        "timeout": timeout,
        "same_domain_only": same_domain_only,
        "include_subdomains": include_subdomains,
        "obey_robots": obey_robots,
        "sitemap_enabled": sitemap_enabled,
        "trace_id": trace_id,
    })
    return run_crawl(crawl["crawl_id"])


def run_crawl(crawl_id: str) -> dict:
    crawl = get_crawl(crawl_id)
    if not crawl:
        raise ValueError("crawl_not_found")
    if crawl["status"] == "CANCELLED" or is_cancelled(crawl_id):
        return crawl

    update_crawl(crawl_id, status="DISCOVERING", started_at=time.time())
    emit_event(crawl_id, "crawl.started", {"root": crawl["root_url"]}, crawl.get("trace_id"))

    robots = RobotsRules()
    if crawl.get("obey_robots"):
        robots = fetch_robots(crawl["normalized_root_url"], timeout=float(crawl["timeout"]))
        emit_event(crawl_id, "crawl.robots", {
            "source": robots.source_url, "error": robots.error,
            "sitemaps": robots.sitemaps[:10],
        }, crawl.get("trace_id"))

    # root
    enqueue_url(crawl, crawl["normalized_root_url"], depth=0, priority_note="root")

    if crawl.get("sitemap_enabled"):
        sitemap_candidates = list(robots.sitemaps or [])
        p = urlparse(crawl["normalized_root_url"])
        sitemap_candidates.append(f"{p.scheme}://{p.netloc}/sitemap.xml")
        for sm in sitemap_candidates[:5]:
            if is_cancelled(crawl_id):
                break
            for u in fetch_sitemap_urls(sm, timeout=float(crawl["timeout"])):
                enqueue_url(crawl, u, depth=1, priority_note="sitemap")

    update_crawl(crawl_id, status="CRAWLING")
    stats = {"pages_fetched": 0, "pages_failed": 0, "pages_skipped": 0, "bytes": 0, "requests": 0}
    seen_hashes: dict[str, str] = {}
    partial_reason = None

    while True:
        crawl = get_crawl(crawl_id)
        if is_cancelled(crawl_id) or crawl["status"] == "CANCELLED":
            update_crawl(crawl_id, status="CANCELLED", cancelled_at=time.time(),
                         stats_json=json.dumps(stats))
            emit_event(crawl_id, "crawl.cancelled", stats, crawl.get("trace_id"))
            _CANCEL.discard(crawl_id)
            return get_crawl(crawl_id)

        if stats["pages_fetched"] >= int(crawl["max_pages"]):
            partial_reason = "PAGE_BUDGET_REACHED"
            break
        if stats["requests"] >= int(crawl["max_requests"]):
            partial_reason = "REQUEST_BUDGET_REACHED"
            break
        if stats["bytes"] >= int(crawl["max_bytes"]):
            partial_reason = "BYTE_BUDGET_REACHED"
            break

        batch = claim_queued_pages(crawl_id, limit=max(1, int(crawl.get("concurrency") or 2)))
        if not batch:
            break

        for page in batch:
            if is_cancelled(crawl_id):
                break
            if stats["pages_fetched"] >= int(crawl["max_pages"]):
                partial_reason = "PAGE_BUDGET_REACHED"
                break

            path = urlparse(page["normalized_url"]).path or "/"
            if crawl.get("obey_robots") and not robots.error:
                allowed, rule = robots.allowed(path)
                if not allowed:
                    upsert_page({**page, "status": "BLOCKED", "robots_decision": rule})
                    emit_event(crawl_id, "crawl.page.blocked", {"url": page["normalized_url"], "rule": rule})
                    stats["pages_skipped"] += 1
                    continue

            host = urlparse(page["normalized_url"]).hostname or ""
            delay = float(robots.crawl_delay or 0.0)
            if delay > 0 and not is_cancelled(crawl_id):
                _pace_host(host, delay)
            if is_cancelled(crawl_id):
                break
            body, status, ctype, final_or_err, fetch_prov = _safe_fetch(
                page["normalized_url"],
                timeout=float(crawl["timeout"]),
                max_bytes=min(500_000, int(crawl["max_bytes"]) - stats["bytes"]),
                force_refresh=bool(crawl.get("force_refresh")),
            )
            if fetch_prov.get("cache_status") not in ("CACHE_HIT",):
                stats["requests"] += 1
            if body is None:
                upsert_page({
                    **page, "status": "FAILED", "error": final_or_err,
                    "retry_count": int(page.get("retry_count") or 0) + 1,
                })
                emit_event(crawl_id, "crawl.page.failed", {"url": page["normalized_url"], "error": final_or_err})
                stats["pages_failed"] += 1
                continue

            final_url = final_or_err or page["normalized_url"]
            stats["bytes"] += len(body)
            chash = hashlib.sha256(body).hexdigest()
            if chash in seen_hashes:
                upsert_page({
                    **page, "status": "DUPLICATE", "content_hash": chash,
                    "duplicate_of_page_id": seen_hashes[chash],
                    "http_status": status, "content_type": ctype,
                    "canonical_url": final_url, "fetched_at": time.time(),
                    "error": "CONTENT_HASH",
                })
                stats["pages_skipped"] += 1
                continue
            seen_hashes[chash] = page["page_id"]

            text = body.decode("utf-8", errors="replace")
            ctype_l = (ctype or "").lower()
            extraction = {}
            if "html" in ctype_l or text.lstrip().startswith("<"):
                extraction = extract_html(text, base_url=final_url)
            elif "json" in ctype_l:
                extraction = {"extracted_text": text[:20000], "links": []}
            else:
                extraction = {"extracted_text": text[:20000], "links": []}

            # near-duplicate against previously extracted texts in this crawl
            near_of = None
            near_score = 0.0
            etext = extraction.get("extracted_text") or ""
            for prev in list_pages(crawl_id):
                if prev.get("status") != "EXTRACTED" or not prev.get("extracted_text"):
                    continue
                sc = _text_similarity(etext, prev["extracted_text"])
                if sc >= 0.92:
                    near_of = prev["page_id"]
                    near_score = sc
                    break
            if near_of:
                upsert_page({
                    **page, "status": "DUPLICATE", "content_hash": chash,
                    "duplicate_of_page_id": near_of, "http_status": status,
                    "content_type": ctype, "canonical_url": final_url,
                    "fetched_at": time.time(), "extracted_text": etext,
                    "error": f"NEAR_DUPLICATE:{near_score:.2f}",
                })
                stats["pages_skipped"] += 1
                continue

            upsert_page({
                **page,
                "status": "EXTRACTED",
                "http_status": status,
                "content_type": ctype,
                "content_length": len(body),
                "fetched_at": time.time(),
                "title": extraction.get("title"),
                "description": extraction.get("description"),
                "language": extraction.get("language"),
                "content_hash": chash,
                "extracted_text": extraction.get("extracted_text"),
                "links_json": json.dumps(extraction.get("links") or []),
                "extraction_json": json.dumps({**extraction, "_cache": fetch_prov}),
                "canonical_url": extraction.get("canonical_url") or final_url,
                "error": None if fetch_prov.get("cache_status") != "STALE" else "STALE_CACHE",
            })
            emit_event(crawl_id, "crawl.page.fetched", {
                "url": page["normalized_url"], "title": extraction.get("title"),
            }, crawl.get("trace_id"))
            stats["pages_fetched"] += 1

            # discover children
            depth = int(page.get("depth") or 0)
            if depth < int(crawl["max_depth"]):
                for link in (extraction.get("links") or [])[:100]:
                    enqueue_url(crawl, link, depth=depth + 1, parent_page_id=page["page_id"])

    final_status = "PARTIAL" if partial_reason else "COMPLETED"
    update_crawl(
        crawl_id,
        status=final_status,
        completed_at=time.time(),
        stats_json=json.dumps(stats),
        error=partial_reason,
    )
    emit_event(
        crawl_id,
        "crawl.partial" if partial_reason else "crawl.completed",
        {**stats, "reason": partial_reason},
        crawl.get("trace_id"),
    )
    return get_crawl(crawl_id)


def cancel_crawl(crawl_id: str) -> dict:
    request_cancel(crawl_id)
    update_crawl(crawl_id, status="CANCELLED", cancelled_at=time.time())
    emit_event(crawl_id, "crawl.cancelled", {})
    return get_crawl(crawl_id)


def resume_crawl(crawl_id: str) -> dict:
    crawl = get_crawl(crawl_id)
    if not crawl:
        raise ValueError("crawl_not_found")
    _CANCEL.discard(crawl_id)
    # reset FETCHING → QUEUED
    for p in list_pages(crawl_id, status="FETCHING"):
        upsert_page({**p, "status": "QUEUED"})
    if crawl["status"] in ("CANCELLED", "FAILED", "PARTIAL", "COMPLETED"):
        update_crawl(crawl_id, status="CRAWLING", cancelled_at=None, error=None)
    return run_crawl(crawl_id)


def build_report(crawl_id: str) -> dict:
    crawl = get_crawl(crawl_id)
    pages = list_pages(crawl_id)
    social = []
    entities = []
    for p in pages:
        try:
            ext = json.loads(p.get("extraction_json") or "{}")
        except Exception:
            ext = {}
        for s in ext.get("social_links") or []:
            social.append(s)
        for block in ext.get("json_ld") or []:
            if isinstance(block, dict):
                entities.append({
                    "type": block.get("@type"),
                    "name": block.get("name"),
                    "source_url": p.get("normalized_url"),
                    "page_id": p.get("page_id"),
                })
    by_status = {}
    for p in pages:
        by_status[p["status"]] = by_status.get(p["status"], 0) + 1
    return {
        "crawl_id": crawl_id,
        "root_url": crawl.get("root_url") if crawl else None,
        "status": crawl.get("status") if crawl else None,
        "stats": json.loads(crawl.get("stats_json") or "{}") if crawl else {},
        "pages_by_status": by_status,
        "pages_total": len(pages),
        "social_profiles_discovered": list(dict.fromkeys(social)),
        "structured_entities": entities[:50],
        "partial_reason": crawl.get("error") if crawl else None,
        "note": "PARTIAL means budgets or limits stopped the crawl; not a claim of site completeness.",
    }


def collect_evidence(crawl_id: str) -> list[dict]:
    out = []
    for p in list_pages(crawl_id):
        if p.get("status") not in ("EXTRACTED", "FETCHED"):
            continue
        if p.get("title"):
            out.append({
                "type": "page_title",
                "value": p["title"],
                "source_url": p.get("normalized_url"),
                "page_id": p["page_id"],
                "crawl_id": crawl_id,
                "content_hash": p.get("content_hash"),
            })
        try:
            ext = json.loads(p.get("extraction_json") or "{}")
        except Exception:
            ext = {}
        for s in ext.get("social_links") or []:
            out.append({
                "type": "social_link",
                "value": s,
                "source_url": p.get("normalized_url"),
                "page_id": p["page_id"],
                "crawl_id": crawl_id,
                "status": "DISCOVERED",
            })
        for block in ext.get("json_ld") or []:
            if isinstance(block, dict) and block.get("name"):
                out.append({
                    "type": "json_ld_name",
                    "value": block.get("name"),
                    "schema_type": block.get("@type"),
                    "source_url": p.get("normalized_url"),
                    "page_id": p["page_id"],
                    "crawl_id": crawl_id,
                })
    return out
