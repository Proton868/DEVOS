"""Transactional outbox — Postgres SoT (no SQLite)."""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone, timedelta
from typing import Callable, Optional

logger = logging.getLogger("devos.outbox")
_HANDLERS: dict[str, Callable] = {}


def _backend() -> str:
    from core.sync_session import store_backend
    return store_backend()


def init_outbox() -> None:
    """Tables created via SQLAlchemy metadata / migrations."""
    return


def enqueue(
    event_type: str,
    payload: dict,
    *,
    aggregate_id: Optional[str] = None,
    user_id: Optional[str] = None,
) -> str:
    from core.sync_session import get_sync_session
    from core.database import OutboxEvent, gen_id, utcnow_naive

    eid = gen_id()
    now = utcnow_naive()
    with get_sync_session() as s:
        s.add(
            OutboxEvent(
                id=eid,
                event_type=event_type,
                aggregate_id=aggregate_id,
                user_id=user_id,
                payload=payload or {},
                status="pending",
                attempts=0,
                available_at=now,
                created_at=now,
                updated_at=now,
            )
        )
        s.commit()
    return eid


def claim_pending(limit: int = 20) -> list[dict]:
    from core.sync_session import get_sync_session
    from core.database import OutboxEvent, utcnow_naive
    from sqlalchemy import select

    now = utcnow_naive()
    with get_sync_session() as s:
        rows = (
            s.execute(
                select(OutboxEvent)
                .where(
                    OutboxEvent.status == "pending",
                    (OutboxEvent.available_at == None) | (OutboxEvent.available_at <= now),  # noqa: E711
                )
                .order_by(OutboxEvent.created_at)
                .limit(limit)
            )
        ).scalars().all()
        out = []
        for r in rows:
            r.status = "processing"
            r.attempts = int(r.attempts or 0) + 1
            r.updated_at = now
            out.append(
                {
                    "id": r.id,
                    "event_type": r.event_type,
                    "aggregate_id": r.aggregate_id,
                    "user_id": r.user_id,
                    "payload": r.payload or {},
                    "attempts": r.attempts,
                }
            )
        s.commit()
        return out


def mark_delivered(event_id: str) -> None:
    from core.sync_session import get_sync_session
    from core.database import OutboxEvent, utcnow_naive

    with get_sync_session() as s:
        r = s.get(OutboxEvent, event_id)
        if r:
            r.status = "delivered"
            r.updated_at = utcnow_naive()
            s.commit()


def mark_failed(event_id: str, error: str, *, backoff_sec: float = 5.0) -> None:
    from core.sync_session import get_sync_session
    from core.database import OutboxEvent, utcnow_naive

    with get_sync_session() as s:
        r = s.get(OutboxEvent, event_id)
        if r:
            r.status = "pending"
            r.last_error = (error or "")[:2000]
            r.available_at = utcnow_naive() + timedelta(seconds=backoff_sec)
            r.updated_at = utcnow_naive()
            s.commit()


def register_handler(event_type: str, fn: Callable[[dict], None]) -> None:
    _HANDLERS[event_type] = fn


def dispatch_once(limit: int = 20) -> dict:
    claimed = claim_pending(limit=limit)
    delivered = failed = 0
    for ev in claimed:
        fn = _HANDLERS.get(ev["event_type"])
        try:
            if fn:
                fn(ev)
            mark_delivered(ev["id"])
            delivered += 1
        except Exception as e:
            mark_failed(ev["id"], str(e))
            failed += 1
    return {"claimed": len(claimed), "delivered": delivered, "failed": failed}


def list_events(*, aggregate_id: Optional[str] = None, user_id: Optional[str] = None, limit: int = 50) -> list[dict]:
    from core.sync_session import get_sync_session
    from core.database import OutboxEvent
    from sqlalchemy import select

    with get_sync_session() as s:
        stmt = select(OutboxEvent).order_by(OutboxEvent.created_at.desc()).limit(limit)
        if aggregate_id:
            stmt = stmt.where(OutboxEvent.aggregate_id == aggregate_id)
        if user_id:
            stmt = stmt.where(OutboxEvent.user_id == user_id)
        rows = s.execute(stmt).scalars().all()
        return [
            {
                "id": r.id,
                "event_type": r.event_type,
                "aggregate_id": r.aggregate_id,
                "user_id": r.user_id,
                "status": r.status,
                "payload": r.payload,
            }
            for r in rows
        ]
