"""
API — Workflow route (Agency OS Master Plan §6).

CRUD for workflow definitions, plus import/export in YAML, JSON, and
natural-language formats. All workflows compile to UCIP ExecutionPlan format.

Database (WorkflowRecord) is the source of truth. Runtime objects are hydrated
per-request from the store.
"""
import json
from fastapi import APIRouter, Depends, Request, Query, HTTPException
from pydantic import BaseModel, Field, field_validator
from typing import Optional

from api.routes.auth import get_current_user
from governance.tenant_store import ensure_personal_tenant
from api.deps import tenant_ctx

from core.database import get_db
from core.sanitize import sanitize_name, sanitize_freeform, sanitize_name_list
from brain.workflow import (
    Workflow, WorkflowStep, StepType,
)
from brain import workflow_store

router = APIRouter(prefix="/api/workflows", tags=["workflows"])


class WorkflowStepCreate(BaseModel):
    id: str
    type: str = "capability"
    name: str = ""
    description: str = ""
    capability: Optional[str] = None
    inputs: dict = {}
    outputs: dict = {}
    condition: Optional[str] = None
    branches: dict[str, str] = {}
    next_step: Optional[str] = None
    on_error: Optional[str] = None
    timeout_s: int = 300
    retry: int = 0
    metadata: dict = {}

    @field_validator("name", mode="before")
    @classmethod
    def _sanitize_name(cls, value):
        return sanitize_name(value)

    @field_validator("description", mode="before")
    @classmethod
    def _sanitize_description(cls, value):
        return sanitize_freeform(value)


class WorkflowCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    description: str = Field(default="", max_length=2000)
    version: str = Field(default="1.0.0")
    steps: list[WorkflowStepCreate] = []
    start_step: Optional[str] = None
    triggers: list[str] = ["manual"]
    schedule: Optional[str] = None
    tags: list[str] = []
    metadata: dict = {}
    # Reject client ownership forgery — ignored if present
    owner_id: Optional[str] = None
    tenant_id: Optional[str] = None
    user_id: Optional[str] = None

    @field_validator("name", mode="before")
    @classmethod
    def _sanitize_name(cls, value):
        return sanitize_name(value)

    @field_validator("description", mode="before")
    @classmethod
    def _sanitize_description(cls, value):
        return sanitize_freeform(value)

    @field_validator("tags", mode="before")
    @classmethod
    def _sanitize_tags(cls, value):
        return sanitize_name_list(value)


class WorkflowUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    version: Optional[str] = None
    steps: Optional[list[WorkflowStepCreate]] = None
    start_step: Optional[str] = None
    triggers: Optional[list[str]] = None
    schedule: Optional[str] = None
    tags: Optional[list[str]] = None
    metadata: Optional[dict] = None
    status: Optional[str] = None
    owner_id: Optional[str] = None
    tenant_id: Optional[str] = None
    user_id: Optional[str] = None

    @field_validator("name", mode="before")
    @classmethod
    def _sanitize_name(cls, value):
        return sanitize_name(value)

    @field_validator("description", mode="before")
    @classmethod
    def _sanitize_description(cls, value):
        return sanitize_freeform(value)

    @field_validator("tags", mode="before")
    @classmethod
    def _sanitize_tags(cls, value):
        return sanitize_name_list(value) if value is not None else value


class WorkflowImport(BaseModel):
    format: str = Field(default="yaml")
    content: str = Field(..., min_length=1)
    name: Optional[str] = None


def _steps_from_req(steps: list[WorkflowStepCreate]) -> list[WorkflowStep]:
    out = []
    for s in steps:
        out.append(WorkflowStep(
            id=s.id,
            type=StepType(s.type),
            name=s.name,
            description=s.description,
            capability=s.capability,
            inputs=s.inputs,
            outputs=s.outputs,
            condition=s.condition,
            branches=s.branches,
            next_step=s.next_step,
            on_error=s.on_error,
            timeout_s=s.timeout_s,
            retry=s.retry,
            metadata=s.metadata,
        ))
    return out


