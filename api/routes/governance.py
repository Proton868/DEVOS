"""Governance API routes — HITL, audit, observability, UCIP dashboard."""

from fastapi import APIRouter, Depends, Request, HTTPException
from pydantic import BaseModel
from typing import Optional
from core.database import get_db
from api.routes.auth import get_current_user

router = APIRouter()


# ── HITL Endpoints ────────────────────────────────────────────────────────────

@router.get("/hitl/pending")
async def get_pending_hitl(request: Request, db=Depends(get_db)):
    user = await get_current_user(request, db)
    from governance.hitl import HITLQueue
    return {"requests": HITLQueue().get_pending(user_id=user.id)}


@router.post("/hitl/{request_id}/approve")
async def approve_hitl(request_id: str, request: Request, db=Depends(get_db)):
    user = await get_current_user(request, db)
    from governance.hitl import HITLQueue
    result = HITLQueue().resolve(request_id, approved=True, resolved_by=user.id)
    if not result:
        raise HTTPException(404, "HITL request not found")
    return {"status": "approved", "id": request_id}


@router.post("/hitl/{request_id}/deny")
async def deny_hitl(request_id: str, request: Request, db=Depends(get_db)):
    user = await get_current_user(request, db)
    from governance.hitl import HITLQueue
    result = HITLQueue().resolve(request_id, approved=False, resolved_by=user.id)
    if not result:
        raise HTTPException(404, "HITL request not found")
    return {"status": "denied", "id": request_id}


@router.get("/hitl/history")
async def hitl_history(request: Request, db=Depends(get_db)):
    user = await get_current_user(request, db)
    from governance.hitl import HITLQueue
    return {"history": HITLQueue().get_history(user_id=user.id)}


@router.get("/hitl/stats")
async def hitl_stats(request: Request, db=Depends(get_db)):
    await get_current_user(request, db)
    from governance.hitl import HITLQueue
    return HITLQueue().stats()


# ── Audit Log ─────────────────────────────────────────────────────────────────

@router.get("/audit")
async def get_audit_log(request: Request, limit: int = 100, db=Depends(get_db)):
    user = await get_current_user(request, db)
    from governance.ucip import UCIPGateway
    log = UCIPGateway.get_audit_log(limit=limit)
    return {"log": log, "stats": UCIPGateway.audit_stats()}


# ── Observability / Traces ────────────────────────────────────────────────────

@router.get("/traces")
async def list_traces(request: Request, limit: int = 50, db=Depends(get_db)):
    user = await get_current_user(request, db)
    from governance.observability import ObservabilityStore
    traces = ObservabilityStore().list_traces(task_id=None, limit=limit)
    return {"traces": traces}


@router.get("/traces/{trace_id}")
async def get_trace(trace_id: str, request: Request, db=Depends(get_db)):
    await get_current_user(request, db)
    from governance.observability import ObservabilityStore
    return ObservabilityStore().replay_trace(trace_id)


@router.get("/metrics")
async def get_metrics(request: Request, db=Depends(get_db)):
    user = await get_current_user(request, db)
    from governance.observability import ObservabilityStore
    from governance.ucip import UCIPGateway
    from governance.hitl import HITLQueue
    from governance.ratelimit import RateLimiter
    return {
        "loop_metrics":  ObservabilityStore().metrics(),
        "audit_stats":   UCIPGateway.audit_stats(),
        "hitl_stats":    HITLQueue().stats(),
        "rate_limiter":  RateLimiter().stats(),
    }


# ── UCIP Identity ─────────────────────────────────────────────────────────────

@router.get("/ucip/identity")
async def get_agent_identity(session_id: str, request: Request, db=Depends(get_db)):
    user = await get_current_user(request, db)
    from governance.ucip import AgentIdentity, TrustLevel
    agent = AgentIdentity.create(user.id, session_id, TrustLevel.OPERATOR)
    return agent.to_dict()


@router.get("/ucip/capabilities/{trust_level}")
async def get_capabilities(trust_level: str, request: Request, db=Depends(get_db)):
    await get_current_user(request, db)
    from governance.ucip import TrustLevel, TRUST_LEVEL_CAPS, HITL_REQUIRED_CAPS
    try:
        tl = TrustLevel.from_str(trust_level)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    caps = TRUST_LEVEL_CAPS.get(tl, set())
    return {
        "trust_level": tl.name,
        "capabilities": sorted(caps),
        "hitl_required": sorted(HITL_REQUIRED_CAPS & caps),
    }


# ── Tool Contracts ────────────────────────────────────────────────────────────

@router.get("/tools")
async def list_tool_contracts(request: Request, db=Depends(get_db)):
    await get_current_user(request, db)
    from governance.tool_contracts import ToolValidator
    return {"tools": ToolValidator.list_tools()}


# ── Checkpoints ───────────────────────────────────────────────────────────────

@router.get("/checkpoints")
async def list_checkpoints(request: Request, db=Depends(get_db)):
    await get_current_user(request, db)
    from governance.checkpoint import CheckpointManager
    return {"checkpoints": CheckpointManager().list_incomplete()}
