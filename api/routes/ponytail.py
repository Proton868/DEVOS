"""
API — Ponytail route (Agency OS Master Plan §4).

Exposes the 11-stage Ponytail coding pipeline as a REST API.
Supports starting a pipeline run, polling for status, and listing history.
"""
import json
import uuid
from fastapi import APIRouter, Depends, Request, Query
from pydantic import BaseModel, Field
from typing import Optional

from api.routes.auth import get_current_user
from core.database import get_db

router = APIRouter(prefix="/api/ponytail", tags=["ponytail"])

# In-memory store for pipeline runs
_pipeline_runs: dict[str, dict] = {}


class PonytailRequest(BaseModel):
    goal: str = Field(..., min_length=3, max_length=4000)
    code_context: str = Field(default="", max_length=20000)
    provider: Optional[str] = None
    model: Optional[str] = None


@router.post("/run")
async def start_pipeline(req: PonytailRequest, request: Request, db=Depends(get_db)):
    """Start a Ponytail pipeline run. Returns immediately with a run_id."""
    await get_current_user(request, db)

    run_id = str(uuid.uuid4())
    _pipeline_runs[run_id] = {
        "status": "pending",
        "goal": req.goal,
        "code_context": req.code_context[:500],
        "stages": [],
        "error": None,
    }

    import asyncio
    asyncio.create_task(_run_pipeline_job(
        run_id, req.goal, req.code_context,
        req.provider, req.model,
    ))

    return {"run_id": run_id, "status": "pending"}


@router.get("/runs/{run_id}")
async def get_run(run_id: str, request: Request, db=Depends(get_db)):
    """Poll a pipeline run by ID."""
    await get_current_user(request, db)
    run = _pipeline_runs.get(run_id)
    if not run:
        return {"error": f"Pipeline run not found: {run_id}"}
    return run


@router.get("/runs")
async def list_runs(
    request: Request,
    status: Optional[str] = Query(None, description="Filter by status"),
    limit: int = Query(20, ge=1, le=100),
    db=Depends(get_db),
):
    """List pipeline runs, newest first."""
    await get_current_user(request, db)
    runs = list(_pipeline_runs.values())
    if status:
        runs = [r for r in runs if r["status"] == status]
    runs = sorted(runs, key=lambda r: r.get("created_at", ""), reverse=True)[:limit]
    return {"runs": runs, "count": len(runs)}


@router.get("/stages")
async def list_stages(request: Request, db=Depends(get_db)):
    """List all Ponytail pipeline stages with descriptions."""
    await get_current_user(request, db)
    from cognitive.ponytail import PipelineStage, STAGE_DESCRIPTIONS
    return {
        "stages": [
            {"stage": s.value, "description": STAGE_DESCRIPTIONS[s]}
            for s in PipelineStage
        ]
    }


async def _run_pipeline_job(run_id: str, goal: str, code_context: str,
                             provider: Optional[str], model: Optional[str]):
    """Background task: run the full Ponytail pipeline."""
    try:
        from cognitive.ponytail import PonytailPipeline
        pipeline = PonytailPipeline(provider=provider, model=model)
        run = await pipeline.run(goal=goal, code_context=code_context, run_id=run_id)

        _pipeline_runs[run_id] = {
            "status": "done",
            "goal": goal,
            "code_context": code_context[:500],
            "stages": [s.to_dict() for s in run.stages],
            "lessons": run.lessons,
            "final_status": run.final_status,
            "total_time_ms": run.total_time_ms,
            "total_tokens": run.total_tokens,
            "error": None,
        }
    except Exception as e:
        _pipeline_runs[run_id] = {
            "status": "failed",
            "goal": goal,
            "code_context": code_context[:500],
            "stages": _pipeline_runs.get(run_id, {}).get("stages", []),
            "error": str(e),
        }