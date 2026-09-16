"""Governance audit log — Postgres SoT."""
from __future__ import annotations

import enum
import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger("devos.audit")


class AuditEventType(str, enum.Enum):
    AUTH = "auth"
    CAPABILITY = "capability"
    CAPABILITY_INVOKE = "capability.invoke"
    RBAC_CHECK = "rbac.check"
    EXECUTION = "execution"
    GOVERNANCE = "governance"
    AGENT = "agent"
    SYSTEM = "system"
    OTHER = "other"


@dataclass
class AuditEntry:
    event_type: Any = ""
    actor_id: str = ""
    tenant_id: str = ""
    action: str = ""
    resource: str = ""
    resource_id: str = ""
    result: str = ""
    details: dict = field(default_factory=dict)
    # Extended fields used by tests / enterprise API
    event_id: str = ""
    timestamp: Optional[datetime] = None
    target: str = ""
    outcome: str = ""
    trace_id: str = ""

    def to_dict(self) -> dict:
        et = self.event_type
        if isinstance(et, AuditEventType):
            et = et.value
        return {
            "event_id": self.event_id,
            "event_type": et,
            "actor_id": self.actor_id,
            "tenant_id": self.tenant_id,
            "action": self.action or self.target,
            "target": self.target or self.resource,
            "resource": self.resource,
            "resource_id": self.resource_id,
            "result": self.result or self.outcome,
            "outcome": self.outcome or self.result,
            "details": self.details or {},
            "trace_id": self.trace_id,
            "timestamp": (
                self.timestamp.isoformat()
                if isinstance(self.timestamp, datetime)
                else self.timestamp
            ),
        }


class AuditLogger:
    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def log(
        self,
        event_type: Any,
        actor_id: str,
        tenant_id: str = "",
        action: str = "",
        resource: str = "",
        resource_id: str = "",
        mission_id: str = "",
        task_id: str = "",
        result: str = "",
        evidence: str = "",
        details: Optional[dict] = None,
        user_id: Optional[str] = None,
        actor_type: str = "user",
        # Aliases used by older/tests call sites
        target: str = "",
        outcome: str = "",
        trace_id: str = "",
        **_extra,
    ) -> str:
        from core.sync_session import get_sync_session
        from core.database import AuditLogRecord, gen_id, utcnow_naive

        et = event_type.value if isinstance(event_type, AuditEventType) else str(event_type)
        action_s = action or target or ""
        result_s = result or outcome or ""
        resource_s = resource or target or ""
        det = dict(details or {})
        if trace_id:
            det.setdefault("trace_id", trace_id)
        eid = gen_id()
        with get_sync_session() as s:
            s.add(
                AuditLogRecord(
                    id=eid,
                    event_type=et,
                    actor_id=actor_id,
                    actor_type=actor_type,
                    tenant_id=tenant_id or None,
                    user_id=user_id or actor_id,
                    action=action_s,
                    resource=resource_s,
                    resource_id=resource_id or None,
                    mission_id=mission_id or None,
                    task_id=task_id or None,
                    result=result_s,
                    evidence=evidence or None,
                    details=det,
                    created_at=utcnow_naive(),
                )
            )
            s.commit()
        return eid

    def query(
        self,
        actor_id: Optional[str] = None,
        tenant_id: Optional[str] = None,
        user_id: Optional[str] = None,
        event_type: Optional[Any] = None,
        offset: int = 0,
        limit: int = 100,
    ) -> list[dict]:
        from core.sync_session import get_sync_session
        from core.database import AuditLogRecord
        from sqlalchemy import select, desc

        with get_sync_session() as s:
            stmt = select(AuditLogRecord).order_by(desc(AuditLogRecord.created_at))
            if user_id:
                stmt = stmt.where(AuditLogRecord.user_id == user_id)
            if actor_id:
                stmt = stmt.where(AuditLogRecord.actor_id == actor_id)
            if tenant_id:
                stmt = stmt.where(AuditLogRecord.tenant_id == tenant_id)
            if event_type is not None:
                et = event_type.value if isinstance(event_type, AuditEventType) else str(event_type)
                stmt = stmt.where(AuditLogRecord.event_type == et)
            if offset:
                stmt = stmt.offset(int(offset))
            stmt = stmt.limit(limit)
            rows = s.execute(stmt).scalars().all()
            out = []
            for r in rows:
                det = dict(r.details or {})
                out.append(
                    {
                        "id": r.id,
                        "event_id": r.id,
                        "event_type": r.event_type,
                        "actor_id": r.actor_id,
                        "user_id": r.user_id,
                        "tenant_id": r.tenant_id,
                        "action": r.action,
                        "resource": r.resource,
                        "result": r.result,
                        "details": det,
                        "trace_id": det.get("trace_id", ""),
                        "created_at": r.created_at.isoformat() if r.created_at else None,
                    }
                )
            return out

    def stats(self, tenant_id: Optional[str] = None) -> dict:
        rows = self.query(tenant_id=tenant_id, limit=5000)
        by_type: dict[str, int] = {}
        for r in rows:
            et = r.get("event_type") or "other"
            by_type[et] = by_type.get(et, 0) + 1
        return {
            "count": len(rows),
            "total_entries": len(rows),
            "by_type": by_type,
        }

    def close(self):
        pass


def get_audit_logger() -> AuditLogger:
    return AuditLogger()
