"""Durable Web Intelligence resource cache — SQLite authoritative, not Redis.

Uses same data/web_intel.sqlite3 as crawl store. Cache is evidence-supporting
infrastructure, not authority. Fresh hits skip network; revalidation still
runs SSRF/robots safety on refresh.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Optional

from execution.web_intel.url_norm import normalize_url

_LOCK = threading.Lock()
_DB = Path("data/web_intel.sqlite3")

DEFAULT_TTL = int(os.environ.get("WEB_CACHE_TTL_SECONDS", "86400"))
MIN_TTL = int(os.environ.get("WEB_CACHE_MIN_TTL_SECONDS", "60"))
MAX_TTL = int(os.environ.get("WEB_CACHE_MAX_TTL_SECONDS", "604800"))


def _conn() -> sqlite3.Connection:
    _DB.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(_DB), check_same_thread=False)
    c.row_factory = sqlite3.Row
    return c


def init_cache() -> None:
    with _LOCK:
        c = _conn()
        try:
            c.executescript(
                """
                CREATE TABLE IF NOT EXISTS web_cache (
                  cache_key TEXT PRIMARY KEY,
                  normalized_url TEXT NOT NULL,
                  final_url TEXT,
                  content_hash TEXT,
                  content_type TEXT,
                  http_status INTEGER,
                  etag TEXT,
                  last_modified TEXT,
                  body BLOB,
                  body_size INTEGER,
                  extracted_json TEXT,
                  fetched_at REAL,
                  last_validated_at REAL,
                  expires_at REAL,
                  robots_context TEXT,
                  headers_json TEXT,
                  version INTEGER DEFAULT 1,
                  previous_hash TEXT,
                  error TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_web_cache_exp ON web_cache(expires_at);
                """
            )
            c.commit()
        finally:
            c.close()


init_cache()

def _otel_cache(event: str, **attrs):
    try:
        from observability.tracing import start_span
        safe = {k: v for k, v in attrs.items() if k not in ("body", "headers", "cookie", "authorization")}
        # redaction: no credentials in URL attrs
        if "url" in safe and isinstance(safe["url"], str):
            u = safe["url"]
            if "@" in u:
                safe["url"] = u.split("@")[-1]
        with start_span(f"web.cache.{event}", kind="internal", attributes=safe):
            pass
    except Exception:
        pass



def cache_key_for(url: str) -> str:
    nu = normalize_url(url) or (url or "").strip()
    return hashlib.sha256(nu.encode("utf-8")).hexdigest()


def _ttl(seconds: Optional[int] = None) -> int:
    t = DEFAULT_TTL if seconds is None else int(seconds)
    return max(MIN_TTL, min(MAX_TTL, t))


def lookup(url: str, *, now: Optional[float] = None) -> dict[str, Any]:
    """Return {status: FRESH|STALE|MISSING, entry: dict|None}."""
    now = now if now is not None else time.time()
    key = cache_key_for(url)
    with _LOCK:
        c = _conn()
        try:
            r = c.execute("SELECT * FROM web_cache WHERE cache_key=?", (key,)).fetchone()
        finally:
            c.close()
    if not r:
        return {"status": "MISSING", "entry": None}
    entry = dict(r)
    # body is bytes
    exp = entry.get("expires_at") or 0
    if exp > now:
        _otel_cache("hit", status="FRESH", content_hash=(entry.get("content_hash") or "")[:16])
        return {"status": "FRESH", "entry": entry}
    _otel_cache("stale", content_hash=(entry.get("content_hash") or "")[:16])
    return {"status": "STALE", "entry": entry}


def put(
    url: str,
    *,
    body: bytes,
    content_type: Optional[str] = None,
    http_status: int = 200,
    final_url: Optional[str] = None,
    etag: Optional[str] = None,
    last_modified: Optional[str] = None,
    headers: Optional[dict] = None,
    extracted: Optional[dict] = None,
    robots_context: Optional[str] = None,
    ttl_seconds: Optional[int] = None,
    now: Optional[float] = None,
) -> dict:
    now = now if now is not None else time.time()
    nu = normalize_url(url) or url
    key = cache_key_for(nu)
    chash = hashlib.sha256(body).hexdigest() if body is not None else None
    prev = lookup(nu)
    previous_hash = None
    version = 1
    if prev["entry"]:
        previous_hash = prev["entry"].get("content_hash")
        version = int(prev["entry"].get("version") or 1)
        if previous_hash and chash and previous_hash != chash:
            version += 1
        elif previous_hash == chash:
            # same content — keep version, do not duplicate body if identical
            pass
    ttl = _ttl(ttl_seconds)
    row = {
        "cache_key": key,
        "normalized_url": nu,
        "final_url": final_url or nu,
        "content_hash": chash,
        "content_type": content_type,
        "http_status": http_status,
        "etag": etag,
        "last_modified": last_modified,
        "body": body,
        "body_size": len(body) if body else 0,
        "extracted_json": json.dumps(extracted) if extracted else None,
        "fetched_at": now if not (prev["entry"] and previous_hash == chash) else prev["entry"].get("fetched_at", now),
        "last_validated_at": now,
        "expires_at": now + ttl,
        "robots_context": robots_context,
        "headers_json": json.dumps(headers or {}),
        "version": version,
        "previous_hash": previous_hash if previous_hash != chash else (prev["entry"] or {}).get("previous_hash"),
        "error": None,
    }
    # preserve original fetched_at on same hash
    if prev["entry"] and previous_hash == chash and prev["entry"].get("fetched_at"):
        row["fetched_at"] = prev["entry"]["fetched_at"]
        row["body"] = prev["entry"].get("body") if prev["entry"].get("body") is not None else body
        row["body_size"] = prev["entry"].get("body_size") or row["body_size"]
    with _LOCK:
        c = _conn()
        try:
            cols = list(row.keys())
            c.execute(
                f"INSERT OR REPLACE INTO web_cache ({','.join(cols)}) VALUES ({','.join('?'*len(cols))})",
                tuple(row[k] for k in cols),
            )
            c.commit()
        finally:
            c.close()
    _otel_cache("update", version=version, content_changed=bool(previous_hash and previous_hash != chash))
    return row


def touch_validated(url: str, *, ttl_seconds: Optional[int] = None, now: Optional[float] = None) -> Optional[dict]:
    """304 path: keep body, update validation + expiry."""
    now = now if now is not None else time.time()
    key = cache_key_for(url)
    ttl = _ttl(ttl_seconds)
    with _LOCK:
        c = _conn()
        try:
            r = c.execute("SELECT * FROM web_cache WHERE cache_key=?", (key,)).fetchone()
            if not r:
                return None
            c.execute(
                "UPDATE web_cache SET last_validated_at=?, expires_at=? WHERE cache_key=?",
                (now, now + ttl, key),
            )
            c.commit()
            r2 = c.execute("SELECT * FROM web_cache WHERE cache_key=?", (key,)).fetchone()
            out = dict(r2) if r2 else None
            _otel_cache("304", host=(normalize_url(url) or "")[:80])
            return out
        finally:
            c.close()


def provenance(entry: dict, *, cache_status: str, used_at: Optional[float] = None) -> dict:
    used_at = used_at if used_at is not None else time.time()
    return {
        "source": "web_cache" if cache_status in ("FRESH", "STALE", "REVALIDATED", "CACHE_HIT") else "network",
        "cached": cache_status in ("FRESH", "CACHE_HIT", "REVALIDATED"),
        "cache_status": cache_status,
        "content_hash": entry.get("content_hash") if entry else None,
        "fetched_at": entry.get("fetched_at") if entry else None,
        "last_validated_at": entry.get("last_validated_at") if entry else None,
        "cache_used_at": used_at,
        "expires_at": entry.get("expires_at") if entry else None,
        "version": entry.get("version") if entry else None,
        "http_status": entry.get("http_status") if entry else None,
        "etag": entry.get("etag") if entry else None,
    }
