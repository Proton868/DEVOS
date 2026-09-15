"""Web Intelligence resource cache — in-process CACHE-ONLY (not domain authority).

Loss of cache does not affect domain correctness. Not reported as durable storage.
"""
from __future__ import annotations

import hashlib
import os
import threading
import time
from typing import Any, Optional

from execution.web_intel.url_norm import normalize_url

_LOCK = threading.Lock()
_CACHE: dict[str, dict] = {}

DEFAULT_TTL = int(os.environ.get("WEB_CACHE_TTL_SECONDS", "86400"))
MIN_TTL = int(os.environ.get("WEB_CACHE_MIN_TTL_SECONDS", "60"))
MAX_TTL = int(os.environ.get("WEB_CACHE_MAX_TTL_SECONDS", "604800"))


def init_cache() -> None:
    return


def _otel_cache(event: str, **attrs):
    try:
        from observability.tracing import start_span
        safe = {k: v for k, v in attrs.items() if k not in ("body", "headers", "cookie", "authorization")}
        with start_span(f"web.cache.{event}", attributes=safe):
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
    now = now if now is not None else time.time()
    key = cache_key_for(url)
    with _LOCK:
        entry = _CACHE.get(key)
    if not entry:
        return {"status": "MISSING", "entry": None}
    exp = entry.get("expires_at") or 0
    if exp > now:
        _otel_cache("hit", status="FRESH")
        return {"status": "FRESH", "entry": dict(entry)}
    _otel_cache("stale")
    return {"status": "STALE", "entry": dict(entry)}


def put(
    url: str,
    *,
    body: bytes,
    content_type: str = "",
    http_status: int = 200,
    etag: Optional[str] = None,
    last_modified: Optional[str] = None,
    final_url: Optional[str] = None,
    extracted: Optional[dict] = None,
    ttl_seconds: Optional[int] = None,
    robots_context: Optional[str] = None,
    headers: Optional[dict] = None,
    error: Optional[str] = None,
) -> dict:
    key = cache_key_for(url)
    now = time.time()
    content_hash = hashlib.sha256(body or b"").hexdigest()
    with _LOCK:
        prev = _CACHE.get(key)
        version = 1
        previous_hash = None
        fetched_at = now
        if prev:
            if prev.get("content_hash") == content_hash:
                fetched_at = prev.get("fetched_at") or now
                version = int(prev.get("version") or 1)
                previous_hash = prev.get("previous_hash")
            else:
                version = int(prev.get("version") or 1) + 1
                previous_hash = prev.get("content_hash")
        entry = {
            "cache_key": key,
            "normalized_url": normalize_url(url) or url,
            "final_url": final_url or url,
            "content_hash": content_hash,
            "content_type": content_type,
            "http_status": http_status,
            "etag": etag,
            "last_modified": last_modified,
            "body": body,
            "body_size": len(body or b""),
            "extracted_json": extracted,
            "fetched_at": fetched_at,
            "last_validated_at": now,
            "expires_at": now + _ttl(ttl_seconds),
            "robots_context": robots_context,
            "headers_json": headers or {},
            "version": version,
            "previous_hash": previous_hash,
            "error": error,
        }
        _CACHE[key] = entry
    _otel_cache("put", version=version)
    return dict(entry)


def touch_validated(url: str) -> None:
    key = cache_key_for(url)
    with _LOCK:
        ent = _CACHE.get(key)
        if ent:
            ent["last_validated_at"] = time.time()
            ent["expires_at"] = time.time() + _ttl()


def provenance(entry_or_key, cache_status: str = "CACHE_HIT") -> dict:
    if isinstance(entry_or_key, dict):
        e = entry_or_key
    else:
        hit = lookup(str(entry_or_key))
        e = hit.get("entry") or {}
    return {
        "cached": cache_status in ("CACHE_HIT", "FRESH", "STALE"),
        "cache_status": cache_status,
        "fetched_at": e.get("fetched_at"),
        "cache_used_at": time.time(),
        "content_hash": e.get("content_hash"),
        "version": e.get("version"),
    }


# Test helper: force expire without SQLite
def _force_expire(url: str) -> None:
    key = cache_key_for(url)
    with _LOCK:
        ent = _CACHE.get(key)
        if ent:
            ent["expires_at"] = time.time() - 10
