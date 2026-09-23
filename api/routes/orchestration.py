"""Nuha orchestration API — plan (no side effects) and run (via existing agent runtime)."""
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
import asyncio
import json
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



@router.get("/{plan_id}/stream")
async def orchestration_stream(
    plan_id: str,
    request: Request,
    db=Depends(get_db),
    after: int = 0,
):
    """SSE stream of durable plan events with ownership + replay from sequence."""
    user = await get_current_user(request, db)
    from governance.request_identity import get_tenant_context
    from governance.ucip import TrustLevel
    from governance.world_execution import assert_sse_world, WorldBoundaryError
    tctx = await get_tenant_context(request, db, user, trust=TrustLevel.OPERATOR)
    plan = get_plan(plan_id)
    if plan is None:
        plan = await get_plan_durable(plan_id)
    if not plan or plan.user_id != user.id:
        raise HTTPException(404, "plan not found")
    # World-scope SSE: plan must not belong to another world
    plan_resource = {
        "tenant_id": getattr(plan, "tenant_id", None) or tctx.world.world_id,
        "user_id": plan.user_id,
        "owner_id": plan.user_id,
    }
    try:
        assert_sse_world(tctx.world, plan_resource, resource_name="orchestration_stream")
    except WorldBoundaryError:
        raise HTTPException(404, "plan not found")

    async def event_gen():
        last = after
        terminal = {
            "completed", "failed", "cancelled", "canceled", "blocked", "denied",
        }
        open_evt = {"type": "stream.open", "execution_id": plan_id, "after": after}
        yield "data: " + json.dumps(open_evt) + "\n\n"
        while True:
            if await request.is_disconnected():
                break
            pl = get_plan(plan_id)
            if pl is None:
                pl = await get_plan_durable(plan_id)
            if pl is None:
                yield "data: " + json.dumps({"type": "stream.error", "error": "plan_missing"}) + "\n\n"
                break
            events = list(getattr(pl, "events", None) or [])
            for i, e in enumerate(events):
                if not isinstance(e, dict):
                    continue
                seq = int(e.get("sequence") or (i + 1))
                if seq <= last:
                    continue
                last = seq
                payload = {
                    "type": e.get("type") or "event",
                    "sequence": seq,
                    "event_id": e.get("event_id") or f"{plan_id}:{seq}",
                    "execution_id": plan_id,
                    "mission_id": plan_id,
                    "payload": e.get("payload") or e.get("data") or {},
                    "timestamp": e.get("timestamp") or e.get("at"),
                    "plan_status": getattr(pl, "status", None),
                }
                yield "data: " + json.dumps(payload) + "\n\n"
            st = (getattr(pl, "status", None) or "").lower()
            if st in terminal:
                term = {
                    "type": "stream.terminal",
                    "status": st,
                    "execution_id": plan_id,
                    "last_seq": last,
                }
                yield "data: " + json.dumps(term) + "\n\n"
                break
            yield f": heartbeat last_seq={last}\n\n"
            await asyncio.sleep(2.0)

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


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


class HitlDecisionReq(BaseModel):
    decision: str  # APPROVED | DENIED


@router.get("/hitl/pending")
async def hitl_pending(request: Request, db=Depends(get_db)):
    user = await get_current_user(request, db)
    await ensure_personal_tenant(db, user)
    from brain.hitl_store import list_pending_for_user
    return {"approvals": list_pending_for_user(user.id)}


@router.post("/hitl/{approval_id}/decide")
async def hitl_decide(approval_id: str, req: HitlDecisionReq, request: Request, db=Depends(get_db)):
    user = await get_current_user(request, db)
    await ensure_personal_tenant(db, user)
    from brain.hitl_store import get_approval, decide_approval
    rec = get_approval(approval_id)
    if not rec or rec.get("user_id") != user.id:
        raise HTTPException(404, "approval not found")
    try:
        out = decide_approval(approval_id, decision=req.decision, decided_by=user.id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    # Resume same plan when approved — never restart a new mission
    if out and (out.get("status") == "APPROVED"):
        plan_id = out.get("execution_id") or out.get("mission_id")
        node_id = out.get("node_id")
        if plan_id:
            try:
                from brain.orchestration import get_plan, get_plan_durable
                plan = get_plan(plan_id) or await get_plan_durable(plan_id)
                if plan and plan.user_id == user.id:
                    if (plan.status or "").lower() in ("waiting_for_user", "awaiting_approval"):
                        plan.status = "authorized"
                        plan.emit("hitl.approved", {
                            "approval_id": approval_id,
                            "node_id": node_id,
                        })
                    for n in getattr(plan, "nodes", None) or []:
                        if node_id and getattr(n, "id", None) == node_id:
                            if (getattr(n, "status", "") or "").lower() in (
                                "awaiting_approval", "waiting_for_user"
                            ):
                                n.status = "authorized"
                    try:
                        from brain.orchestration_store import persist_plan
                        await persist_plan(plan)
                    except Exception:
                        pass
                from execution.durable_resume import resume_plan
                await resume_plan(plan_id)
            except Exception:
                pass
    elif out and (out.get("status") == "DENIED"):
        plan_id = out.get("execution_id") or out.get("mission_id")
        if plan_id:
            try:
                from brain.orchestration import get_plan, get_plan_durable
                plan = get_plan(plan_id) or await get_plan_durable(plan_id)
                if plan and plan.user_id == user.id:
                    plan.emit("hitl.denied", {"approval_id": approval_id})
                    # Do not force-complete; leave blocked/waiting semantics to mission_truth
                    if (plan.status or "").lower() in ("waiting_for_user", "awaiting_approval"):
                        plan.status = "blocked"
                    try:
                        from brain.orchestration_store import persist_plan
                        await persist_plan(plan)
                    except Exception:
                        pass
            except Exception:
                pass
    return out or {}
