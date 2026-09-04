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
    list_plans_for_user,
    request_cancel,
    detect_mode,
    NuhaMode,
)

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
        plan = get_plan(req.plan_id)
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

    plan = await execute_plan(plan)
    return {
        "plan_id": plan.id,
        "mode": NuhaMode.ACTION.value,
        "status": plan.status,
        "plan": plan.to_dict(),
    }


@router.get("/{plan_id}")
async def orchestration_get(plan_id: str, request: Request, db=Depends(get_db)):
    user = await get_current_user(request, db)
    await ensure_personal_tenant(db, user)
    plan = get_plan(plan_id)
    if not plan or plan.user_id != user.id:
        raise HTTPException(404, "plan not found")
    return plan.to_dict()


@router.get("/{plan_id}/events")
async def orchestration_events(plan_id: str, request: Request, db=Depends(get_db)):
    user = await get_current_user(request, db)
    await ensure_personal_tenant(db, user)
    plan = get_plan(plan_id)
    if not plan or plan.user_id != user.id:
        raise HTTPException(404, "plan not found")
    return {"plan_id": plan_id, "events": plan.events}


@router.post("/{plan_id}/cancel")
async def orchestration_cancel(plan_id: str, request: Request, db=Depends(get_db)):
    user = await get_current_user(request, db)
    await ensure_personal_tenant(db, user)
    plan = get_plan(plan_id)
    if not plan or plan.user_id != user.id:
        raise HTTPException(404, "plan not found")
    plan = request_cancel(plan_id)
    return plan.to_dict() if plan else {"status": "not_found"}


@router.get("")
async def orchestration_list(request: Request, db=Depends(get_db)):
    user = await get_current_user(request, db)
    await ensure_personal_tenant(db, user)
    plans = list_plans_for_user(user.id)
    return {"plans": [p.to_dict() for p in plans]}


@router.post("/detect-mode")
async def orchestration_detect_mode(req: PlanReq, request: Request, db=Depends(get_db)):
    user = await get_current_user(request, db)
    await ensure_personal_tenant(db, user)
    mode = detect_mode(req.goal)
    return {"mode": mode.value, "goal": req.goal}
