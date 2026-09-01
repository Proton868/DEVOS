"""
API — Workflow route (Agency OS Master Plan §6).

CRUD for workflow definitions, plus import/export in YAML, JSON, and
natural-language formats. All workflows compile to UCIP ExecutionPlan format.
"""
import json
from fastapi import APIRouter, Depends, Request, Query, HTTPException
from pydantic import BaseModel, Field, field_validator
from typing import Optional

from api.routes.auth import get_current_user
from core.database import get_db
from core.sanitize import sanitize_name, sanitize_freeform, sanitize_name_list
from brain.workflow import (
    Workflow, WorkflowStep, StepType, WorkflowEngine,
    get_workflow_engine, create_workflow,
)

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

    # security-audit P3f
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
    """Import a workflow from YAML or JSON."""
    format: str = Field(default="yaml")
    content: str = Field(..., min_length=1)
    name: Optional[str] = None


@router.get("")
async def list_workflows(
    request: Request,
    status: Optional[str] = Query(None),
    tag: Optional[str] = Query(None),
    db=Depends(get_db),
):
    """List all workflows, optionally filtered."""
    await get_current_user(request, db)
    engine = get_workflow_engine()
    tags = [tag] if tag else None
    workflows = engine.list_all(status=status, tags=tags)
    return {
        "workflows": [w.to_dict() for w in workflows],
        "count": len(workflows),
    }


@router.post("")
async def create_workflow_route(req: WorkflowCreate, request: Request, db=Depends(get_db)):
    """Create a new workflow."""
    await get_current_user(request, db)

    workflow = create_workflow(
        name=req.name,
        description=req.description,
        triggers=req.triggers,
    )
    workflow.version = req.version
    workflow.start_step = req.start_step
    workflow.schedule = req.schedule
    workflow.tags = req.tags
    workflow.metadata = req.metadata

    for s in req.steps:
        workflow.steps.append(WorkflowStep(
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

    valid, errors = workflow.validate()
    if not valid:
        raise HTTPException(400, detail=f"Invalid workflow: {'; '.join(errors)}")

    engine = get_workflow_engine()
    engine.store(workflow)

    return {
        "workflow": workflow.to_dict(),
        "yaml": workflow.to_yaml(),
        "ucip_plan": workflow.to_ucip_plan(),
    }


@router.get("/{workflow_id}")
async def get_workflow(workflow_id: str, request: Request, db=Depends(get_db)):
    """Get a workflow by ID."""
    await get_current_user(request, db)
    engine = get_workflow_engine()
    workflow = engine.load(workflow_id)
    if not workflow:
        raise HTTPException(404, f"Workflow not found: {workflow_id}")
    return {"workflow": workflow.to_dict()}


@router.patch("/{workflow_id}")
async def update_workflow(workflow_id: str, req: WorkflowUpdate,
                          request: Request, db=Depends(get_db)):
    """Update an existing workflow."""
    await get_current_user(request, db)
    engine = get_workflow_engine()
    workflow = engine.load(workflow_id)
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
        workflow.metadata = req.metadata
    if req.status is not None:
        workflow.status = req.status

    if req.steps is not None:
        workflow.steps = []
        for s in req.steps:
            workflow.steps.append(WorkflowStep(
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

    valid, errors = workflow.validate()
    if not valid:
        raise HTTPException(400, detail=f"Invalid workflow: {'; '.join(errors)}")

    engine.store(workflow)
    return {"workflow": workflow.to_dict()}


@router.delete("/{workflow_id}")
async def delete_workflow(workflow_id: str, request: Request, db=Depends(get_db)):
    """Delete a workflow."""
    await get_current_user(request, db)
    engine = get_workflow_engine()
    if not engine.delete(workflow_id):
        raise HTTPException(404, f"Workflow not found: {workflow_id}")
    return {"deleted": True}


@router.get("/{workflow_id}/export")
async def export_workflow(workflow_id: str, request: Request,
                          format: str = Query("yaml"),
                          db=Depends(get_db)):
    """Export a workflow as YAML or JSON."""
    await get_current_user(request, db)
    engine = get_workflow_engine()
    workflow = engine.load(workflow_id)
    if not workflow:
        raise HTTPException(404, f"Workflow not found: {workflow_id}")

    if format == "json":
        return {"format": "json", "content": workflow.to_json()}
    return {"format": "yaml", "content": workflow.to_yaml()}


@router.get("/{workflow_id}/ucip")
async def workflow_ucip(workflow_id: str, request: Request, db=Depends(get_db)):
    """Get the UCIP ExecutionPlan for a workflow."""
    await get_current_user(request, db)
    engine = get_workflow_engine()
    workflow = engine.load(workflow_id)
    if not workflow:
        raise HTTPException(404, f"Workflow not found: {workflow_id}")
    return {"ucip_plan": workflow.to_ucip_plan()}


@router.post("/import")
async def import_workflow(req: WorkflowImport, request: Request, db=Depends(get_db)):
    """Import a workflow from YAML or JSON."""
    await get_current_user(request, db)

    if req.format == "yaml":
        workflow = Workflow.from_yaml(req.content)
    elif req.format == "json":
        data = json.loads(req.content)
        workflow = Workflow.from_dict(data)
    else:
        raise HTTPException(400, f"Unsupported format: {req.format}")

    if req.name:
        workflow.name = req.name

    valid, errors = workflow.validate()
    if not valid:
        raise HTTPException(400, detail=f"Invalid workflow: {'; '.join(errors)}")

    engine = get_workflow_engine()
    engine.store(workflow)

    return {"workflow": workflow.to_dict()}