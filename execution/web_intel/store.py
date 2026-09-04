"""Durable crawl store — existing SQLite conventions."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Optional

_LOCK = threading.Lock()
_DB = Path("data/web_intel.sqlite3")


def _conn() -> sqlite3.Connection:
    _DB.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(_DB), check_same_thread=False)
    c.row_factory = sqlite3.Row
    return c


def init_store() -> None:
    with _LOCK:
        c = _conn()
        try:
            c.executescript(
                """
                CREATE TABLE IF NOT EXISTS crawls (
                  crawl_id TEXT PRIMARY KEY,
                  user_id TEXT NOT NULL,
                  project_id TEXT,
                  persona_id TEXT,
                  mission_id TEXT,
                  root_url TEXT,
                  normalized_root_url TEXT,
                  status TEXT,
                  created_at REAL,
                  started_at REAL,
                  completed_at REAL,
                  cancelled_at REAL,
                  max_depth INTEGER,
                  max_pages INTEGER,
                  max_bytes INTEGER,
                  max_requests INTEGER,
                  concurrency INTEGER,
                  timeout REAL,
                  same_domain_only INTEGER,
                  include_subdomains INTEGER,
                  obey_robots INTEGER,
                  sitemap_enabled INTEGER,
                  trace_id TEXT,
                  budgets_json TEXT,
                  stats_json TEXT,
                  error TEXT
                );
                CREATE TABLE IF NOT EXISTS crawl_pages (
                  page_id TEXT PRIMARY KEY,
                  crawl_id TEXT NOT NULL,
                  url TEXT,
                  normalized_url TEXT,
                  canonical_url TEXT,
                  depth INTEGER,
                  parent_page_id TEXT,
                  status TEXT,
                  http_status INTEGER,
                  content_type TEXT,
                  content_length INTEGER,
                  fetched_at REAL,
                  title TEXT,
                  description TEXT,
                  language TEXT,
                  content_hash TEXT,
                  extracted_text TEXT,
                  links_json TEXT,
                  extraction_json TEXT,
                  error TEXT,
                  retry_count INTEGER DEFAULT 0,
                  duplicate_of_page_id TEXT,
                  robots_decision TEXT,
                  trace_id TEXT,
                  UNIQUE(crawl_id, normalized_url)
                );
                CREATE TABLE IF NOT EXISTS crawl_events (
                  event_id TEXT PRIMARY KEY,
                  crawl_id TEXT NOT NULL,
                  event_type TEXT,
                  payload TEXT,
                  created_at REAL,
                  trace_id TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_pages_crawl ON crawl_pages(crawl_id, status);
                CREATE INDEX IF NOT EXISTS idx_events_crawl ON crawl_events(crawl_id, created_at);
                """
            )
            c.commit()
        finally:
            c.close()


init_store()


def new_id(prefix: str = "") -> str:
    return f"{prefix}{uuid.uuid4().hex}"


def create_crawl(data: dict) -> dict:
    cid = data.get("crawl_id") or new_id("cr_")
    now = time.time()
    row = {
        "crawl_id": cid,
        "user_id": data["user_id"],
        "project_id": data.get("project_id"),
        "persona_id": data.get("persona_id") or "nuha",
        "mission_id": data.get("mission_id"),
        "root_url": data["root_url"],
        "normalized_root_url": data.get("normalized_root_url") or data["root_url"],
        "status": "QUEUED",
        "created_at": now,
        "started_at": None,
        "completed_at": None,
        "cancelled_at": None,
        "max_depth": int(data.get("max_depth") or 2),
        "max_pages": int(data.get("max_pages") or 30),
        "max_bytes": int(data.get("max_bytes") or 5_000_000),
        "max_requests": int(data.get("max_requests") or 50),
        "concurrency": int(data.get("concurrency") or 2),
        "timeout": float(data.get("timeout") or 15.0),
        "same_domain_only": 1 if data.get("same_domain_only", True) else 0,
        "include_subdomains": 1 if data.get("include_subdomains") else 0,
        "obey_robots": 1 if data.get("obey_robots", True) else 0,
        "sitemap_enabled": 1 if data.get("sitemap_enabled", True) else 0,
        "trace_id": data.get("trace_id"),
        "budgets_json": json.dumps({}),
        "stats_json": json.dumps({"pages_fetched": 0, "pages_failed": 0, "bytes": 0, "requests": 0}),
        "error": None,
    }
    with _LOCK:
        c = _conn()
        try:
            c.execute(
                f"INSERT INTO crawls ({','.join(row.keys())}) VALUES ({','.join('?'*len(row))})",
                tuple(row.values()),
            )
            c.commit()
        finally:
            c.close()
    emit_event(cid, "crawl.created", {"root_url": row["root_url"]}, trace_id=row["trace_id"])
    return get_crawl(cid)


def get_crawl(crawl_id: str) -> Optional[dict]:
    with _LOCK:
        c = _conn()
        try:
            r = c.execute("SELECT * FROM crawls WHERE crawl_id=?", (crawl_id,)).fetchone()
            return dict(r) if r else None
        finally:
            c.close()


def list_crawls(user_id: str, limit: int = 50) -> list[dict]:
    with _LOCK:
        c = _conn()
        try:
            rows = c.execute(
                "SELECT * FROM crawls WHERE user_id=? ORDER BY created_at DESC LIMIT ?",
                (user_id, limit),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            c.close()


def update_crawl(crawl_id: str, **fields) -> Optional[dict]:
    s = get_crawl(crawl_id)
    if not s:
        return None
    s.update(fields)
    cols = [k for k in fields.keys()]
    if not cols:
        return s
    with _LOCK:
        c = _conn()
        try:
            c.execute(
                f"UPDATE crawls SET {', '.join(k+'=?' for k in cols)} WHERE crawl_id=?",
                tuple(fields[k] for k in cols) + (crawl_id,),
            )
            c.commit()
        finally:
            c.close()
    return get_crawl(crawl_id)


def emit_event(crawl_id: str, event_type: str, payload: Optional[dict] = None, trace_id: Optional[str] = None) -> None:
    eid = new_id("ev_")
    with _LOCK:
        c = _conn()
        try:
            c.execute(
                "INSERT INTO crawl_events (event_id,crawl_id,event_type,payload,created_at,trace_id) VALUES(?,?,?,?,?,?)",
                (eid, crawl_id, event_type, json.dumps(payload or {}), time.time(), trace_id),
            )
            c.commit()
        finally:
            c.close()
    try:
        from execution.outbox import enqueue
        enqueue(event_type, aggregate_type="crawl", aggregate_id=crawl_id,
                payload=payload or {}, trace_id=trace_id,
                idempotency_key=f"{event_type}:{crawl_id}:{eid}")
    except Exception:
        pass


def list_events(crawl_id: str, limit: int = 200) -> list[dict]:
    with _LOCK:
        c = _conn()
        try:
            rows = c.execute(
                "SELECT * FROM crawl_events WHERE crawl_id=? ORDER BY created_at ASC LIMIT ?",
                (crawl_id, limit),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            c.close()


def upsert_page(page: dict) -> dict:
    page = dict(page)
    page.setdefault("page_id", new_id("pg_"))
    with _LOCK:
        c = _conn()
        try:
            existing = c.execute(
                "SELECT page_id FROM crawl_pages WHERE crawl_id=? AND normalized_url=?",
                (page["crawl_id"], page["normalized_url"]),
            ).fetchone()
            if existing:
                page["page_id"] = existing["page_id"]
                cols = [k for k in page.keys() if k != "page_id"]
                c.execute(
                    f"UPDATE crawl_pages SET {', '.join(k+'=?' for k in cols)} WHERE page_id=?",
                    tuple(page[k] for k in cols) + (page["page_id"],),
                )
            else:
                c.execute(
                    f"INSERT INTO crawl_pages ({','.join(page.keys())}) VALUES ({','.join('?'*len(page))})",
                    tuple(page.values()),
                )
            c.commit()
        finally:
            c.close()
    return page


def get_page(page_id: str) -> Optional[dict]:
    with _LOCK:
        c = _conn()
        try:
            r = c.execute("SELECT * FROM crawl_pages WHERE page_id=?", (page_id,)).fetchone()
            return dict(r) if r else None
        finally:
            c.close()


def list_pages(crawl_id: str, status: Optional[str] = None) -> list[dict]:
    with _LOCK:
        c = _conn()
        try:
            if status:
                rows = c.execute(
                    "SELECT * FROM crawl_pages WHERE crawl_id=? AND status=? ORDER BY depth, fetched_at",
                    (crawl_id, status),
                ).fetchall()
            else:
                rows = c.execute(
                    "SELECT * FROM crawl_pages WHERE crawl_id=? ORDER BY depth, fetched_at",
                    (crawl_id,),
                ).fetchall()
            return [dict(r) for r in rows]
        finally:
            c.close()


def claim_queued_pages(crawl_id: str, limit: int = 5) -> list[dict]:
    with _LOCK:
        c = _conn()
        try:
            rows = c.execute(
                "SELECT * FROM crawl_pages WHERE crawl_id=? AND status='QUEUED' ORDER BY depth ASC LIMIT ?",
                (crawl_id, limit),
            ).fetchall()
            out = []
            for r in rows:
                c.execute(
                    "UPDATE crawl_pages SET status='FETCHING' WHERE page_id=? AND status='QUEUED'",
                    (r["page_id"],),
                )
                if c.total_changes:
                    d = dict(r)
                    d["status"] = "FETCHING"
                    out.append(d)
            c.commit()
            return out
        finally:
            c.close()
