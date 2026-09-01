"""
Loop route — exposes the Brain↔Execution loop to the frontend.
This is the core of DevOS v2. The user gives a goal,
the Brain plans and executes autonomously, streaming each step back.
"""
import asyncio
import json
from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Optional
from core.database import get_db
from api.routes.auth import get_current_user

router = APIRouter()

class LoopRequest(BaseModel):
    goal: str
    provider: Optional[str] = None
    model: Optional[str] = None
    session_id: Optional[str] = None

@router.post("/run")
async def run_loop(req: LoopRequest, request: Request,
                   db=Depends(get_db)):
    user = await get_current_user(request, db)
    import uuid
    session_id = req.session_id or str(uuid.uuid4())

    async def sse_stream():
        from core.loop import BrainExecutionLoop, LoopStep, StepType

        steps_queue: asyncio.Queue = asyncio.Queue()

        async def on_step(step: LoopStep):
            await steps_queue.put(step)

        async def run_loop_task():
            loop = BrainExecutionLoop(
                user_id=user.id,
                session_id=session_id,
                provider=req.provider,
                model=req.model,
                on_step=on_step,
            )
            state = await loop.run(req.goal)
            await steps_queue.put(state)  # Final state marker

        task = asyncio.create_task(run_loop_task())

        while True:
            try:
                item = await asyncio.wait_for(steps_queue.get(), timeout=120.0)
            except asyncio.TimeoutError:
                yield f"data: {json.dumps({'type': 'error', 'content': 'Timeout'})}\n\n"
                break

            from core.loop import LoopState, LoopStep
            if isinstance(item, LoopState):
                # Final result
                yield f"data: {json.dumps({'type': 'done', 'state': item.to_dict(), 'answer': item.final_answer, 'session_id': session_id})}\n\n"
                break
            elif isinstance(item, LoopStep):
                yield f"data: {json.dumps({'type': 'step', 'step_type': item.type, 'content': item.content[:500], 'meta': item.metadata})}\n\n"

        if not task.done():
            task.cancel()

    return StreamingResponse(sse_stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

@router.post("/run/sync")
async def run_loop_sync(req: LoopRequest, request: Request, db=Depends(get_db)):
    """Non-streaming version for simple clients."""
    user = await get_current_user(request, db)
    import uuid
    from core.loop import BrainExecutionLoop
    loop = BrainExecutionLoop(user_id=user.id,
                               session_id=req.session_id or str(uuid.uuid4()),
                               provider=req.provider, model=req.model)
    state = await loop.run(req.goal)
    return state.to_dict()
