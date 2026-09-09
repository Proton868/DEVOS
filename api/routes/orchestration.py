"""Nuha orchestration API — plan (no side effects) and run (via existing agent runtime)."""
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from typing import Optional

from core.database import get_db
from api.routes.auth import get_current_user
from governance.tenant_store import ensure_personal_tenant
from brain.orchestration import (
    create_plan,
    execute_plan,
    get_plan,
    get_plan_durable,
    list_plans_for_user,
    request_cancel,
    detect_mode,
    NuhaMode,
)
from brain.orchestration_store import list_user_plans as durable_list_plans

router = APIRouter()


class PlanReq(BaseModel):
    goal: str = Field(..., min_length=1)
    workspace_id: Optional[str] = "default"
    persona_id: Optional[str] = "nuha"


class RunReq(BaseModel):
    goal: Optional[str] = None
    workspace_id: Optional[str] = "default"
    plan_id: Optional[str] = None
    persona_id: Optional[str] = "nuha"
    background: bool = False  # if True: return execution_id immediately, run in background


@router.post("/plan")
async def orchestration_plan(req: PlanReq, request: Request, db=Depends(get_db)):
    """Plan Mode: produce PLAN_READY. No writes / no execution side effects."""
    user = await get_current_user(request, db)
    await ensure_personal_tenant(db, user)
    plan = await create_plan(
        user_id=user.id,
        goal=req.goal.strip(),
        workspace_id=req.workspace_id or "default",
        persona_id=req.persona_id or "nuha",
    )
    return {
        "plan_id": plan.id,
        "mode": NuhaMode.PLAN.value,
        "status": plan.status,
        "plan": plan.to_dict(),
    }


@router.post("/run")
async def orchestration_run(req: RunReq, request: Request, db=Depends(get_db)):
    """Action Mode: authorize via UCIP path then existing Agent Runtime."""
    user = await get_current_user(request, db)
    await ensure_personal_tenant(db, user)

    plan = None
    if req.plan_id:
        plan = await get_plan_durable(req.plan_id)
        if not plan or plan.user_id != user.id:
            raise HTTPException(404, "plan not found")
    else:
        if not req.goal or not req.goal.strip():
            raise HTTPException(400, "goal or plan_id required")
        plan = await create_plan(
            user_id=user.id,
            goal=req.goal.strip(),
            workspace_id=req.workspace_id or "default",
            persona_id=req.persona_id or "nuha",
        )

    # Optional async handoff: persist + background execute, return execution_id immediately
    background = bool(getattr(req, "background", False))
    if request.query_params.get("background") in ("1", "true", "yes"):
        background = True

    if background:
        import asyncio
        from brain.orchestration_store import persist_plan
        await persist_plan(plan)
        plan.emit("execution.created", {"background": True})
        plan.emit("execution.started", {})

        async def _bg():
            try:
                await execute_plan(plan)
            except Exception as e:
                plan.emit("execution.failed", {"error": str(e)[:300]})
                try:
                    await persist_plan(plan)
                except Exception:
                    pass

        asyncio.create_task(_bg())
        return {
            "plan_id": plan.id,
            "execution_id": plan.id,
            "mode": NuhaMode.ACTION.value,
            "status": plan.status,
            "background": True,
            "stream": f"/api/orchestration/{plan.id}/events",
            "plan": plan.to_dict(),
        }

    plan = await execute_plan(plan)
    return {
        "plan_id": plan.id,
        "execution_id": plan.id,
        "mode": NuhaMode.ACTION.value,
        "status": plan.status,
        "background": False,
        "plan": plan.to_dict(),
    }


@router.get("/{plan_id}")
async def orchestration_get(plan_id: str, request: Request, db=Depends(get_db)):
    user = await get_current_user(request, db)
    await ensure_personal_tenant(db, user)
    plan = await get_plan_durable(plan_id)
    if not plan or plan.user_id != user.id:
        raise HTTPException(404, "plan not found")
    return plan.to_dict()


@router.get("/{plan_id}/events")
async def orchestration_events(
    plan_id: str,
    request: Request,
    db=Depends(get_db),
    after: int = 0,
):
    """Replay-friendly event list. Use ?after=N to fetch events with sequence > N."""
    user = await get_current_user(request, db)
    await ensure_personal_tenant(db, user)
    plan = get_plan(plan_id)
    if plan is None:
        plan = await get_plan_durable(plan_id)
    if not plan or plan.user_id != user.id:
        raise HTTPException(404, "plan not found")
    events = list(getattr(plan, "events", None) or [])
    # Ensure sequence numbers for legacy events
    out = []
    for i, e in enumerate(events):
        if not isinstance(e, dict):
            continue
        seq = int(e.get("sequence") or (i + 1))
        if seq <= after:
            continue
        if "sequence" not in e:
            e = {**e, "sequence": seq, "event_id": e.get("event_id") or f"{plan_id}:{seq}"}
        out.append(e)
    return {
        "plan_id": plan_id,
        "execution_id": plan_id,
        "status": getattr(plan, "status", None),
        "after": after,
        "events": out,
    }


@router.post("/{plan_id}/cancel")
async def orchestration_cancel(plan_id: str, request: Request, db=Depends(get_db)):
    user = await get_current_user(request, db)
    await ensure_personal_tenant(db, user)
    plan = await get_plan_durable(plan_id)
    if not plan or plan.user_id != user.id:
        raise HTTPException(404, "plan not found")
    plan = request_cancel(plan_id)
    return plan.to_dict() if plan else {"status": "not_found"}




@router.post("/{plan_id}/resume")
async def orchestration_resume(plan_id: str, request: Request, db=Depends(get_db)):
    """Reconcile durable state and resume incomplete mission nodes."""
    user = await get_current_user(request, db)
    await ensure_personal_tenant(db, user)
    plan = await get_plan_durable(plan_id)
    if not plan or plan.user_id != user.id:
        raise HTTPException(404, "plan not found")
    from execution.durable_resume import resume_plan
    return await resume_plan(plan_id)

@router.get("")
async def orchestration_list(request: Request, db=Depends(get_db)):
    user = await get_current_user(request, db)
    await ensure_personal_tenant(db, user)
    mem = [p.to_dict() for p in list_plans_for_user(user.id)]
    try:
        durable = await durable_list_plans(user.id)
        seen = {p.get("id") for p in mem}
        for d in durable:
            if d.get("id") not in seen:
                mem.append(d)
    except Exception:
        pass
    return {"plans": mem}


@router.post("/detect-mode")
async def orchestration_detect_mode(req: PlanReq, request: Request, db=Depends(get_db)):
    user = await get_current_user(request, db)
    await ensure_personal_tenant(db, user)
    mode = detect_mode(req.goal)
    return {"mode": mode.value, "goal": req.goal}
