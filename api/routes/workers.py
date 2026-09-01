"""Workers route — list/detail/run for the AGENT_LIBRARY personas, now
actually invocable via workers/runtime.py instead of being pure data."""
import asyncio
import json
import uuid
from fastapi import APIRouter, Depends, Request, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Optional

from core.database import get_db
from api.routes.auth import get_current_user
from brain.agents import AGENT_LIBRARY
from workers.runtime import WorkerRuntime, UnknownWorkerError

router = APIRouter()


@router.get("")
async def list_workers(request: Request, db=Depends(get_db)):
    await get_current_user(request, db)
    return {"workers": [p.to_dict() for p in AGENT_LIBRARY.values()]}


@router.get("/{slug}")
async def get_worker(slug: str, request: Request, db=Depends(get_db)):
    await get_current_user(request, db)
    persona = AGENT_LIBRARY.get(slug)
    if not persona:
        raise HTTPException(404, f"No worker persona '{slug}'")
    return persona.to_dict()


class WorkerRunRequest(BaseModel):
    goal: str
    provider: Optional[str] = None
    model: Optional[str] = None
    session_id: Optional[str] = None
    trust_level: str = "OPERATOR"


def _build_requester_identity(user_id: str, session_id: str, trust_level_str: str):
    from governance.ucip import AgentIdentity, TrustLevel
    return AgentIdentity.create(user_id, session_id, TrustLevel.from_str(trust_level_str))


class PlanRunRequest(BaseModel):
    goal: str
    provider: Optional[str] = None
    model: Optional[str] = None
    session_id: Optional[str] = None
    trust_level: str = "OPERATOR"


@router.post("/plan/run")
async def run_coordinated_plan(req: PlanRunRequest, request: Request, db=Depends(get_db)):
    """Decomposes the goal (cognitive.decomposer) then dispatches each
    subtask to whichever Worker persona fits best, running independent
    subtasks concurrently (cognitive.coordinator) — the actual multi-worker
    coordination path, as opposed to /run/sync which invokes exactly one
    named Worker directly.

    Registered BEFORE the /{slug}/run routes below on purpose — a real
    routing bug (found via testing, not before) had FastAPI matching
    /plan/run against /{slug}/run with slug="plan" instead, since more
    specific static routes must be registered before dynamic ones with
    the same shape in FastAPI/Starlette, not just declared anywhere in
    the file."""
    user = await get_current_user(request, db)
    session_id = req.session_id or str(uuid.uuid4())
    requester_identity = _build_requester_identity(user.id, session_id, req.trust_level)

    from cognitive.decomposer import GoalDecomposer
    from cognitive.coordinator import Coordinator
    from brain.llm import BrainLLM

    planning_brain = BrainLLM(req.provider, req.model, user_id=user.id)
    subtasks = await GoalDecomposer().decompose(req.goal, planning_brain)
    results = await Coordinator().run_plan(subtasks, requester_identity,
                                           provider=req.provider, model=req.model)
    return {
        "goal": req.goal,
        "subtasks": [t.to_dict() for t in subtasks],
        "results": [r.to_dict() for r in results],
        "all_succeeded": all(r.success for r in results),
    }


@router.post("/{slug}/run/sync")
async def run_worker_sync(slug: str, req: WorkerRunRequest, request: Request, db=Depends(get_db)):
    user = await get_current_user(request, db)
    if slug not in AGENT_LIBRARY:
        raise HTTPException(404, f"No worker persona '{slug}'")
    session_id = req.session_id or str(uuid.uuid4())
    requester_identity = _build_requester_identity(user.id, session_id, req.trust_level)
    try:
        state, delegated_identity = await WorkerRuntime().run(
            slug, req.goal, requester_identity, provider=req.provider, model=req.model,
        )
    except UnknownWorkerError as e:
        raise HTTPException(404, str(e))
    result = state.to_dict()
    result["worker"] = slug
    result["delegated_identity"] = delegated_identity.to_dict()
    return result


@router.post("/{slug}/run")
async def run_worker_stream(slug: str, req: WorkerRunRequest, request: Request, db=Depends(get_db)):
    """Streaming variant, same SSE shape as /api/loop/run, for a Worker
    instead of the raw generalist Brain."""
    user = await get_current_user(request, db)
    if slug not in AGENT_LIBRARY:
        raise HTTPException(404, f"No worker persona '{slug}'")
    session_id = req.session_id or str(uuid.uuid4())

    async def sse_stream():
        from core.loop import LoopStep, LoopState

        steps_queue: asyncio.Queue = asyncio.Queue()

        async def on_step(step):
            await steps_queue.put(step)

        async def run_worker_task():
            requester_identity = _build_requester_identity(user.id, session_id, req.trust_level)
            state, delegated_identity = await WorkerRuntime().run(
                slug, req.goal, requester_identity,
                provider=req.provider, model=req.model, on_step=on_step,
            )
            result = state.to_dict()
            result["worker"] = slug
            result["delegated_identity"] = delegated_identity.to_dict()
            await steps_queue.put(result)

        task = asyncio.create_task(run_worker_task())

        while True:
            try:
                item = await asyncio.wait_for(steps_queue.get(), timeout=120.0)
            except asyncio.TimeoutError:
                yield f"data: {json.dumps({'type': 'error', 'content': 'Timeout'})}\n\n"
                break
            if isinstance(item, dict):
                yield f"data: {json.dumps({'type': 'done', **item, 'session_id': session_id})}\n\n"
                break
            elif isinstance(item, LoopStep):
                yield f"data: {json.dumps({'type': 'step', 'step_type': item.type, 'content': item.content[:500], 'meta': item.metadata})}\n\n"

        if not task.done():
            task.cancel()

    return StreamingResponse(sse_stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
