"""Durable voice sessions + unified transcript lines (text/voice/phone)."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Optional

_LOCK = threading.Lock()
_DB = Path("data/carai.sqlite3")

SESSION_STATUSES = (
    "CREATED", "CONNECTING", "LISTENING", "THINKING", "SPEAKING",
    "INTERRUPTED", "ENDING", "COMPLETED", "FAILED", "CANCELLED",
)


def _conn() -> sqlite3.Connection:
    _DB.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(_DB), check_same_thread=False)
    c.row_factory = sqlite3.Row
    return c


def init_carai_db() -> None:
    with _LOCK:
        c = _conn()
        try:
            c.executescript(
                """
                CREATE TABLE IF NOT EXISTS voice_sessions (
                  id TEXT PRIMARY KEY,
                  user_id TEXT NOT NULL,
                  project_id TEXT,
                  persona_id TEXT DEFAULT 'nuha',
                  provider TEXT,
                  status TEXT,
                  started_at REAL,
                  ended_at REAL,
                  trace_id TEXT,
                  metadata TEXT
                );
                CREATE TABLE IF NOT EXISTS transcript_lines (
                  id TEXT PRIMARY KEY,
                  session_id TEXT,
                  conversation_id TEXT,
                  user_id TEXT,
                  project_id TEXT,
                  persona_id TEXT,
                  channel TEXT,
                  speaker TEXT,
                  text TEXT,
                  sequence INTEGER,
                  ts REAL,
                  duration REAL,
                  confidence REAL,
                  mission_id TEXT,
                  tool_ref TEXT,
                  trace_id TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_tl_session ON transcript_lines(session_id, sequence);
                CREATE INDEX IF NOT EXISTS idx_tl_conv ON transcript_lines(conversation_id, sequence);
                """
            )
            c.commit()
        finally:
            c.close()


init_carai_db()


def create_voice_session(
    *,
    user_id: str,
    project_id: Optional[str] = None,
    persona_id: str = "nuha",
    provider: str = "browser_delegated",
    trace_id: Optional[str] = None,
    metadata: Optional[dict] = None,
) -> dict:
    sid = uuid.uuid4().hex
    now = time.time()
    with _LOCK:
        c = _conn()
        try:
            c.execute(
                """INSERT INTO voice_sessions
                   (id,user_id,project_id,persona_id,provider,status,started_at,ended_at,trace_id,metadata)
                   VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (sid, user_id, project_id, persona_id or "nuha", provider,
                 "CREATED", now, None, trace_id, json.dumps(metadata or {})),
            )
            c.commit()
        finally:
            c.close()
    try:
        from execution.outbox import enqueue
        enqueue("voice.session.started", aggregate_type="voice_session", aggregate_id=sid,
                payload={"persona_id": persona_id, "project_id": project_id},
                trace_id=trace_id, idempotency_key=f"voice.started:{sid}")
    except Exception:
        pass
    return get_voice_session(sid)


def get_voice_session(session_id: str) -> Optional[dict]:
    with _LOCK:
        c = _conn()
        try:
            r = c.execute("SELECT * FROM voice_sessions WHERE id=?", (session_id,)).fetchone()
            return dict(r) if r else None
        finally:
            c.close()


def update_voice_session(session_id: str, *, status: Optional[str] = None, metadata_patch: Optional[dict] = None) -> Optional[dict]:
    s = get_voice_session(session_id)
    if not s:
        return None
    if status:
        if status not in SESSION_STATUSES:
            raise ValueError(f"invalid status {status}")
        s["status"] = status
        if status in ("COMPLETED", "FAILED", "CANCELLED"):
            s["ended_at"] = time.time()
    if metadata_patch:
        meta = json.loads(s.get("metadata") or "{}")
        meta.update(metadata_patch)
        s["metadata"] = json.dumps(meta)
    with _LOCK:
        c = _conn()
        try:
            c.execute(
                "UPDATE voice_sessions SET status=?, ended_at=?, metadata=? WHERE id=?",
                (s["status"], s.get("ended_at"), s.get("metadata"), session_id),
            )
            c.commit()
        finally:
            c.close()
    if status in ("COMPLETED", "FAILED", "CANCELLED"):
        try:
            from execution.outbox import enqueue
            enqueue(f"voice.session.{status.lower()}", aggregate_type="voice_session",
                    aggregate_id=session_id, payload={"status": status},
                    trace_id=s.get("trace_id"),
                    idempotency_key=f"voice.{status}:{session_id}")
        except Exception:
            pass
    return get_voice_session(session_id)


def append_transcript(
    *,
    session_id: Optional[str] = None,
    conversation_id: Optional[str] = None,
    user_id: str,
    project_id: Optional[str] = None,
    persona_id: str = "nuha",
    channel: str = "text",  # text | voice | phone
    speaker: str = "user",
    text: str,
    sequence: Optional[int] = None,
    confidence: Optional[float] = None,
    duration: Optional[float] = None,
    mission_id: Optional[str] = None,
    tool_ref: Optional[str] = None,
    trace_id: Optional[str] = None,
) -> dict:
    # Redact obvious secrets
    safe = text or ""
    for needle in ("sk-", "Bearer ", "ghp_", "api_key"):
        if needle in safe:
            safe = "[REDACTED]"
            break
    lid = uuid.uuid4().hex
    ts = time.time()
    with _LOCK:
        c = _conn()
        try:
            if sequence is None:
                key = session_id or conversation_id or ""
                row = c.execute(
                    "SELECT MAX(sequence) AS m FROM transcript_lines WHERE session_id=? OR conversation_id=?",
                    (session_id or "", conversation_id or ""),
                ).fetchone()
                sequence = int(row["m"] or 0) + 1
            c.execute(
                """INSERT INTO transcript_lines
                   (id,session_id,conversation_id,user_id,project_id,persona_id,channel,speaker,text,
                    sequence,ts,duration,confidence,mission_id,tool_ref,trace_id)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (lid, session_id, conversation_id, user_id, project_id, persona_id, channel,
                 speaker, safe[:8000], sequence, ts, duration, confidence, mission_id, tool_ref, trace_id),
            )
            c.commit()
        finally:
            c.close()
    return {
        "id": lid, "session_id": session_id, "conversation_id": conversation_id,
        "channel": channel, "speaker": speaker, "text": safe[:8000], "sequence": sequence,
        "ts": ts, "confidence": confidence, "persona_id": persona_id, "tool_ref": tool_ref,
    }


def get_transcript(*, session_id: Optional[str] = None, conversation_id: Optional[str] = None) -> list[dict]:
    with _LOCK:
        c = _conn()
        try:
            if session_id:
                rows = c.execute(
                    "SELECT * FROM transcript_lines WHERE session_id=? ORDER BY sequence ASC",
                    (session_id,),
                ).fetchall()
            elif conversation_id:
                rows = c.execute(
                    "SELECT * FROM transcript_lines WHERE conversation_id=? ORDER BY sequence ASC",
                    (conversation_id,),
                ).fetchall()
            else:
                return []
            return [dict(r) for r in rows]
        finally:
            c.close()


def list_sessions(user_id: str, limit: int = 20) -> list[dict]:
    with _LOCK:
        c = _conn()
        try:
            rows = c.execute(
                "SELECT * FROM voice_sessions WHERE user_id=? ORDER BY started_at DESC LIMIT ?",
                (user_id, limit),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            c.close()