def _api_dict(wf: Workflow) -> dict:
    d = wf.to_dict()
    if hasattr(wf, "revision"):
        d["revision"] = getattr(wf, "revision")
    if hasattr(wf, "enabled"):
        d["enabled"] = getattr(wf, "enabled")
    return d


@router.get("")
async def list_workflows(
    request: Request,
    status: Optional[str] = Query(None),
    tag: Optional[str] = Query(None),
    db=Depends(get_db),
):
    user = await get_current_user(request, db)
    await ensure_personal_tenant(db, user)
    tags = [tag] if tag else None
    workflows = await workflow_store.list_workflows_for_owner(
        db, user.id, status=status, tags=tags
    )
    return {
        "workflows": [_api_dict(w) for w in workflows],
        "count": len(workflows),
    }


@router.get("/jobs/{job_id}")
async def get_workflow_job(job_id: str, request: Request, db=Depends(get_db)):
    """Owner-scoped job inspection. Returns identity + version, not secrets."""
    from core.database import ExecutionJob
    from sqlalchemy import select

    user = await get_current_user(request, db)
    await ensure_personal_tenant(db, user)
    r = await db.execute(
        select(ExecutionJob).where(
            ExecutionJob.id == job_id,
            ExecutionJob.owner_id == user.id,
        )
    )
    job = r.scalar_one_or_none()
    if not job:
        raise HTTPException(404, "Job not found")
    snap = (job.payload or {}).get("workflow_snapshot") if isinstance(job.payload, dict) else None
    return {
        "job_id": job.id,
        "status": job.status,
        "job_type": job.job_type,
        "workflow_id": job.workflow_id,
        "workflow_version": job.workflow_version,
        "has_snapshot": bool(snap),
        "snapshot_version": (snap or {}).get("version") if isinstance(snap, dict) else None,
        "correlation": job.correlation,
        "attempts": job.attempts,
        "created_at": job.created_at.isoformat() if job.created_at else None,
    }


@router.post("")
async def create_workflow_route(req: WorkflowCreate, request: Request, db=Depends(get_db)):
    user = await get_current_user(request, db)
    tenant = await ensure_personal_tenant(db, user)

    import uuid
    workflow = Workflow(
        workflow_id=str(uuid.uuid4()),
        name=req.name,
        description=req.description,
        version=req.version,
        start_step=req.start_step,
        triggers=req.triggers,
        schedule=req.schedule,
        tags=req.tags,
        metadata={k: v for k, v in (req.metadata or {}).items()
                  if k not in ("owner_id", "tenant_id", "user_id", "trust_level", "extra_caps")},
        owner_id=user.id,
    )
    workflow.steps = _steps_from_req(req.steps)
    if workflow.steps and not workflow.start_step:
        workflow.start_step = workflow.steps[0].id

    valid, errors = workflow.validate()
    if not valid:
        raise HTTPException(400, detail=f"Invalid workflow: {'; '.join(errors)}")

    saved = await workflow_store.create_workflow_record(
        db, owner_id=user.id, workflow=workflow, tenant_id=tenant.id
    )
    return {
        "workflow": _api_dict(saved),
        "yaml": saved.to_yaml(),
        "ucip_plan": saved.to_ucip_plan(),
    }


@router.get("/{workflow_id}")
async def get_workflow(workflow_id: str, request: Request, db=Depends(get_db)):
    user = await get_current_user(request, db)
    await ensure_personal_tenant(db, user)
    workflow = await workflow_store.get_workflow_for_owner(db, workflow_id, user.id)
    if not workflow:
        raise HTTPException(404, f"Workflow not found: {workflow_id}")
    return {"workflow": _api_dict(workflow)}


