"""Governance audit log — Postgres SoT."""
from __future__ import annotations

import enum
import logging
import threading
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger("devos.audit")


class AuditEventType(str, enum.Enum):
    AUTH = "auth"
    CAPABILITY = "capability"
    EXECUTION = "execution"
    GOVERNANCE = "governance"
    AGENT = "agent"
    SYSTEM = "system"
    OTHER = "other"


@dataclass
class AuditEntry:
    event_type: str
    actor_id: str
    tenant_id: str = ""
    action: str = ""
    resource: str = ""
    resource_id: str = ""
    result: str = ""
    details: dict = field(default_factory=dict)


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
        event_type: AuditEventType | str,
        actor_id: str,
        tenant_id: str = "",
        *,
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
    ) -> str:
        from core.sync_session import get_sync_session
        from core.database import AuditLogRecord, gen_id, utcnow_naive

        et = event_type.value if isinstance(event_type, AuditEventType) else str(event_type)
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
                    action=action,
                    resource=resource,
                    resource_id=resource_id or None,
                    mission_id=mission_id or None,
                    task_id=task_id or None,
                    result=result,
                    evidence=evidence or None,
                    details=details or {},
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
        limit: int = 100,
    ) -> list[dict]:
        from core.sync_session import get_sync_session
        from core.database import AuditLogRecord
        from sqlalchemy import select, desc

        with get_sync_session() as s:
            stmt = select(AuditLogRecord).order_by(desc(AuditLogRecord.created_at)).limit(limit)
            if user_id:
                stmt = stmt.where(AuditLogRecord.user_id == user_id)
            if actor_id:
                stmt = stmt.where(AuditLogRecord.actor_id == actor_id)
            if tenant_id:
                stmt = stmt.where(AuditLogRecord.tenant_id == tenant_id)
            rows = s.execute(stmt).scalars().all()
            return [
                {
                    "id": r.id,
                    "event_type": r.event_type,
                    "actor_id": r.actor_id,
                    "user_id": r.user_id,
                    "action": r.action,
                    "resource": r.resource,
                    "result": r.result,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                }
                for r in rows
            ]

    def stats(self, tenant_id: Optional[str] = None) -> dict:
        rows = self.query(tenant_id=tenant_id, limit=1000)
        return {"count": len(rows)}

    def close(self):
        pass


def get_audit_logger() -> AuditLogger:
    return AuditLogger()
