"""Terminal route — persistent PTY-backed interactive shell.

The WebSocket route at /{project_id}/ws now speaks the same protocol the
frontend's TerminalPanel.jsx expects: {type:"input", data} → write to PTY,
{type:"resize", cols, rows} → resize PTY, and PTY output → {type:"data", data}.

See execution/pty_session.py for the session manager — one persistent PTY
per (user_id, project_id), shared across all WebSocket connections attached
to that project. The old one-command-at-a-time HTTP route at /{project_id}/run
is preserved for backward compatibility with non-interactive callers.
"""
import logging
from fastapi import APIRouter, Depends, Request, HTTPException, WebSocket, WebSocketDisconnect
from sqlalchemy import select

from core.database import AsyncSessionLocal, User
from api.routes.auth import verify_any_token, sync_supabase_user
from execution.pty_session import get_or_create_session, kill_session

logger = logging.getLogger("devos.terminal.route")
router = APIRouter()


@router.websocket("/{project_id}/ws")
async def terminal_ws(websocket: WebSocket, project_id: str):
    """Persistent PTY-backed WebSocket terminal. Client sends:
      - First message: {"token": "..."} to authenticate
      - Then: {"type": "input", "data": "..."} to write to the PTY
      - Or: {"type": "resize", "cols": N, "rows": M} to resize the PTY
    Server sends:
      - {"type": "data", "data": "..."} for PTY output
      - {"type": "exit", "exitCode": N} when the shell process exits
    """
    await websocket.accept()
    user = None
    session = None

    try:
        # Authenticate
        auth_msg = await websocket.receive_json()
        token = auth_msg.get("token", "")
        payload, source = verify_any_token(token)
        if payload is None:
            await websocket.send_json({"type": "error", "message": "Invalid or missing token"})
            await websocket.close(code=4401)
            return

        async with AsyncSessionLocal() as db:
            if source == "supabase":
                user = await sync_supabase_user(db, payload)
            else:
                r = await db.execute(select(User).where(User.id == payload["sub"]))
                user = r.scalar_one_or_none()

        if not user:
            await websocket.send_json({"type": "error", "message": "User not found"})
            await websocket.close(code=4401)
            return

        # Get or create a persistent PTY session for this project
        session = await get_or_create_session(user.id, project_id)
        session.attach(websocket)

        # Main message loop
        while True:
            try:
                msg = await websocket.receive_json()
            except WebSocketDisconnect:
                break

            msg_type = msg.get("type", "")
            if msg_type == "input":
                data = msg.get("data", "")
                session.write(data.encode("utf-8"))
            elif msg_type == "resize":
                cols = msg.get("cols", 80)
                rows = msg.get("rows", 24)
                await session.resize(cols, rows)
            # Ignore unknown message types

    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.warning(f"[terminal] ws error for project={project_id}: {e}")
        try:
            await websocket.send_json({"type": "error", "message": str(e)})
        except Exception:
            pass
    finally:
        if session and websocket:
            session.detach(websocket)


# ── Backward-compatible HTTP route (one-command-at-a-time, non-interactive) ──

from pydantic import BaseModel
from core.database import get_db
from api.routes.auth import get_current_user
from execution.terminal import TerminalService, DeniedCommand


class RunReq(BaseModel):
    command: str
    timeout: int = 60


@router.post("/{project_id}/run")
async def run_command(project_id: str, req: RunReq, request: Request, db=Depends(get_db)):
    user = await get_current_user(request, db)
    try:
        return await TerminalService(user.id, project_id).run(req.command, req.timeout)
    except DeniedCommand as e:
        raise HTTPException(403, str(e))