@router.patch("/{workflow_id}")
async def update_workflow(workflow_id: str, req: WorkflowUpdate,
                          request: Request, db=Depends(get_db)):
    user = await get_current_user(request, db)
    await ensure_personal_tenant(db, user)
    workflow = await workflow_store.get_workflow_for_owner(db, workflow_id, user.id)
    if not workflow:
        raise HTTPException(404, f"Workflow not found: {workflow_id}")

    if req.name is not None:
        workflow.name = req.name
    if req.description is not None:
        workflow.description = req.description
    if req.version is not None:
        workflow.version = req.version
    if req.start_step is not None:
        workflow.start_step = req.start_step
    if req.triggers is not None:
        workflow.triggers = req.triggers
    if req.schedule is not None:
        workflow.schedule = req.schedule
    if req.tags is not None:
        workflow.tags = req.tags
    if req.metadata is not None:
        workflow.metadata = {k: v for k, v in req.metadata.items()
                             if k not in ("owner_id", "tenant_id", "user_id", "trust_level")}
    if req.status is not None:
        workflow.status = req.status
    if req.steps is not None:
        workflow.steps = _steps_from_req(req.steps)

    valid, errors = workflow.validate()
    if not valid:
        raise HTTPException(400, detail=f"Invalid workflow: {'; '.join(errors)}")

    saved = await workflow_store.update_workflow_for_owner(
        db, workflow_id, user.id, workflow
    )
    if not saved:
        raise HTTPException(404, f"Workflow not found: {workflow_id}")
    return {"workflow": _api_dict(saved)}


@router.delete("/{workflow_id}")
async def delete_workflow(workflow_id: str, request: Request, db=Depends(get_db)):
    user = await get_current_user(request, db)
    await ensure_personal_tenant(db, user)
    ok = await workflow_store.delete_workflow_for_owner(db, workflow_id, user.id)
    if not ok:
        raise HTTPException(404, f"Workflow not found: {workflow_id}")
    return {"deleted": True}


@router.get("/{workflow_id}/export")
async def export_workflow(workflow_id: str, request: Request,
                          format: str = Query("yaml"),
                          db=Depends(get_db)):
    user = await get_current_user(request, db)
    await ensure_personal_tenant(db, user)
    workflow = await workflow_store.get_workflow_for_owner(db, workflow_id, user.id)
    if not workflow:
        raise HTTPException(404, f"Workflow not found: {workflow_id}")
    # Export definition without owner authority fields
    payload = workflow.to_dict()
    payload.pop("owner_id", None)
    if format == "json":
        return {"format": "json", "content": json.dumps(payload, indent=2)}
    import yaml
    return {"format": "yaml", "content": yaml.dump(payload, default_flow_style=False, sort_keys=False)}


@router.get("/{workflow_id}/ucip")
async def workflow_ucip(workflow_id: str, request: Request, db=Depends(get_db)):
    user = await get_current_user(request, db)
    await ensure_personal_tenant(db, user)
    workflow = await workflow_store.get_workflow_for_owner(db, workflow_id, user.id)
    if not workflow:
        raise HTTPException(404, f"Workflow not found: {workflow_id}")
    return {"ucip_plan": workflow.to_ucip_plan()}


@router.post("/import")
async def import_workflow(req: WorkflowImport, request: Request, db=Depends(get_db)):
    user = await get_current_user(request, db)
    tenant = await ensure_personal_tenant(db, user)

    if req.format == "yaml":
        import yaml
        data = yaml.safe_load(req.content)
    elif req.format == "json":
        data = json.loads(req.content)
    else:
        raise HTTPException(400, f"Unsupported format: {req.format}")

    data = workflow_store.sanitize_import_dict(data if isinstance(data, dict) else {})
    if req.name:
        data["name"] = req.name
    if "name" not in data:
        raise HTTPException(400, "Imported workflow requires a name")

    import uuid
    data["workflow_id"] = str(uuid.uuid4())
    workflow = Workflow.from_dict(data)
    workflow.owner_id = user.id

    valid, errors = workflow.validate()
    if not valid:
        raise HTTPException(400, detail=f"Invalid workflow: {'; '.join(errors)}")

    saved = await workflow_store.create_workflow_record(
        db, owner_id=user.id, workflow=workflow, tenant_id=tenant.id
    )
    return {"workflow": _api_dict(saved)}


