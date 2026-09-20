"""Remote SSH terminal WebSocket — same client protocol as local terminal.

Distinguishes mode=remote_ssh and always emits verified host_identity.
Credentials are never sent on the wire.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import select

from core.database import AsyncSessionLocal, User
from api.routes.auth import verify_any_token, sync_supabase_user
from execution.ssh_remote_session import get_or_create_remote_session
from governance.ssh_host_verify import HostVerifyError, HostKeyChangedError

logger = logging.getLogger("devos.ssh_terminal.route")
router = APIRouter()


@router.websocket("/{connection_id}/ws")
async def ssh_terminal_ws(websocket: WebSocket, connection_id: str):
    await websocket.accept()
    try:
        first = await websocket.receive_json()
        token = first.get("token")
        auto_approve = bool(first.get("auto_approve_new_host", False))
        if not token:
            await websocket.send_json({"type": "error", "message": "auth_required"})
            await websocket.close(code=4401)
            return

        payload = verify_any_token(token)
        async with AsyncSessionLocal() as db:
            if payload.get("supabase"):
                user = await sync_supabase_user(db, payload)
            else:
                r = await db.execute(select(User).where(User.id == payload["sub"]))
                user = r.scalar_one_or_none()
        if not user:
            await websocket.send_json({"type": "error", "message": "user_not_found"})
            await websocket.close(code=4401)
            return

        try:
            session = await get_or_create_remote_session(
                user.id,
                connection_id,
                actor="user",
                auto_approve_new=auto_approve,
            )
        except HostKeyChangedError:
            await websocket.send_json({
                "type": "error",
                "code": "host_key_changed",
                "message": "REMOTE HOST IDENTIFICATION HAS CHANGED",
                "mode": "remote_ssh",
            })
            await websocket.close(code=4403)
            return
        except HostVerifyError as e:
            await websocket.send_json({
                "type": "error",
                "code": e.code,
                "message": e.code,
                "mode": "remote_ssh",
            })
            await websocket.close(code=4403)
            return
        except Exception as e:
            logger.warning("ssh_terminal_connect_failed: %s", type(e).__name__)
            await websocket.send_json({"type": "error", "message": "connect_failed"})
            await websocket.close(code=4500)
            return

        session.attach(websocket)
        await session.send_status(websocket)

        while True:
            try:
                msg = await websocket.receive_json()
            except WebSocketDisconnect:
                break
            msg_type = msg.get("type", "")
            if msg_type == "input":
                data = msg.get("data", "")
                await session.write(data.encode("utf-8"))
            elif msg_type == "resize":
                await session.resize(int(msg.get("cols", 80)), int(msg.get("rows", 24)))
            elif msg_type == "close":
                break
    finally:
        try:
            session.detach(websocket)  # type: ignore[name-defined]
        except Exception:
            pass
