"""Distributed tracing — observational spans in Postgres (not authority)."""
from __future__ import annotations

import contextvars
import re
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Generator, Optional

_trace_ctx: contextvars.ContextVar[Optional["TraceContext"]] = contextvars.ContextVar(
    "devos_trace", default=None
)
_SECRET_RE = re.compile(
    r"(?i)(api[_-]?key|token|password|secret|bearer|authorization)\s*[:=]\s*\S+"
)


@dataclass(frozen=True)
class TraceContext:
    trace_id: str
    span_id: str
    parent_span_id: Optional[str] = None

    def child(self) -> "TraceContext":
        return TraceContext(
            trace_id=self.trace_id,
            span_id=uuid.uuid4().hex[:16],
            parent_span_id=self.span_id,
        )


def new_trace() -> TraceContext:
    return TraceContext(trace_id=uuid.uuid4().hex, span_id=uuid.uuid4().hex[:16])


def get_current_trace() -> Optional[TraceContext]:
    return _trace_ctx.get()


def set_current_trace(ctx: Optional[TraceContext]) -> None:
    _trace_ctx.set(ctx)


def continue_trace(trace_id: str, parent_span_id: Optional[str] = None) -> TraceContext:
    ctx = TraceContext(
        trace_id=trace_id, span_id=uuid.uuid4().hex[:16], parent_span_id=parent_span_id
    )
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


def init_tracing_db() -> None:
    return


def _persist_span(
    *,
    trace_id: str,
    span_id: str,
    parent_span_id: Optional[str],
    name: str,
    status: str,
    attrs: dict,
    started_at: float,
    ended_at: Optional[float],
) -> None:
    try:
        from core.sync_session import get_sync_session
        from core.database import TraceSpanRecord, gen_id

        with get_sync_session() as s:
            s.add(
                TraceSpanRecord(
                    id=gen_id(),
                    trace_id=trace_id,
                    span_id=span_id,
                    parent_span_id=parent_span_id,
                    name=name,
                    status=status,
                    attrs=attrs,
                    started_at=started_at,
                    ended_at=ended_at,
                )
            )
            s.commit()
    except Exception:
        pass  # observational — never block execution


@contextmanager
def start_span(
    name: str,
    attrs: Optional[dict] = None,
    *,
    kind: Optional[str] = None,
    attributes: Optional[dict] = None,
) -> Generator[TraceContext, None, None]:
    merged_attrs = dict(attrs or {})
    merged_attrs.update(attributes or {})
    if kind is not None:
        merged_attrs.setdefault("kind", kind)

    parent = get_current_trace()
    ctx = parent.child() if parent else new_trace()
    token = _trace_ctx.set(ctx)
    started = time.time()
    status = "ok"
    try:
        yield ctx
    except Exception:
        status = "error"
        raise
    finally:
        _persist_span(
            trace_id=ctx.trace_id,
            span_id=ctx.span_id,
            parent_span_id=ctx.parent_span_id,
            name=name,
            status=status,
            attrs=_sanitize_attrs(merged_attrs),
            started_at=started,
            ended_at=time.time(),
        )
        _trace_ctx.reset(token)


def end_span(*args, **kwargs) -> None:
    return


def get_trace_spans(trace_id: str, limit: int = 100) -> list[dict]:
    from core.sync_session import get_sync_session
    from core.database import TraceSpanRecord
    from sqlalchemy import select

    with get_sync_session() as s:
        rows = s.execute(
            select(TraceSpanRecord)
            .where(TraceSpanRecord.trace_id == trace_id)
            .limit(limit)
        ).scalars().all()
        return [
            {
                "trace_id": r.trace_id,
                "span_id": r.span_id,
                "name": r.name,
                "status": r.status,
                "attributes": __import__("json").dumps(r.attrs or {}),
            }
            for r in rows
        ]


def tracing_health() -> dict:
    from core.sync_session import store_backend

    return {
        "tracing_enabled": True,
        "backend": store_backend(),
        "role": "observational",
    }
