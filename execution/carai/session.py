"""CARAI voice sessions — Postgres SoT."""
from __future__ import annotations

import time
import uuid
from typing import Optional


def init_carai_db() -> None:
    return


def create_voice_session(user_id: str, **meta) -> dict:
    from core.sync_session import get_sync_session
    from core.database import CaraiVoiceSession, gen_id

    sid = gen_id()
    with get_sync_session() as s:
        s.add(
            CaraiVoiceSession(
                id=sid,
                user_id=user_id,
                status="active",
                meta=meta or {},
                transcript=[],
                created_at=time.time(),
                updated_at=time.time(),
            )
        )
        s.commit()
    return {"id": sid, "user_id": user_id}


def get_voice_session(session_id: str) -> Optional[dict]:
    from core.sync_session import get_sync_session
    from core.database import CaraiVoiceSession

    with get_sync_session() as s:
        r = s.get(CaraiVoiceSession, session_id)
        if not r:
            return None
        return {
            "id": r.id,
            "user_id": r.user_id,
            "status": r.status,
            "meta": r.meta,
            "transcript": r.transcript,
        }


def update_voice_session(session_id: str, user_id: str, **fields) -> bool:
    from core.sync_session import get_sync_session
    from core.database import CaraiVoiceSession

    with get_sync_session() as s:
        r = s.get(CaraiVoiceSession, session_id)
        if not r or r.user_id != user_id:
            return False
        if "status" in fields:
            r.status = fields["status"]
        meta = dict(r.meta or {})
        for k, v in fields.items():
            if k != "status":
                meta[k] = v
        r.meta = meta
        r.updated_at = time.time()
        s.commit()
        return True


def append_transcript(session_id: str, user_id: str, entry: dict) -> bool:
    from core.sync_session import get_sync_session
    from core.database import CaraiVoiceSession

    with get_sync_session() as s:
        r = s.get(CaraiVoiceSession, session_id)
        if not r or r.user_id != user_id:
            return False
        tr = list(r.transcript or [])
        tr.append(entry)
        r.transcript = tr
        r.updated_at = time.time()
        s.commit()
        return True


def get_transcript(session_id: str, user_id: str) -> list:
    sess = get_voice_session(session_id)
    if not sess or sess.get("user_id") != user_id:
        return []
    return list(sess.get("transcript") or [])


def list_sessions(user_id: str) -> list[dict]:
    from core.sync_session import get_sync_session
    from core.database import CaraiVoiceSession
    from sqlalchemy import select

    with get_sync_session() as s:
        rows = s.execute(
            select(CaraiVoiceSession).where(CaraiVoiceSession.user_id == user_id)
        ).scalars().all()
        return [{"id": r.id, "user_id": r.user_id, "status": r.status} for r in rows]
