"""
Loop route — Brain↔Execution loop via the unified execution pipeline.

identity → UCI (inside BrainExecutionLoop) → isolation → evidence (+ job id)
"""
import asyncio
import json
from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Optional
from core.database import get_db
from api.routes.auth import get_current_user
from governance.tenant_store import ensure_personal_tenant

router = APIRouter()


class LoopRequest(BaseModel):
    goal: str
    provider: Optional[str] = None
    model: Optional[str] = None
    session_id: Optional[str] = None


@router.post("/run")
async def run_loop(req: LoopRequest, request: Request, db=Depends(get_db)):
    user = await get_current_user(request, db)
    tenant = await ensure_personal_tenant(db, user)
    import uuid
    session_id = req.session_id or str(uuid.uuid4())

    async def sse_stream():
        from core.loop import LoopStep, LoopState
        from governance.execution_pipeline import run_brain_loop

        steps_queue: asyncio.Queue = asyncio.Queue()

        async def on_step(step: LoopStep):
            await steps_queue.put(step)

        async def run_loop_task():
            state, _ctx, _identity = await run_brain_loop(
                user=user,
                tenant_id=tenant.id,
                session_id=session_id,
                goal=req.goal,
                provider=req.provider,
                model=req.model,
                on_step=on_step,
                path="brain_loop",
            )
            await steps_queue.put(state)

        task = asyncio.create_task(run_loop_task())
        while True:
            try:
                item = await asyncio.wait_for(steps_queue.get(), timeout=120.0)
            except asyncio.TimeoutError:
                yield f"data: {json.dumps({'type': 'error', 'content': 'Timeout'})}\n\n"
                break
            if isinstance(item, LoopState):
                yield f"data: {json.dumps({'type': 'done', 'state': item.to_dict(), 'answer': item.final_answer, 'session_id': session_id})}\n\n"
                break
            elif isinstance(item, LoopStep):
                yield f"data: {json.dumps({'type': 'step', 'step_type': item.type, 'content': item.content[:500], 'meta': item.metadata})}\n\n"
        if not task.done():
            task.cancel()

    return StreamingResponse(
        sse_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/run/sync")
async def run_loop_sync(req: LoopRequest, request: Request, db=Depends(get_db)):
    user = await get_current_user(request, db)
    tenant = await ensure_personal_tenant(db, user)
    import uuid
    session_id = req.session_id or str(uuid.uuid4())
    from governance.execution_pipeline import run_brain_loop
    state, _ctx, _identity = await run_brain_loop(
        user=user,
        tenant_id=tenant.id,
        session_id=session_id,
        goal=req.goal,
        provider=req.provider,
        model=req.model,
        path="brain_loop_sync",
    )
    return {"state": state.to_dict(), "answer": state.final_answer, "session_id": session_id}
