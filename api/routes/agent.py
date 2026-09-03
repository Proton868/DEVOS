"""Agentic IDE routes — governed coding agent tool loop.

POST /api/agent/run          — start agent task (SSE)
POST /api/agent/{id}/cancel  — cancel
GET  /api/agent/{id}         — status
GET  /api/agent/tools        — list tools for mode
POST /api/agent/patch/preview
POST /api/agent/patch/apply
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from core.database import get_db
from api.routes.auth import get_current_user
from governance.tenant_store import ensure_personal_tenant

router = APIRouter()


class AgentContextIn(BaseModel):
    active_file: Optional[str] = None
    selected_text: Optional[str] = None
    cursor_position: Optional[dict] = None
    open_files: list[str] = Field(default_factory=list)
    recent_files: list[str] = Field(default_factory=list)
    git_status_summary: Optional[str] = None
    diagnostics: list[dict] = Field(default_factory=list)
    terminal_context: Optional[str] = None


class AgentRunReq(BaseModel):
    objective: str
    project_id: str = "default"
    mode: str = "agent"  # ask | edit | agent | review
    provider: Optional[str] = None
    model: Optional[str] = None
    context: Optional[AgentContextIn] = None


class PatchPreviewReq(BaseModel):
    project_id: str = "default"
    path: str
    unified_diff: Optional[str] = None
    old_text: Optional[str] = None
    new_text: Optional[str] = None


class PatchApplyReq(BaseModel):
    project_id: str = "default"
    path: str
    unified_diff: Optional[str] = None
    old_text: Optional[str] = None
    new_text: Optional[str] = None
    expected_hash: Optional[str] = None


@router.get("/tools")
async def list_tools(mode: str = "agent", request: Request = None, db=Depends(get_db)):
    user = await get_current_user(request, db)
    await ensure_personal_tenant(db, user)
    from brain.agent_tools import AgentMode, list_agent_tools
    try:
        m = AgentMode(mode)
    except ValueError:
        raise HTTPException(400, f"invalid mode: {mode}")
    return {"mode": m.value, "tools": list_agent_tools(m)}


@router.post("/run")
async def run_agent(req: AgentRunReq, request: Request, db=Depends(get_db)):
    user = await get_current_user(request, db)
    tenant = await ensure_personal_tenant(db, user)

    from brain.agent_runtime import AgentContext, AgentRuntime
    from brain.agent_tools import AgentMode

    try:
        mode = AgentMode(req.mode)
    except ValueError:
        raise HTTPException(400, f"invalid mode: {req.mode}")

    if not req.objective or not req.objective.strip():
        raise HTTPException(400, "objective required")

    ctx_in = req.context or AgentContextIn()
    context = AgentContext(
        project_id=req.project_id or "default",
        active_file=ctx_in.active_file,
        selected_text=ctx_in.selected_text,
        cursor_position=ctx_in.cursor_position,
        open_files=ctx_in.open_files or [],
        recent_files=ctx_in.recent_files or [],
        git_status_summary=ctx_in.git_status_summary,
        diagnostics=ctx_in.diagnostics or [],
        terminal_context=ctx_in.terminal_context,
        user_request=req.objective,
    )

    runtime = AgentRuntime(
        user_id=user.id,
        project_id=req.project_id or "default",
        tenant_id=getattr(tenant, "id", None) if tenant else None,
        provider=req.provider,
        model=req.model,
        mode=mode,
    )

    async def sse():
        try:
            async for event in runtime.run(req.objective.strip(), context):
                yield f"data: {json.dumps(event, default=str)}\n\n"
                await asyncio.sleep(0)
        except Exception as e:
            err = {
                "type": "agent.error",
                "data": {"message": str(e)},
            }
            yield f"data: {json.dumps(err)}\n\n"
        yield "data: {\"type\": \"agent.stream_end\"}\n\n"

    return StreamingResponse(sse(), media_type="text/event-stream")


@router.get("/tasks")
async def list_tasks(request: Request, db=Depends(get_db)):
    user = await get_current_user(request, db)
    await ensure_personal_tenant(db, user)
    from brain.agent_runtime import list_tasks_for_user
    live = list_tasks_for_user(user.id)
    # Merge durable history for completed/prior sessions
    try:
        from brain.agent_task_store import list_user_tasks
        durable = await list_user_tasks(user.id, limit=20)
        seen = {t["id"] for t in live}
        for t in durable:
            if t["id"] not in seen:
                live.append(t)
    except Exception:
        pass
    return {"tasks": live[:30]}


@router.get("/{task_id}")
async def get_task(task_id: str, request: Request, db=Depends(get_db)):
    user = await get_current_user(request, db)
    await ensure_personal_tenant(db, user)
    from brain.agent_runtime import get_task as get_live
    task = get_live(task_id)
    if task:
        if task.user_id != user.id:
            raise HTTPException(404, "task not found")
        return task.to_dict()
    from brain.agent_task_store import load_task
    row = await load_task(task_id)
    if not row or row.get("user_id") != user.id:
        raise HTTPException(404, "task not found")
    return row


@router.get("/{task_id}/events")
async def get_task_events(
    task_id: str,
    after_seq: int = 0,
    request: Request = None,
    db=Depends(get_db),
):
    """Reconnect helper: return buffered agent events after a sequence number.

    Events are scoped to the authenticated user. Never leaks cross-tenant streams.
    """
    user = await get_current_user(request, db)
    await ensure_personal_tenant(db, user)
    from brain.agent_runtime import get_task as get_live, get_task_events as live_events
    task = get_live(task_id)
    if task:
        if task.user_id != user.id:
            raise HTTPException(404, "task not found")
        return {"task_id": task_id, "events": live_events(task_id, after_seq)}
    from brain.agent_task_store import load_task, load_task_events
    row = await load_task(task_id)
    if not row or row.get("user_id") != user.id:
        raise HTTPException(404, "task not found")
    events = await load_task_events(task_id, after_seq)
    return {"task_id": task_id, "events": events, "status": row.get("status")}


@router.post("/{task_id}/cancel")
async def cancel_task(task_id: str, request: Request, db=Depends(get_db)):
    user = await get_current_user(request, db)
    await ensure_personal_tenant(db, user)
    from brain.agent_runtime import get_task, request_cancel
    task = get_task(task_id)
    if task:
        if task.user_id != user.id:
            raise HTTPException(404, "task not found")
        ok = request_cancel(task_id)
        # Mirror durable cancel for live tasks
        try:
            from brain.agent_task_store import mark_task_cancelled
            await mark_task_cancelled(task_id, user.id)
        except Exception:
            pass
        return {"ok": ok, "task_id": task_id, "status": "cancel_requested"}
    # Durable-only task: cancel without executing
    from brain.agent_task_store import mark_task_cancelled, load_task
    result = await mark_task_cancelled(task_id, user.id)
    if not result.get("ok"):
        raise HTTPException(404, "task not found")
    return {
        "ok": True,
        "task_id": task_id,
        "status": result.get("status", "cancelled"),
        "already_terminal": result.get("already_terminal", False),
    }


@router.post("/patch/preview")
async def patch_preview(req: PatchPreviewReq, request: Request, db=Depends(get_db)):
    user = await get_current_user(request, db)
    await ensure_personal_tenant(db, user)
    from execution.files import FileService, PathViolation
    from brain.agent_tools import apply_context_replace, apply_unified_diff, make_line_diff

    fs = FileService(user.id, req.project_id or "default")
    try:
        current = fs.read(req.path)["content"] or ""
    except FileNotFoundError:
        raise HTTPException(404, "file not found")
    except PathViolation as e:
        raise HTTPException(400, str(e))

    if req.unified_diff:
        ok, new_content, err = apply_unified_diff(current, req.unified_diff)
    elif req.old_text is not None:
        ok, new_content, err = apply_context_replace(
            current, req.old_text or "", req.new_text or ""
        )
    else:
        raise HTTPException(400, "unified_diff or old_text required")

    if not ok:
        return {"ok": False, "error": err, "path": req.path}

    diff = make_line_diff(current, new_content)
    return {
        "ok": True,
        "path": req.path,
        "additions": sum(1 for d in diff if d["type"] == "add"),
        "deletions": sum(1 for d in diff if d["type"] == "del"),
        "diff": diff[:500],
        "preview_len": len(new_content),
    }


@router.post("/patch/apply")
async def patch_apply(req: PatchApplyReq, request: Request, db=Depends(get_db)):
    """Apply a user-accepted patch through FileService (same path validation as IDE)."""
    user = await get_current_user(request, db)
    await ensure_personal_tenant(db, user)
    import hashlib
    from execution.files import FileService, PathViolation
    from brain.agent_tools import apply_context_replace, apply_unified_diff, make_line_diff

    fs = FileService(user.id, req.project_id or "default")
    try:
        current = fs.read(req.path)["content"] or ""
    except FileNotFoundError:
        raise HTTPException(404, "file not found")
    except PathViolation as e:
        raise HTTPException(400, str(e))

    if req.expected_hash:
        h = hashlib.sha256(current.encode("utf-8")).hexdigest()
        if h != req.expected_hash:
            raise HTTPException(409, "patch_conflict: file changed since preview")

    if req.unified_diff:
        ok, new_content, err = apply_unified_diff(current, req.unified_diff)
    elif req.old_text is not None:
        ok, new_content, err = apply_context_replace(
            current, req.old_text or "", req.new_text or ""
        )
    else:
        raise HTTPException(400, "unified_diff or old_text required")

    if not ok:
        raise HTTPException(409, err or "patch_conflict")

    fs.write(req.path, new_content)
    diff = make_line_diff(current, new_content)
    return {
        "ok": True,
        "path": req.path,
        "additions": sum(1 for d in diff if d["type"] == "add"),
        "deletions": sum(1 for d in diff if d["type"] == "del"),
        "diff": diff[:200],
    }


class ChangeIdReq(BaseModel):
    project_id: str = "default"


@router.get("/{task_id}/changes")
async def list_task_changes(task_id: str, request: Request, db=Depends(get_db)):
    user = await get_current_user(request, db)
    await ensure_personal_tenant(db, user)
    from brain.agent_changes import list_task_changes_detailed
    from brain.agent_runtime import get_task
    task = get_task(task_id)
    if task and task.user_id != user.id:
        raise HTTPException(404, "task not found")
    return {"task_id": task_id, "changes": list_task_changes_detailed(task_id, user.id)}


@router.post("/changes/{change_id}/accept")
async def accept_one(change_id: str, request: Request, db=Depends(get_db)):
    user = await get_current_user(request, db)
    await ensure_personal_tenant(db, user)
    from brain.agent_changes import accept_change
    return accept_change(change_id, user.id)


@router.post("/changes/{change_id}/reject")
async def reject_one(change_id: str, project_id: str = "default", request: Request = None, db=Depends(get_db)):
    user = await get_current_user(request, db)
    await ensure_personal_tenant(db, user)
    from brain.agent_changes import reject_change, get_change
    from execution.files import FileService
    rec = get_change(change_id, user.id)
    if not rec:
        raise HTTPException(404, "change not found")
    fs = FileService(user.id, rec.project_id or project_id)
    return reject_change(change_id, user.id, fs)


@router.post("/changes/{change_id}/revert")
async def revert_one(change_id: str, project_id: str = "default", request: Request = None, db=Depends(get_db)):
    user = await get_current_user(request, db)
    await ensure_personal_tenant(db, user)
    from brain.agent_changes import revert_change, get_change
    from execution.files import FileService
    rec = get_change(change_id, user.id)
    if not rec:
        raise HTTPException(404, "change not found")
    fs = FileService(user.id, rec.project_id or project_id)
    return revert_change(change_id, user.id, fs)


@router.post("/{task_id}/changes/accept-all")
async def accept_all_changes(task_id: str, request: Request, db=Depends(get_db)):
    user = await get_current_user(request, db)
    await ensure_personal_tenant(db, user)
    from brain.agent_changes import accept_all
    return accept_all(task_id, user.id)


@router.post("/{task_id}/changes/reject-all")
async def reject_all_changes(task_id: str, project_id: str = "default", request: Request = None, db=Depends(get_db)):
    user = await get_current_user(request, db)
    await ensure_personal_tenant(db, user)
    from brain.agent_changes import reject_all
    from brain.agent_runtime import get_task
    from execution.files import FileService
    task = get_task(task_id)
    pid = (task.project_id if task else None) or project_id
    fs = FileService(user.id, pid)
    return reject_all(task_id, user.id, fs)
