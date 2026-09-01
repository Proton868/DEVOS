"""Workers route — list/detail/run with earned-autonomy loop wired.

Authority rules:
  - trust_level is NEVER accepted from the client body
  - Human requester → identity_from_user (ActorKind.HUMAN)
  - Worker persona → delegated WORKER identity from stored trust record
  - Outcomes → evaluate → competency → optional pending promotion (human approves)
"""
from __future__ import annotations

import asyncio
import json
import uuid
from fastapi import APIRouter, Depends, Request, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from typing import Optional

from core.database import get_db
from api.routes.auth import get_current_user
from governance.tenant_store import ensure_personal_tenant
from brain.agents import AGENT_LIBRARY
from workers.runtime import WorkerRuntime, UnknownWorkerError, WorkerTrustUnavailable

router = APIRouter()


@router.get("")
async def list_workers(request: Request, db=Depends(get_db)):
    user = await get_current_user(request, db)
    await ensure_personal_tenant(db, user)
    return {"workers": [p.to_dict() for p in AGENT_LIBRARY.values()]}


@router.get("/{slug}")
async def get_worker(slug: str, request: Request, db=Depends(get_db)):
    user = await get_current_user(request, db)
    tenant = await ensure_personal_tenant(db, user)
    persona = AGENT_LIBRARY.get(slug)
    if not persona:
        raise HTTPException(404, f"No worker persona '{slug}'")
    data = persona.to_dict()
    # Attach trust / pending promotion for this tenant+worker
    from governance.agency_evolution import get_or_create_trust, _effective_autonomy
    row = await get_or_create_trust(db, tenant.id, slug)
    data["trust"] = {
        "trust_level": row.trust_level,
        "autonomy": _effective_autonomy(row),
        "success_count": row.success_count,
        "failure_count": row.failure_count,
        "unauthorized_attempts": row.unauthorized_attempts,
        "competency": row.competency or {},
        "pending_promotion": row.pending_promotion,
        "promotion_expires_at": row.promotion_expires_at.isoformat() if row.promotion_expires_at else None,
        "approved_by": row.approved_by,
    }
    return data


class WorkerRunRequest(BaseModel):
    goal: str
    provider: Optional[str] = None
    model: Optional[str] = None
    session_id: Optional[str] = None
    # trust_level intentionally removed — derived from auth + stored trust


class PlanRunRequest(BaseModel):
    goal: str
    provider: Optional[str] = None
    model: Optional[str] = None
    session_id: Optional[str] = None


async def _build_human_identity(user, session_id: str, tenant_id: str):
    """Human API caller — never WORKER actor kind."""
    from governance.identity_authority import identity_from_user
    from governance.ucip import TrustLevel
    ctx = identity_from_user(
        user.id,
        session_id or "api",
        tenant_id=tenant_id,
        is_admin=bool(getattr(user, "is_admin", False)),
        trust=TrustLevel.OPERATOR,
    )
    return ctx.to_agent_identity(), ctx


@router.post("/plan/run")
async def run_coordinated_plan(req: PlanRunRequest, request: Request, db=Depends(get_db)):
    user = await get_current_user(request, db)
    tenant = await ensure_personal_tenant(db, user)
    session_id = req.session_id or str(uuid.uuid4())
    requester_identity, _ = await _build_human_identity(user, session_id, tenant.id)

    from cognitive.decomposer import GoalDecomposer
    from cognitive.coordinator import Coordinator
    from brain.llm import BrainLLM

    planning_brain = BrainLLM(req.provider, req.model, user_id=user.id)
    subtasks = await GoalDecomposer().decompose(req.goal, planning_brain)
    results = await Coordinator().run_plan(
        subtasks, requester_identity, provider=req.provider, model=req.model
    )
    return {
        "goal": req.goal,
        "subtasks": [t.to_dict() for t in subtasks],
        "results": [r.to_dict() for r in results],
        "all_succeeded": all(r.success for r in results),
    }


async def _run_and_learn(slug: str, goal: str, user, tenant, session_id: str,
                         provider=None, model=None, on_step=None):
    requester_identity, human_ctx = await _build_human_identity(user, session_id, tenant.id)
    state, delegated_identity = await WorkerRuntime().run(
        slug, goal, requester_identity,
        provider=provider, model=model, on_step=on_step,
        tenant_id=tenant.id, db=None,
    )
    # Close the learning loop
    from governance.agency_evolution import evaluate_execution, record_outcome
    from core.database import AsyncSessionLocal

    success = bool(getattr(state, "succeeded", False))
    steps = list(getattr(state, "steps", []) or [])
    caps_used = set()
    violations = list(getattr(state, "sandbox_violations", []) or [])
    unauthorized = (getattr(state, "ucip_denials", 0) or 0) > 0
    for step in steps:
        meta = getattr(step, "metadata", None) or {}
        if isinstance(meta, dict):
            cap = meta.get("capability") or meta.get("cap")
            if cap:
                caps_used.add(cap)
            if meta.get("denied") or meta.get("unauthorized"):
                unauthorized = True
            # From sandbox / UCIP result metadata
            if meta.get("status") == "sandbox_denied":
                unauthorized = True
        st = getattr(step, "type", "") or ""
        if st in ("sandbox_denied", "denied", "ucip_deny"):
            unauthorized = True
    outcome_valid = getattr(state, "outcome_valid", None)

    evaluation = evaluate_execution(
        success=success and not unauthorized,
        status="complete" if success else "failed",
        unauthorized=unauthorized,
        capabilities_used=sorted(caps_used) or ["ucip:general"],
        sandbox_violations=violations,
        expected_outcome_met=outcome_valid,
        steps=len(steps),
        max_steps=20,
    )
    async with AsyncSessionLocal() as learn_db:
        trust_row = await record_outcome(
            learn_db, tenant.id, slug, evaluation=evaluation,
            owner_id=user.id, goal=goal,
        )
    return state, delegated_identity, trust_row, evaluation


