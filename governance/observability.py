"""Governance observability — Postgres-backed tool/trace records (not authority)."""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Optional


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


class ObservabilityStore:
    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def record_tool(self, rec: ToolCallRecord) -> None:
        try:
            from observability.tracing import start_span

            with start_span("tool." + rec.tool, {"user_id": rec.user_id, "status": rec.status}):
                pass
        except Exception:
            pass

    def record_trace(self, rec: TraceRecord) -> None:
        try:
            from observability.tracing import start_span

            with start_span(rec.name, {"user_id": rec.user_id, **(rec.meta or {})}):
                pass
        except Exception:
            pass

    @property
    def backend(self) -> str:
        from core.sync_session import store_backend

        return store_backend()
