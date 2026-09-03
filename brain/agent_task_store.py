"""
Durable AgentTask persistence + bounded event ring for reconnect.

AgentTask is orchestration/session state for the IDE coding agent.
It does NOT replace ExecutionJob. Evidence remains the audit system.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import select, desc

logger = logging.getLogger("devos.agent_task_store")

MAX_EVENTS_PER_TASK = 200


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def persist_task(task) -> bool:
    """Upsert AgentTask into SQLite. Best-effort; never raises into tool loop."""
    try:
        from core.database import AsyncSessionLocal, AgentTaskRecord

        async with AsyncSessionLocal() as db:
            row = await db.get(AgentTaskRecord, task.id)
            payload = {
                "user_id": task.user_id,
                "tenant_id": task.tenant_id,
                "project_id": task.project_id,
                "session_id": task.session_id,
                "objective": task.objective or "",
                "mode": task.mode.value if hasattr(task.mode, "value") else str(task.mode),
                "status": task.status.value if hasattr(task.status, "value") else str(task.status),
                "current_tool": task.current_tool,
                "files_changed": list(task.files_changed or []),
                "tools_used": list(task.tools_used or []),
                "correlation_id": task.correlation_id,
                "error": task.error,
                "summary": task.summary,
                "updated_at": _now(),
            }
            if row is None:
                row = AgentTaskRecord(
                    id=task.id,
                    events=[],
                    started_at=_parse_iso(task.started_at) or _now(),
                    created_at=_now(),
                    **payload,
                )
                db.add(row)
            else:
                for k, v in payload.items():
                    setattr(row, k, v)
                if task.completed_at:
                    row.completed_at = _parse_iso(task.completed_at)
            await db.commit()
        return True
    except Exception:
        logger.debug("persist_task failed", exc_info=True)
        return False


async def append_event(task_id: str, event: dict) -> bool:
    """Append event to durable ring buffer (bounded). Returns True if durable.

    Dedupes by seq: same seq for a task is not stored twice.
    """
    try:
        from core.database import AsyncSessionLocal, AgentTaskRecord

        async with AsyncSessionLocal() as db:
            row = await db.get(AgentTaskRecord, task_id)
            if row is None:
                return False
            events = list(row.events or [])
            seq = event.get("seq")
            if seq is not None:
                for e in events:
                    if e.get("seq") == seq:
                        return True  # already durable
            events.append(event)
            if len(events) > MAX_EVENTS_PER_TASK:
                events = events[-MAX_EVENTS_PER_TASK:]
            row.events = events
            row.updated_at = _now()
            await db.commit()
            return True
    except Exception:
        logger.debug("append_event failed", exc_info=True)
        return False


async def load_task(task_id: str) -> Optional[dict]:
    try:
        from core.database import AsyncSessionLocal, AgentTaskRecord

        async with AsyncSessionLocal() as db:
            row = await db.get(AgentTaskRecord, task_id)
            if row is None:
                return None
            return _row_to_dict(row)
    except Exception:
        logger.debug("load_task failed", exc_info=True)
        return None


async def load_task_events(task_id: str, after_seq: int = 0) -> list[dict]:
    """Return events with seq > after_seq for reconnect."""
    try:
        from core.database import AsyncSessionLocal, AgentTaskRecord

        async with AsyncSessionLocal() as db:
            row = await db.get(AgentTaskRecord, task_id)
            if row is None:
                return []
            events = list(row.events or [])
            if after_seq <= 0:
                return events
            return [e for e in events if int(e.get("seq") or 0) > after_seq]
    except Exception:
        logger.debug("load_task_events failed", exc_info=True)
        return []


async def list_user_tasks(user_id: str, limit: int = 20) -> list[dict]:
    try:
        from core.database import AsyncSessionLocal, AgentTaskRecord

        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(AgentTaskRecord)
                .where(AgentTaskRecord.user_id == user_id)
                .order_by(desc(AgentTaskRecord.created_at))
                .limit(limit)
            )
            rows = result.scalars().all()
            return [_row_to_dict(r, include_events=False) for r in rows]
    except Exception:
        logger.debug("list_user_tasks failed", exc_info=True)
        return []


def _parse_iso(val: Optional[str]) -> Optional[datetime]:
    if not val:
        return None
    try:
        return datetime.fromisoformat(val.replace("Z", "+00:00"))
    except Exception:
        return None


def _row_to_dict(row, include_events: bool = True) -> dict:
    d = {
        "id": row.id,
        "user_id": row.user_id,
        "tenant_id": row.tenant_id,
        "project_id": row.project_id,
        "session_id": row.session_id,
        "objective": row.objective,
        "mode": row.mode,
        "status": row.status,
        "current_tool": row.current_tool,
        "files_changed": row.files_changed or [],
        "tools_used": row.tools_used or [],
        "correlation_id": row.correlation_id,
        "error": row.error,
        "summary": row.summary,
        "provider": row.provider,
        "model": row.model,
        "started_at": row.started_at.isoformat() if row.started_at else None,
        "completed_at": row.completed_at.isoformat() if row.completed_at else None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }
    if include_events:
        d["events"] = row.events or []
    return d


async def persist_hai_checkpoint(task_id: str, checkpoint: dict) -> bool:
    """Best-effort durable HAI checkpoint on AgentTaskRecord."""
    try:
        from core.database import AsyncSessionLocal, AgentTaskRecord
        async with AsyncSessionLocal() as db:
            row = await db.get(AgentTaskRecord, task_id)
            if row is None:
                return False
            if hasattr(row, "hai_checkpoint"):
                row.hai_checkpoint = checkpoint
            else:
                # Fallback: store under events metadata marker is avoided; skip
                return False
            row.updated_at = _now()
            await db.commit()
            return True
    except Exception:
        logger.debug("persist_hai_checkpoint failed", exc_info=True)
        return False


async def load_hai_checkpoint(task_id: str):
    try:
        from core.database import AsyncSessionLocal, AgentTaskRecord
        async with AsyncSessionLocal() as db:
            row = await db.get(AgentTaskRecord, task_id)
            if row is None:
                return None
            cp = getattr(row, "hai_checkpoint", None)
            return dict(cp) if isinstance(cp, dict) else None
    except Exception:
        logger.debug("load_hai_checkpoint failed", exc_info=True)
        return None



async def claim_task_recovery(task_id: str, owner_id: str, lease_seconds: int = 60) -> bool:
    """Atomically claim exclusive recovery ownership.

    Same owner renews the lease. A different owner is denied while the lease
    is unexpired. Guarantee survives separate OS processes via conditional UPDATE.
    """
    try:
        from datetime import timedelta
        from sqlalchemy import text
        from core.database import AsyncSessionLocal, engine

        now = _now()
        expiry = now + timedelta(seconds=int(lease_seconds))
        # Conditional UPDATE — ownership decided by rowcount
        sql = text(
            """
            UPDATE agent_tasks
            SET recovery_owner = :owner,
                recovery_lease_expires_at = :expiry,
                updated_at = :now
            WHERE id = :task_id
              AND (
                   recovery_owner IS NULL
                   OR recovery_owner = :owner
                   OR recovery_lease_expires_at IS NULL
                   OR recovery_lease_expires_at <= :now
              )
            """
        )
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                sql,
                {
                    "owner": owner_id,
                    "expiry": expiry,
                    "now": now,
                    "task_id": task_id,
                },
            )
            await db.commit()
            # SQLAlchemy 2: rowcount on CursorResult
            rc = getattr(result, "rowcount", None)
            if rc is None:
                return False
            return int(rc) == 1
    except Exception:
        logger.debug("claim_task_recovery failed", exc_info=True)
        return False


async def release_task_recovery(task_id: str, owner_id: str) -> bool:
    try:
        from core.database import AsyncSessionLocal, AgentTaskRecord
        async with AsyncSessionLocal() as db:
            row = await db.get(AgentTaskRecord, task_id)
            if row is None:
                return False
            if getattr(row, "recovery_owner", None) != owner_id:
                return False
            row.recovery_owner = None
            row.recovery_lease_expires_at = None
            row.updated_at = _now()
            await db.commit()
            return True
    except Exception:
        logger.debug("release_task_recovery failed", exc_info=True)
        return False


async def load_task_identity(task_id: str) -> Optional[dict]:
    """Durable identity fields only (never from HAI checkpoint)."""
    try:
        from core.database import AsyncSessionLocal, AgentTaskRecord
        async with AsyncSessionLocal() as db:
            row = await db.get(AgentTaskRecord, task_id)
            if row is None:
                return None
            return {
                "id": row.id,
                "user_id": row.user_id,
                "tenant_id": row.tenant_id,
                "project_id": row.project_id,
                "session_id": row.session_id,
                "status": row.status,
                "correlation_id": row.correlation_id,
                "objective": row.objective,
            }
    except Exception:
        logger.debug("load_task_identity failed", exc_info=True)
        return None


async def mark_task_cancelled(task_id: str, user_id: str) -> dict:
    """Durable cancel for tasks not in memory. Does not execute tools.

    Returns status dict. Ownership enforced: user_id must match.
    Terminal states are left unchanged.
    """
    try:
        from core.database import AsyncSessionLocal, AgentTaskRecord
        async with AsyncSessionLocal() as db:
            row = await db.get(AgentTaskRecord, task_id)
            if row is None or row.user_id != user_id:
                return {"ok": False, "reason": "not_found"}
            status = (row.status or "").lower()
            terminal = {"succeeded", "failed", "cancelled", "blocked", "unknown"}
            if status in terminal:
                return {"ok": True, "task_id": task_id, "status": row.status, "already_terminal": True}
            row.status = "cancelled"
            row.updated_at = _now()
            # Append cancel event if possible
            events = list(row.events or [])
            last_seq = 0
            for e in events:
                try:
                    last_seq = max(last_seq, int(e.get("seq") or 0))
                except Exception:
                    pass
            events.append({
                "type": "agent.cancelled",
                "task_id": task_id,
                "seq": last_seq + 1,
                "timestamp": _now().isoformat(),
                "data": {"source": "durable_cancel"},
            })
            if len(events) > MAX_EVENTS_PER_TASK:
                events = events[-MAX_EVENTS_PER_TASK:]
            row.events = events
            await db.commit()
            return {"ok": True, "task_id": task_id, "status": "cancelled", "already_terminal": False}
    except Exception:
        logger.exception("mark_task_cancelled failed")
        return {"ok": False, "reason": "error"}
