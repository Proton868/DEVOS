"""
Minimal durable outbox — same SQLite DB as delivery durable store.
Not an execution queue. Not an authorization authority.
Ensures domain state + event record commit together.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Optional

logger = logging.getLogger("devos.outbox")

_LOCK = threading.Lock()
_DB = Path("data/delivery_durable.sqlite3")
_HANDLERS: dict[str, Callable[[dict], None]] = {}


def _conn() -> sqlite3.Connection:
    _DB.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(_DB), check_same_thread=False)
    c.row_factory = sqlite3.Row
    return c


def init_outbox() -> None:
    with _LOCK:
        c = _conn()
        try:
            c.execute(
                """CREATE TABLE IF NOT EXISTS outbox_events (
                    id TEXT PRIMARY KEY,
                    event_type TEXT NOT NULL,
                    aggregate_type TEXT,
                    aggregate_id TEXT,
                    payload TEXT,
                    trace_id TEXT,
                    created_at REAL,
                    available_at REAL,
                    attempts INTEGER DEFAULT 0,
                    delivered_at REAL,
                    status TEXT DEFAULT 'pending',
                    last_error TEXT,
                    idempotency_key TEXT UNIQUE
                )"""
            )
            c.execute(
                "CREATE INDEX IF NOT EXISTS idx_outbox_pending ON outbox_events(status, available_at)"
            )
            c.commit()
        finally:
            c.close()


init_outbox()


def enqueue(
    event_type: str,
    *,
    aggregate_type: str = "",
    aggregate_id: str = "",
    payload: Optional[dict] = None,
    trace_id: Optional[str] = None,
    idempotency_key: Optional[str] = None,
    conn: Optional[sqlite3.Connection] = None,
) -> str:
    """Insert outbox row. Pass conn to participate in an outer transaction."""
    eid = uuid.uuid4().hex
    key = idempotency_key or f"{event_type}:{aggregate_id}:{uuid.uuid4().hex[:8]}"
    now = time.time()
    row = (
        eid, event_type, aggregate_type, aggregate_id,
        json.dumps(payload or {}, default=str),
        trace_id, now, now, 0, None, "pending", None, key,
    )
    owns = conn is None
    if owns:
        conn = _conn()
    try:
        try:
            conn.execute(
                """INSERT INTO outbox_events
                   (id,event_type,aggregate_type,aggregate_id,payload,trace_id,
                    created_at,available_at,attempts,delivered_at,status,last_error,idempotency_key)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                row,
            )
            if owns:
                conn.commit()
        except sqlite3.IntegrityError:
            # idempotent — return existing id if key exists
            cur = conn.execute(
                "SELECT id FROM outbox_events WHERE idempotency_key=?", (key,)
            ).fetchone()
            return cur["id"] if cur else eid
    finally:
        if owns:
            conn.close()
    return eid


def claim_pending(limit: int = 20) -> list[dict]:
    """Claim pending events for dispatch (status → processing)."""
    now = time.time()
    with _LOCK:
        c = _conn()
        try:
            rows = c.execute(
                """SELECT * FROM outbox_events
                   WHERE status='pending' AND available_at<=?
                   ORDER BY created_at ASC LIMIT ?""",
                (now, limit),
            ).fetchall()
            claimed = []
            for r in rows:
                c.execute(
                    "UPDATE outbox_events SET status='processing', attempts=attempts+1 WHERE id=? AND status='pending'",
                    (r["id"],),
                )
                if c.total_changes:
                    claimed.append(dict(r))
            c.commit()
            return claimed
        finally:
            c.close()


def mark_delivered(event_id: str) -> None:
    with _LOCK:
        c = _conn()
        try:
            c.execute(
                "UPDATE outbox_events SET status='delivered', delivered_at=? WHERE id=?",
                (time.time(), event_id),
            )
            c.commit()
        finally:
            c.close()


def mark_failed(event_id: str, error: str, *, backoff_sec: float = 5.0) -> None:
    with _LOCK:
        c = _conn()
        try:
            row = c.execute("SELECT attempts FROM outbox_events WHERE id=?", (event_id,)).fetchone()
            attempts = (row["attempts"] if row else 1) or 1
            delay = min(backoff_sec * (2 ** max(0, attempts - 1)), 300)
            status = "failed" if attempts >= 8 else "pending"
            c.execute(
                """UPDATE outbox_events SET status=?, last_error=?, available_at=? WHERE id=?""",
                (status, (error or "")[:500], time.time() + delay, event_id),
            )
            c.commit()
        finally:
            c.close()


def register_handler(event_type: str, fn: Callable[[dict], None]) -> None:
    _HANDLERS[event_type] = fn


def dispatch_once(limit: int = 20) -> dict:
    """Deliver claimed events to in-process handlers. Safe on restart."""
    claimed = claim_pending(limit)
    delivered = 0
    failed = 0
    for ev in claimed:
        et = ev["event_type"]
        try:
            payload = json.loads(ev["payload"] or "{}")
            handler = _HANDLERS.get(et) or _HANDLERS.get("*")
            if handler:
                handler({**ev, "payload": payload})
            # no handler = still mark delivered (observability sink)
            mark_delivered(ev["id"])
            delivered += 1
        except Exception as e:
            mark_failed(ev["id"], str(e))
            failed += 1
            logger.warning("outbox delivery failed %s: %s", ev["id"], e)
    return {"claimed": len(claimed), "delivered": delivered, "failed": failed}


def list_events(*, aggregate_id: Optional[str] = None, limit: int = 50) -> list[dict]:
    with _LOCK:
        c = _conn()
        try:
            if aggregate_id:
                rows = c.execute(
                    "SELECT * FROM outbox_events WHERE aggregate_id=? ORDER BY created_at DESC LIMIT ?",
                    (aggregate_id, limit),
                ).fetchall()
            else:
                rows = c.execute(
                    "SELECT * FROM outbox_events ORDER BY created_at DESC LIMIT ?", (limit,)
                ).fetchall()
            return [dict(r) for r in rows]
        finally:
            c.close()
