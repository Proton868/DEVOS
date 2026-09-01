"""Communications route — exposes the in-process EventBus to the frontend
over Server-Sent Events, so the IDE gets pushed HITL/terminal/build events
instead of polling. Auth via query-param token since browser EventSource
can't set custom headers."""
import asyncio
import json
import logging

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

logger = logging.getLogger("devos.comms.route")
router = APIRouter()


async def _event_stream(user_id: str):
    from communications.bus import EventBus
    bus = EventBus()
    try:
        async for event in bus.subscribe(f"user:{user_id}"):
            yield f"id: {event['id']}\nevent: {event['type']}\ndata: {json.dumps(event['data'])}\n\n"
    except asyncio.CancelledError:
        logger.info(f"[comms] SSE stream closed for user {user_id}")
        raise


@router.get("/stream")
async def stream(token: str):
    # Delegates to auth.py's verify_any_token() (security-audit P2) so
    # Supabase-authenticated users' event streams work too -- the previous
    # inline jwt.decode(..., algorithms=["HS256"]) only understood locally
    # issued tokens and silently 401'd every Supabase session here even
    # though /api/auth/me worked fine for the same user.
    from api.routes.auth import verify_any_token
    payload, _source = verify_any_token(token)
    if payload is None:
        raise HTTPException(401, "Invalid or missing token")
    user_id = payload["sub"]
    return StreamingResponse(
        _event_stream(user_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )
