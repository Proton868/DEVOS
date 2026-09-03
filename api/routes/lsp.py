"""LSP WebSocket proxy — Monaco useLSP ↔ language server manager.

Protocol:
  1. Client connects to /api/lsp/{project_id}/ws?lang=python
  2. First JSON message: {"token": "..."}
  3. Subsequent messages: LSP JSON-RPC objects (no Content-Length framing on WS)
  4. Server sends JSON-RPC objects back (diagnostics, responses, etc.)

Status endpoint:
  GET /api/lsp/status — which servers are installed/available
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, Request, WebSocket, WebSocketDisconnect, HTTPException
from sqlalchemy import select

from core.database import AsyncSessionLocal, User, get_db
from api.routes.auth import get_current_user, verify_any_token, sync_supabase_user
from governance.tenant_store import ensure_personal_tenant
from execution.lsp_manager import (
    SUPPORTED_LANGUAGES,
    get_lsp_manager,
    list_available_servers,
)

logger = logging.getLogger("devos.lsp.route")
router = APIRouter()


@router.get("/status")
async def lsp_status(request: Request, db=Depends(get_db)):
    user = await get_current_user(request, db)
    await ensure_personal_tenant(db, user)
    return {
        "supported": sorted(SUPPORTED_LANGUAGES),
        "servers": list_available_servers(),
    }


@router.websocket("/{project_id}/ws")
async def lsp_ws(websocket: WebSocket, project_id: str, lang: str = "python"):
    await websocket.accept()
    user = None
    session = None
    send_cb = None
    language = (lang or "python").lower().strip()

    try:
        auth_msg = await websocket.receive_json()
        token = auth_msg.get("token", "") if isinstance(auth_msg, dict) else ""
        payload, source = verify_any_token(token)
        if payload is None:
            await websocket.send_json({"jsonrpc": "2.0", "error": {"code": -32000, "message": "unauthorized"}})
            await websocket.close(code=4401)
            return

        async with AsyncSessionLocal() as db:
            if source == "supabase":
                user = await sync_supabase_user(db, payload)
            else:
                uid = payload.get("sub") or payload.get("user_id")
                r = await db.execute(select(User).where(User.id == uid))
                user = r.scalar_one_or_none()
            if not user:
                await websocket.send_json({"jsonrpc": "2.0", "error": {"code": -32000, "message": "user not found"}})
                await websocket.close(code=4401)
                return
            await ensure_personal_tenant(db, user)

        if language not in SUPPORTED_LANGUAGES:
            await websocket.send_json({
                "jsonrpc": "2.0",
                "error": {"code": -32601, "message": f"unsupported language: {language}"},
            })
            await websocket.close(code=4400)
            return

        mgr = get_lsp_manager()
        try:
            session = await mgr.get_session(user.id, project_id, language)
        except FileNotFoundError as e:
            await websocket.send_json({
                "jsonrpc": "2.0",
                "method": "window/showMessage",
                "params": {"type": 2, "message": str(e)},
            })
            await websocket.send_json({
                "type": "lsp.unavailable",
                "language": language,
                "message": str(e),
            })
            # Keep socket open so client can still get clear errors on requests
            while True:
                msg = await websocket.receive_json()
                if isinstance(msg, dict) and "id" in msg:
                    await websocket.send_json({
                        "jsonrpc": "2.0",
                        "id": msg["id"],
                        "error": {"code": -32001, "message": str(e)},
                    })
            return
        except Exception as e:
            logger.exception("lsp session start failed")
            await websocket.send_json({
                "jsonrpc": "2.0",
                "error": {"code": -32002, "message": str(e)},
            })
            await websocket.close(code=1011)
            return

        async def send_to_client(msg: dict):
            await websocket.send_json(msg)

        send_cb = send_to_client
        session.attach(send_cb)
        await websocket.send_json({
            "type": "lsp.ready",
            "language": language,
            "project_id": project_id,
            "command": session.command,
        })

        while True:
            msg = await websocket.receive_json()
            if not isinstance(msg, dict):
                continue
            # Allow client to pass token-only first msg already consumed
            if set(msg.keys()) <= {"token", "type"} and "method" not in msg:
                continue
            await session.forward_from_client(msg)

    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.exception("lsp ws error")
        try:
            await websocket.send_json({"jsonrpc": "2.0", "error": {"code": -32003, "message": str(e)}})
        except Exception:
            pass
    finally:
        if session and send_cb:
            session.detach(send_cb)
            if not session._clients:
                # Idle: keep process for reuse; do not kill immediately
                pass
