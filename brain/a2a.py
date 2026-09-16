"""
A2A-compatible message envelope and durable bus for DevOS.

Not a second runtime. Messages are the observable delegation boundary.
Persistence: agent_messages table (Postgres/Supabase SoT) with in-process
fallback ring only when the DB is unavailable (tests / degraded mode).

Supported message_type values:
  delegate | complete | fail | correct | ponytail_request | ponytail_result | notify
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger("devos.a2a")

# In-process fallback for environments without DB (still observable in-process)
_FALLBACK_BUS: list[dict] = []


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class A2AEnvelope:
    message_id: str
    mission_id: str
    task_id: str
    sender_type: str  # nuha | agent | ponytail | human | system
    sender_id: str
    recipient_agent_id: str
    message_type: str
    objective: str = ""
    constraints: list = field(default_factory=list)
    requested_capabilities: list = field(default_factory=list)
    context_refs: list = field(default_factory=list)
    artifact_refs: list = field(default_factory=list)
    parent_message_id: Optional[str] = None
    status: str = "queued"  # queued|delivered|processing|completed|failed|rejected
    evidence_refs: list = field(default_factory=list)
    payload: dict = field(default_factory=dict)
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def create(
        cls,
        *,
        mission_id: str,
        task_id: str,
        sender_type: str,
        sender_id: str,
        recipient_agent_id: str,
        message_type: str,
        objective: str = "",
        constraints: Optional[list] = None,
        requested_capabilities: Optional[list] = None,
        context_refs: Optional[list] = None,
        artifact_refs: Optional[list] = None,
        parent_message_id: Optional[str] = None,
        evidence_refs: Optional[list] = None,
        payload: Optional[dict] = None,
        status: str = "queued",
    ) -> "A2AEnvelope":
        return cls(
            message_id=str(uuid.uuid4()),
            mission_id=mission_id,
            task_id=task_id,
            sender_type=sender_type,
            sender_id=sender_id,
            recipient_agent_id=recipient_agent_id,
            message_type=message_type,
            objective=objective or "",
            constraints=list(constraints or []),
            requested_capabilities=list(requested_capabilities or []),
            context_refs=list(context_refs or []),
            artifact_refs=list(artifact_refs or []),
            parent_message_id=parent_message_id,
            status=status,
            evidence_refs=list(evidence_refs or []),
            payload=dict(payload or {}),
        )


async def persist_message(env: A2AEnvelope) -> str:
    """Write envelope to agent_messages (SoT). Idempotent on message_id.

    Retries of the same logical delivery reuse the same message_id and do not
    insert a second row when the primary key already exists.
    """
    env.updated_at = _now()
    try:
        from core.database import AsyncSessionLocal, AgentMessage

        async with AsyncSessionLocal() as db:
            existing = await db.get(AgentMessage, env.message_id)
            if existing is not None:
                # Do not reset terminal statuses on retry
                if (existing.status or "").lower() not in (
                    "completed", "failed", "accepted", "rejected"
                ):
                    existing.envelope = env.to_dict()
                    existing.status = env.status
                    await db.commit()
                return env.message_id
            row = AgentMessage(
                id=env.message_id,
                mission_id=env.mission_id,
                task_id=env.task_id,
                from_agent_id=f"{env.sender_type}:{env.sender_id}",
                to_agent_id=env.recipient_agent_id,
                envelope=env.to_dict(),
                status=env.status,
            )
            db.add(row)
            await db.commit()
        return env.message_id
    except Exception as e:
        logger.warning("a2a persist fallback: %s", e)
        _FALLBACK_BUS.append(env.to_dict())
        return env.message_id


async def update_message_status(message_id: str, status: str, **extra) -> None:
    try:
        from core.database import AsyncSessionLocal, AgentMessage
        from sqlalchemy import select

        async with AsyncSessionLocal() as db:
            row = await db.get(AgentMessage, message_id)
            if row is None:
                return
            env = dict(row.envelope or {})
            env["status"] = status
            env["updated_at"] = _now()
            env.update({k: v for k, v in extra.items() if v is not None})
            row.envelope = env
            row.status = status
            if status in ("completed", "failed", "delivered"):
                from core.database import utcnow_naive
                row.delivered_at = utcnow_naive()
            await db.commit()
    except Exception:
        for m in _FALLBACK_BUS:
            if m.get("message_id") == message_id:
                m["status"] = status
                m["updated_at"] = _now()
                m.update(extra)


async def list_messages(
    *,
    mission_id: Optional[str] = None,
    task_id: Optional[str] = None,
    limit: int = 100,
) -> list[dict]:
    try:
        from core.database import AsyncSessionLocal, AgentMessage
        from sqlalchemy import select

        async with AsyncSessionLocal() as db:
            q = select(AgentMessage)
            if mission_id:
                q = q.where(AgentMessage.mission_id == mission_id)
            if task_id:
                q = q.where(AgentMessage.task_id == task_id)
            q = q.limit(limit)
            rows = (await db.execute(q)).scalars().all()
            out = []
            for r in rows:
                d = dict(r.envelope or {})
                d.setdefault("message_id", r.id)
                d.setdefault("status", r.status)
                out.append(d)
            return out
    except Exception:
        items = list(_FALLBACK_BUS)
        if mission_id:
            items = [m for m in items if m.get("mission_id") == mission_id]
        if task_id:
            items = [m for m in items if m.get("task_id") == task_id]
        return items[-limit:]


def clear_fallback_bus() -> None:
    _FALLBACK_BUS.clear()
