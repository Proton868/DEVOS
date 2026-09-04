"""Distributed tracing — observational only. Never grants authority."""
from __future__ import annotations

import contextvars
import json
import re
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Generator, Optional

_trace_ctx: contextvars.ContextVar[Optional["TraceContext"]] = contextvars.ContextVar("devos_trace", default=None)
_LOCK = threading.Lock()
_DB = Path("data/tracing.sqlite3")
_MAX_SPANS = 50000

_SECRET_RE = re.compile(
    r"(?i)(api[_-]?key|token|password|secret|bearer|authorization)\s*[:=]\s*\S+"
)


@dataclass(frozen=True)
class TraceContext:
    trace_id: str
    span_id: str
    parent_span_id: Optional[str] = None

    def child(self) -> "TraceContext":
        return TraceContext(trace_id=self.trace_id, span_id=uuid.uuid4().hex[:16], parent_span_id=self.span_id)


def new_trace() -> TraceContext:
    return TraceContext(trace_id=uuid.uuid4().hex, span_id=uuid.uuid4().hex[:16], parent_span_id=None)


def get_current_trace() -> Optional[TraceContext]:
    return _trace_ctx.get()


def set_current_trace(ctx: Optional[TraceContext]) -> None:
    _trace_ctx.set(ctx)


def continue_trace(trace_id: str, parent_span_id: Optional[str] = None) -> TraceContext:
    ctx = TraceContext(trace_id=trace_id, span_id=uuid.uuid4().hex[:16], parent_span_id=parent_span_id)
    _trace_ctx.set(ctx)
    return ctx


def propagate_headers(ctx: Optional[TraceContext] = None) -> dict[str, str]:
    ctx = ctx or get_current_trace()
    if not ctx:
        return {}
    return {
        "X-DevOS-Trace-ID": ctx.trace_id,
        "X-DevOS-Parent-Span-ID": ctx.span_id,
    }


def from_headers(headers: dict) -> Optional[TraceContext]:
    tid = headers.get("x-devos-trace-id") or headers.get("X-DevOS-Trace-ID")
    parent = headers.get("x-devos-parent-span-id") or headers.get("X-DevOS-Parent-Span-ID")
    if not tid:
        return None
    return TraceContext(trace_id=tid, span_id=uuid.uuid4().hex[:16], parent_span_id=parent)


def _sanitize_attrs(attrs: Optional[dict]) -> dict:
    out = {}
    for k, v in (attrs or {}).items():
        ks = str(k).lower()
        if any(x in ks for x in ("token", "secret", "password", "authorization", "api_key", "jwt")):
            continue
        s = str(v)
        if _SECRET_RE.search(s):
            s = "[REDACTED]"
        out[str(k)] = s[:500]
    return out


def _conn() -> sqlite3.Connection:
    _DB.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(_DB), check_same_thread=False)
    c.row_factory = sqlite3.Row
    return c


def init_tracing_db() -> None:
    with _LOCK:
        c = _conn()
        try:
            c.execute(
                """CREATE TABLE IF NOT EXISTS spans (
                    span_id TEXT PRIMARY KEY,
                    trace_id TEXT NOT NULL,
                    parent_span_id TEXT,
                    name TEXT,
                    kind TEXT,
                    status TEXT,
                    started_at REAL,
                    ended_at REAL,
                    attributes TEXT,
                    error TEXT
                )"""
            )
            c.execute("CREATE INDEX IF NOT EXISTS idx_spans_trace ON spans(trace_id)")
            c.commit()
        finally:
            c.close()


init_tracing_db()


def _persist_span(span: dict) -> None:
    with _LOCK:
        c = _conn()
        try:
            c.execute(
                """INSERT OR REPLACE INTO spans
                   (span_id,trace_id,parent_span_id,name,kind,status,started_at,ended_at,attributes,error)
                   VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (
                    span["span_id"], span["trace_id"], span.get("parent_span_id"),
                    span.get("name"), span.get("kind"), span.get("status"),
                    span.get("started_at"), span.get("ended_at"),
                    json.dumps(span.get("attributes") or {}),
                    json.dumps(span.get("error")) if span.get("error") else None,
                ),
            )
            c.execute(
                "DELETE FROM spans WHERE span_id NOT IN (SELECT span_id FROM spans ORDER BY started_at DESC LIMIT ?)",
                (_MAX_SPANS,),
            )
            c.commit()
        finally:
            c.close()


@contextmanager
def start_span(name: str, kind: str = "internal", attributes: Optional[dict] = None) -> Generator[TraceContext, None, None]:
    parent = get_current_trace()
    if parent:
        ctx = parent.child()
    else:
        ctx = new_trace()
    token = _trace_ctx.set(ctx)
    started = time.time()
    status = "ok"
    err = None
    try:
        yield ctx
    except Exception as e:
        status = "error"
        err = {"message": str(e)[:300]}
        raise
    finally:
        ended = time.time()
        _persist_span({
            "span_id": ctx.span_id,
            "trace_id": ctx.trace_id,
            "parent_span_id": ctx.parent_span_id,
            "name": name,
            "kind": kind,
            "status": status,
            "started_at": started,
            "ended_at": ended,
            "attributes": _sanitize_attrs(attributes),
            "error": err,
        })
        _trace_ctx.reset(token)


def end_span(ctx: TraceContext, status: str = "ok", attributes: Optional[dict] = None, error: Optional[dict] = None) -> None:
    _persist_span({
        "span_id": ctx.span_id,
        "trace_id": ctx.trace_id,
        "parent_span_id": ctx.parent_span_id,
        "name": (attributes or {}).get("name", "span"),
        "kind": (attributes or {}).get("kind", "internal"),
        "status": status,
        "started_at": time.time(),
        "ended_at": time.time(),
        "attributes": _sanitize_attrs(attributes),
        "error": error,
    })


def get_trace_spans(trace_id: str) -> list[dict]:
    with _LOCK:
        c = _conn()
        try:
            rows = c.execute(
                "SELECT * FROM spans WHERE trace_id=? ORDER BY started_at ASC", (trace_id,)
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            c.close()


def tracing_health() -> dict:
    with _LOCK:
        c = _conn()
        try:
            n = c.execute("SELECT COUNT(*) AS n FROM spans").fetchone()["n"]
        finally:
            c.close()
    return {
        "tracing_enabled": True,
        "trace_storage_available": True,
        "span_count": n,
        "retention_policy": f"max_spans={_MAX_SPANS}",
    }
