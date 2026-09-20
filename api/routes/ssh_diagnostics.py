"""HTTP API for structured SSH diagnostics and remote workflow."""
from __future__ import annotations

from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from api.routes.auth import get_current_user
from core.database import get_db
from governance.tenant_store import ensure_personal_tenant
from governance.ssh_diagnostics import run_diagnostic, list_diagnostic_capabilities
from governance.ssh_remote_workflow import run_remote_diagnostic_workflow
from governance import ssh_cancel

router = APIRouter()


class DiagBody(BaseModel):
    capability: str
    unit: Optional[str] = None
    container: Optional[str] = None
    lines: int = Field(default=50, ge=1, le=200)


class WorkflowBody(BaseModel):
    objective: str = Field(..., max_length=2000)
    user_confirmed: bool = False
    approved_remediation_commands: Optional[List[str]] = None
    job_id: Optional[str] = None


class CancelBody(BaseModel):
    reason: str = "user_cancelled"


@router.get("/diagnostics/capabilities")
async def diag_caps(request: Request, db=Depends(get_db)):
    await get_current_user(request, db)
    return {"capabilities": list_diagnostic_capabilities()}


@router.post("/connections/{connection_id}/diagnostics")
async def run_diag(connection_id: str, body: DiagBody, request: Request, db=Depends(get_db)):
    user = await get_current_user(request, db)
    await ensure_personal_tenant(db, user)
    try:
        r = await run_diagnostic(
            owner_id=user.id,
            connection_id=connection_id,
            capability=body.capability,
            unit=body.unit,
            container=body.container,
            lines=body.lines,
            actor="user",
        )
    except Exception:
        raise HTTPException(400, "diagnostic_failed")
    if r.status == "denied":
        raise HTTPException(403, "diagnostic_denied")
    return r.to_public()


@router.post("/connections/{connection_id}/workflow")
async def run_workflow(connection_id: str, body: WorkflowBody, request: Request, db=Depends(get_db)):
    user = await get_current_user(request, db)
    await ensure_personal_tenant(db, user)
    report = await run_remote_diagnostic_workflow(
        owner_id=user.id,
        connection_id=connection_id,
        objective=body.objective,
        actor="user",
        user_confirmed=body.user_confirmed,
        approved_remediation_commands=body.approved_remediation_commands,
        job_id=body.job_id,
    )
    return report.to_public()


@router.post("/jobs/{job_id}/cancel")
async def cancel_job(job_id: str, body: CancelBody, request: Request, db=Depends(get_db)):
    await get_current_user(request, db)
    ssh_cancel.request_cancel(job_id, reason=body.reason)
    return ssh_cancel.cancel_status_result(job_id=job_id)