@router.post("/{slug}/run/sync")
async def run_worker_sync(slug: str, req: WorkerRunRequest, request: Request, db=Depends(get_db)):
    user = await get_current_user(request, db)
    tenant = await ensure_personal_tenant(db, user)
    if slug not in AGENT_LIBRARY:
        raise HTTPException(404, f"No worker persona '{slug}'")
    session_id = req.session_id or str(uuid.uuid4())
    try:
        state, delegated_identity, trust_row, evaluation = await _run_and_learn(
            slug, req.goal, user, tenant, session_id,
            provider=req.provider, model=req.model,
        )
    except UnknownWorkerError as e:
        raise HTTPException(404, str(e))
    except WorkerTrustUnavailable as e:
        raise HTTPException(503, f"worker trust unavailable: {e}")
    result = state.to_dict()
    result["worker"] = slug
    result["delegated_identity"] = delegated_identity.to_dict()
    result["evaluation"] = evaluation.to_dict()
    result["trust"] = {
        "trust_level": trust_row.trust_level,
        "autonomy": trust_row.autonomy,
        "success_count": trust_row.success_count,
        "failure_count": trust_row.failure_count,
        "pending_promotion": trust_row.pending_promotion,
        "competency": trust_row.competency or {},
    }
    return result


@router.post("/{slug}/run")
async def run_worker_stream(slug: str, req: WorkerRunRequest, request: Request, db=Depends(get_db)):
    user = await get_current_user(request, db)
    tenant = await ensure_personal_tenant(db, user)
    if slug not in AGENT_LIBRARY:
        raise HTTPException(404, f"No worker persona '{slug}'")
    session_id = req.session_id or str(uuid.uuid4())

    async def sse_stream():
        from core.loop import LoopStep

        steps_queue: asyncio.Queue = asyncio.Queue()

        async def on_step(step):
            await steps_queue.put(step)

        async def run_worker_task():
            state, delegated_identity, trust_row, evaluation = await _run_and_learn(
                slug, req.goal, user, tenant, session_id,
                provider=req.provider, model=req.model, on_step=on_step,
            )
            result = state.to_dict()
            result["worker"] = slug
            result["delegated_identity"] = delegated_identity.to_dict()
            result["evaluation"] = evaluation.to_dict()
            result["trust"] = {
                "trust_level": trust_row.trust_level,
                "autonomy": trust_row.autonomy,
                "pending_promotion": trust_row.pending_promotion,
                "competency": trust_row.competency or {},
            }
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

    return StreamingResponse(
        sse_stream(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── Human-gated promotion / demotion ─────────────────────────────────────────

class PromotionDecision(BaseModel):
    permanent: bool = True
    duration_hours: Optional[int] = Field(default=None, description="If not permanent, hours until expiry")


@router.post("/{slug}/promotion/approve")
async def approve_worker_promotion(
    slug: str, req: PromotionDecision, request: Request, db=Depends(get_db)
):
    """Human approves a pending promotion (permanent or time-bounded)."""
    user = await get_current_user(request, db)
    tenant = await ensure_personal_tenant(db, user)
    from governance.agency_evolution import approve_promotion
    try:
        row = await approve_promotion(
            db, tenant.id, slug,
            approved_by=user.id,
            permanent=req.permanent,
            duration_hours=None if req.permanent else req.duration_hours,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {
        "worker": slug,
        "trust_level": row.trust_level,
        "autonomy": row.autonomy,
        "promotion_expires_at": row.promotion_expires_at.isoformat() if row.promotion_expires_at else None,
        "approved_by": row.approved_by,
    }


@router.post("/{slug}/promotion/reject")
async def reject_worker_promotion(slug: str, request: Request, db=Depends(get_db)):
    user = await get_current_user(request, db)
    tenant = await ensure_personal_tenant(db, user)
    from governance.agency_evolution import reject_promotion
    row = await reject_promotion(db, tenant.id, slug, rejected_by=user.id)
    return {"worker": slug, "pending_promotion": row.pending_promotion}


@router.post("/{slug}/demote")
async def demote_worker_endpoint(slug: str, request: Request, db=Depends(get_db)):
    user = await get_current_user(request, db)
    tenant = await ensure_personal_tenant(db, user)
    from governance.agency_evolution import demote_worker
    row = await demote_worker(db, tenant.id, slug, demoted_by=user.id)
    return {"worker": slug, "autonomy": row.autonomy, "trust_level": row.trust_level}
