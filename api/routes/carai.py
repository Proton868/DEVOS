"""Carai voice sessions + transcripts. Voice is an interface — not an authority."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from api.deps import get_current_user, get_db, ensure_personal_tenant
from execution.carai.session import (
    create_voice_session, get_voice_session, update_voice_session,
    append_transcript, get_transcript, list_sessions,
)
from execution.carai.provider import get_voice_provider
from observability.tracing import start_span, new_trace, set_current_trace

router = APIRouter(prefix="/api/carai", tags=["carai"])


class SessionCreate(BaseModel):
    project_id: Optional[str] = None
    persona_id: str = "nuha"


class StatusUpdate(BaseModel):
    status: str


class TranscriptLine(BaseModel):
    session_id: Optional[str] = None
    conversation_id: Optional[str] = None
    project_id: Optional[str] = None
    persona_id: str = "nuha"
    channel: str = "voice"
    speaker: str = "user"
    text: str = Field(..., min_length=1, max_length=8000)
    confidence: Optional[float] = None
    mission_id: Optional[str] = None
    tool_ref: Optional[str] = None


@router.get("/health")
async def carai_health(request: Request, db=Depends(get_db)):
    await get_current_user(request, db)
    return get_voice_provider().health()


@router.post("/sessions")
async def create_session(body: SessionCreate, request: Request, db=Depends(get_db)):
    user = await get_current_user(request, db)
    await ensure_personal_tenant(db, user)
    trace = new_trace()
    set_current_trace(trace)
    with start_span("voice.session.create", kind="voice", attributes={"persona_id": body.persona_id}):
        s = create_voice_session(
            user_id=str(user.id),
            project_id=body.project_id,
            persona_id=body.persona_id or "nuha",
            provider=get_voice_provider().name,
            trace_id=trace.trace_id,
        )
    return s


@router.get("/sessions")
async def sessions_list(request: Request, db=Depends(get_db)):
    user = await get_current_user(request, db)
    return {"sessions": list_sessions(str(user.id))}


@router.get("/sessions/{session_id}")
async def session_get(session_id: str, request: Request, db=Depends(get_db)):
    user = await get_current_user(request, db)
    s = get_voice_session(session_id)
    if not s or s.get("user_id") != str(user.id):
        raise HTTPException(404, "session not found")
    return s


@router.post("/sessions/{session_id}/status")
async def session_status(session_id: str, body: StatusUpdate, request: Request, db=Depends(get_db)):
    user = await get_current_user(request, db)
    s = get_voice_session(session_id)
    if not s or s.get("user_id") != str(user.id):
        raise HTTPException(404, "session not found")
    try:
        return update_voice_session(session_id, status=body.status)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/transcript")
async def transcript_append(body: TranscriptLine, request: Request, db=Depends(get_db)):
    user = await get_current_user(request, db)
    if body.session_id:
        s = get_voice_session(body.session_id)
        if not s or s.get("user_id") != str(user.id):
            raise HTTPException(404, "session not found")
    line = append_transcript(
        session_id=body.session_id,
        conversation_id=body.conversation_id,
        user_id=str(user.id),
        project_id=body.project_id,
        persona_id=body.persona_id or "nuha",
        channel=body.channel,
        speaker=body.speaker,
        text=body.text,
        confidence=body.confidence,
        mission_id=body.mission_id,
        tool_ref=body.tool_ref,
    )
    return line


@router.get("/transcript")
async def transcript_get(
    request: Request,
    db=Depends(get_db),
    session_id: Optional[str] = None,
    conversation_id: Optional[str] = None,
):
    user = await get_current_user(request, db)
    if session_id:
        s = get_voice_session(session_id)
        if not s or s.get("user_id") != str(user.id):
            raise HTTPException(404, "session not found")
    return {"lines": get_transcript(session_id=session_id, conversation_id=conversation_id)}
