"""Governance observability — operational traces/errors (NOT authority).

Separation of concerns:
  AuditLogger  → authorization/governance audit trail (who was allowed to do what)
  ObservabilityStore → operational telemetry (tool calls, loop traces, HTTP errors)

Durable state uses Postgres (Supabase) via the sync session. This module is
observational only: failures here must never block execution or grant authority.
"""
from __future__ import annotations

import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("devos.observability")

# Legacy test hook (not production authority). Tests may point this at a temp path;
# production always uses the Postgres-backed models below.
OBS_DB: Path = Path("data/observability.db")


@dataclass
class ToolCallRecord:
    tool: str
    user_id: str = ""
    status: str = "ok"
    duration_ms: float = 0
    meta: dict = field(default_factory=dict)


@dataclass
class TraceRecord:
    name: str
    user_id: str = ""
    meta: dict = field(default_factory=dict)


def _new_id() -> str:
    return str(uuid.uuid4())


def _redact(text: Optional[str], limit: int = 500) -> str:
    if not text:
        return ""
    s = str(text)
    lower = s.lower()
    for marker in ("api_key", "authorization", "bearer ", "password=", "secret=", "token="):
        if marker in lower:
            return "[REDACTED]"
    return s[:limit]


class ObservabilityStore:
    """Singleton operational telemetry store (Postgres SoT)."""

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    # ── Legacy / simple adapters ──────────────────────────────────────────

    def record_tool(self, rec: ToolCallRecord) -> None:
        try:
            from observability.tracing import start_span

            with start_span(
                "tool." + rec.tool,
                {"user_id": rec.user_id, "status": rec.status, "duration_ms": rec.duration_ms},
            ):
                pass
        except Exception:
            logger.debug("record_tool failed", exc_info=True)

    def record_trace(self, rec: TraceRecord) -> None:
        try:
            from observability.tracing import start_span

            with start_span(rec.name, {"user_id": rec.user_id, **(rec.meta or {})}):
                pass
        except Exception:
            logger.debug("record_trace failed", exc_info=True)

    @property
    def backend(self) -> str:
        try:
            from core.sync_session import store_backend

            return store_backend()
        except Exception:
            return "unknown"

    # ── Errors (HTTP middleware + ops) ────────────────────────────────────

    def record_error(
        self,
        component: str,
        message: str,
        *,
        trace_id: Optional[str] = None,
        user_id: Optional[str] = None,
        status_code: Optional[int] = None,
        **meta: Any,
    ) -> dict:
        """Record an operational error. Returns the stored entry dict."""
        entry = {
            "id": _new_id(),
            "component": component,
            "message": _redact(message, 2000),
            "trace_id": trace_id or "",
            "user_id": user_id or "",
            "status_code": status_code,
            "created_at": time.time(),
            "meta": {k: v for k, v in meta.items() if k not in ("password", "token", "secret")},
        }
        try:
            from core.sync_session import get_sync_session
            from core.database import ObservabilityErrorRecord

            with get_sync_session() as s:
                s.add(
                    ObservabilityErrorRecord(
                        id=entry["id"],
                        component=entry["component"],
                        message=entry["message"],
                        trace_id=entry["trace_id"] or None,
                        user_id=entry["user_id"] or None,
                        status_code=status_code,
                        meta=entry["meta"],
                        created_at=entry["created_at"],
                    )
                )
                s.commit()
        except Exception:
            logger.debug("record_error persist failed", exc_info=True)
        return entry

    def list_errors(
        self,
        limit: int = 50,
        *,
        user_id: Optional[str] = None,
        component: Optional[str] = None,
    ) -> list[dict]:
        try:
            from core.sync_session import get_sync_session
            from core.database import ObservabilityErrorRecord
            from sqlalchemy import select, desc

            with get_sync_session() as s:
                stmt = select(ObservabilityErrorRecord).order_by(
                    desc(ObservabilityErrorRecord.created_at)
                )
                if user_id:
                    stmt = stmt.where(ObservabilityErrorRecord.user_id == user_id)
                if component:
                    stmt = stmt.where(ObservabilityErrorRecord.component == component)
                stmt = stmt.limit(limit)
                rows = s.execute(stmt).scalars().all()
                return [
                    {
                        "id": r.id,
                        "component": r.component,
                        "message": r.message,
                        "trace_id": r.trace_id or "",
                        "user_id": r.user_id or "",
                        "status_code": r.status_code,
                        "created_at": r.created_at,
                        "meta": r.meta or {},
                    }
                    for r in rows
                ]
        except Exception:
            logger.debug("list_errors failed", exc_info=True)
            return []

    # ── Loop traces (BrainExecutionLoop) ──────────────────────────────────

    def start_trace(
        self,
        task_id: str,
        agent_id: str,
        session_id: str,
        goal: str,
        provider: str = "",
        model: str = "",
        user_id: str = "",
    ) -> None:
        """Open a durable loop/task trace. task_id is the correlation key."""
        try:
            from core.sync_session import get_sync_session
            from core.database import ObservabilityTraceRecord

            now = time.time()
            with get_sync_session() as s:
                row = s.get(ObservabilityTraceRecord, task_id)
                if row is None:
                    s.add(
                        ObservabilityTraceRecord(
                            id=task_id,
                            agent_id=agent_id,
                            session_id=session_id,
                            user_id=user_id or None,
                            goal=_redact(goal, 1000),
                            provider=provider,
                            model=model,
                            status="running",
                            meta={},
                            started_at=now,
                            ended_at=None,
                        )
                    )
                else:
                    row.status = "running"
                    row.agent_id = agent_id
                    row.session_id = session_id
                    row.goal = _redact(goal, 1000)
                    row.provider = provider
                    row.model = model
                    row.started_at = now
                    row.ended_at = None
                s.commit()
            # Also open a root span for span-level queries
            from observability.tracing import start_span, set_current_trace, new_trace

            ctx = new_trace()
            # bind task_id into span attrs via a short-lived root marker
            from observability.tracing import _persist_span

            _persist_span(
                trace_id=task_id,
                span_id=ctx.span_id,
                parent_span_id=None,
                name="loop.start",
                status="ok",
                attrs={
                    "agent_id": agent_id,
                    "session_id": session_id,
                    "provider": provider,
                    "model": model,
                },
                started_at=now,
                ended_at=now,
            )
        except Exception:
            logger.debug("start_trace failed", exc_info=True)

    def finish_trace(
        self,
        task_id: str,
        *,
        status: str = "complete",
        decision: str = "",
        iterations: int = 0,
        total_tokens: int = 0,
        total_latency_ms: float = 0,
        tool_calls: int = 0,
        final_answer: str = "",
        **extra: Any,
    ) -> None:
        try:
            from core.sync_session import get_sync_session
            from core.database import ObservabilityTraceRecord

            now = time.time()
            with get_sync_session() as s:
                row = s.get(ObservabilityTraceRecord, task_id)
                meta = {
                    "decision": decision,
                    "iterations": iterations,
                    "total_tokens": total_tokens,
                    "total_latency_ms": total_latency_ms,
                    "tool_calls": tool_calls,
                    "final_answer": _redact(final_answer, 500),
                    **{k: v for k, v in extra.items() if "secret" not in k.lower()},
                }
                if row is None:
                    s.add(
                        ObservabilityTraceRecord(
                            id=task_id,
                            status=status,
                            meta=meta,
                            started_at=now,
                            ended_at=now,
                        )
                    )
                else:
                    row.status = status
                    row.meta = {**(row.meta or {}), **meta}
                    row.ended_at = now
                s.commit()
        except Exception:
            logger.debug("finish_trace failed", exc_info=True)

    def record_tool_call(
        self,
        task_id: str,
        agent_id: str,
        session_id: str,
        tool: str,
        tool_input: Any = None,
        tool_output: Any = None,
        status: str = "success",
        ucip_decision: Any = None,
        latency_ms: float = 0,
        *,
        error_type: Optional[str] = None,
        iteration: int = 0,
        user_id: str = "",
    ) -> None:
        """Record a governed tool invocation under a loop/task trace."""
        try:
            from observability.tracing import _persist_span

            _persist_span(
                trace_id=task_id,
                span_id=_new_id(),
                parent_span_id=None,
                name=f"tool.{tool}",
                status="error" if status not in ("success", "ok", "completed") else "ok",
                attrs={
                    "agent_id": agent_id,
                    "session_id": session_id,
                    "tool": tool,
                    "input": _redact(str(tool_input), 300),
                    "output": _redact(str(tool_output), 400),
                    "status": status,
                    "ucip_decision": str(ucip_decision) if ucip_decision is not None else "",
                    "latency_ms": latency_ms,
                    "error_type": error_type,
                    "iteration": iteration,
                    "user_id": user_id,
                },
                started_at=time.time() - (float(latency_ms or 0) / 1000.0),
                ended_at=time.time(),
            )
        except Exception:
            logger.debug("record_tool_call failed", exc_info=True)

    def list_traces(
        self,
        task_id: Optional[str] = None,
        limit: int = 50,
        *,
        user_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> list[dict]:
        try:
            from core.sync_session import get_sync_session
            from core.database import ObservabilityTraceRecord
            from sqlalchemy import select, desc

            with get_sync_session() as s:
                stmt = select(ObservabilityTraceRecord).order_by(
                    desc(ObservabilityTraceRecord.started_at)
                )
                if task_id:
                    stmt = stmt.where(ObservabilityTraceRecord.id == task_id)
                if user_id:
                    stmt = stmt.where(ObservabilityTraceRecord.user_id == user_id)
                if session_id:
                    stmt = stmt.where(ObservabilityTraceRecord.session_id == session_id)
                stmt = stmt.limit(limit)
                rows = s.execute(stmt).scalars().all()
                return [
                    {
                        "trace_id": r.id,
                        "task_id": r.id,
                        "agent_id": r.agent_id,
                        "session_id": r.session_id,
                        "user_id": r.user_id,
                        "goal": r.goal,
                        "provider": r.provider,
                        "model": r.model,
                        "status": r.status,
                        "meta": r.meta or {},
                        "started_at": r.started_at,
                        "ended_at": r.ended_at,
                    }
                    for r in rows
                ]
        except Exception:
            logger.debug("list_traces failed", exc_info=True)
            return []

    def replay_trace(self, trace_id: str) -> dict:
        """Return trace summary + spans for a task/trace id."""
        traces = self.list_traces(task_id=trace_id, limit=1)
        summary = traces[0] if traces else {"trace_id": trace_id, "status": "unknown"}
        spans: list[dict] = []
        try:
            from observability.tracing import get_trace_spans

            spans = get_trace_spans(trace_id, limit=200)
        except Exception:
            logger.debug("replay_trace spans failed", exc_info=True)
        return {"trace": summary, "spans": spans, "trace_id": trace_id}

    def metrics(self) -> dict:
        """Aggregate loop-level metrics for /api/governance/metrics."""
        try:
            rows = self.list_traces(limit=500)
            by_status: dict[str, int] = {}
            total_tokens = 0
            total_tools = 0
            for r in rows:
                st = r.get("status") or "unknown"
                by_status[st] = by_status.get(st, 0) + 1
                meta = r.get("meta") or {}
                total_tokens += int(meta.get("total_tokens") or 0)
                total_tools += int(meta.get("tool_calls") or 0)
            return {
                "trace_count": len(rows),
                "by_status": by_status,
                "total_tokens": total_tokens,
                "total_tool_calls": total_tools,
                "backend": self.backend,
            }
        except Exception:
            return {"trace_count": 0, "by_status": {}, "backend": self.backend}