class WorkflowExecuteReq(BaseModel):
    idempotency_key: Optional[str] = None
    request_id: Optional[str] = None


@router.post("/{workflow_id}/execute")
async def execute_workflow(
    workflow_id: str,
    request: Request,
    db=Depends(get_db),
    body: Optional[WorkflowExecuteReq] = None,
):
    """Create a durable ExecutionJob bound to an immutable workflow snapshot.

    Flow:
      1. Owner-scoped load + snapshot of WorkflowRecord (DB source of truth)
      2. Scrub secrets from snapshot
      3. Enqueue ExecutionJob with workflow_id/version + payload.workflow_snapshot
      4. Record lightweight EvidenceRecord for audit identity

    Governance remains authoritative at step execution time — enabling a
    workflow is not an authority grant. Retries must use payload.workflow_snapshot
    and must NOT reload the current WorkflowRecord definition.
    """
    from workers.job_queue import enqueue
    from governance.reliability import new_request_id, scrub_secrets
    from core.database import EvidenceRecord, gen_id as _gen_id
    from datetime import datetime, timezone

    user = await get_current_user(request, db)
    tenant = await ensure_personal_tenant(db, user)
    snap = await workflow_store.snapshot_workflow_for_execution(db, workflow_id, user.id)
    if not snap:
        raise HTTPException(404, f"Workflow not found: {workflow_id}")
    if not snap.get("enabled", True):
        raise HTTPException(400, "Workflow is disabled")

    body = body or WorkflowExecuteReq()
    correlation_id = body.request_id or new_request_id()
    version = int(snap.get("version") or 1)

    correlation = scrub_secrets({
        "correlation_id": correlation_id,
        "workflow_id": snap["workflow_id"],
        "workflow_version": version,
        "owner_id": user.id,
        "tenant_id": tenant.id,
    })
    payload = scrub_secrets({
        "workflow_snapshot": snap,
        "workflow_id": snap["workflow_id"],
        "workflow_version": version,
    })

    job = await enqueue(
        owner_id=user.id,
        tenant_id=tenant.id,
        job_type="workflow",
        payload=payload,
        actor_id=user.id,
        idempotency_key=body.idempotency_key,
        request_id=correlation_id,
        correlation=correlation,
        workflow_id=snap["workflow_id"],
        workflow_version=version,
    )

    # Durable evidence identity (does not cascade on workflow delete)
    try:
        ev = EvidenceRecord(
            id=_gen_id(),
            owner_id=user.id,
            tenant_id=tenant.id,
            goal=f"workflow:{snap['workflow_id']}:v{version}",
            body=scrub_secrets({
                "kind": "workflow_execution",
                "workflow_id": snap["workflow_id"],
                "workflow_version": version,
                "job_id": job.id,
                "correlation_id": correlation_id,
                "owner_id": user.id,
                "tenant_id": tenant.id,
            }),
            created_at=datetime.now(timezone.utc),
        )
        db.add(ev)
        await db.commit()
        evidence_id = ev.id
    except Exception:
        evidence_id = None
        try:
            await db.rollback()
        except Exception:
            pass

    return {
        "status": "queued" if job.status == "queued" else job.status,
        "workflow_id": snap["workflow_id"],
        "workflow_version": version,
        "job_id": job.id,
        "correlation_id": correlation_id,
        "evidence_id": evidence_id,
        "note": "Job payload holds immutable workflow_snapshot; governance applies at step execution.",
    }


