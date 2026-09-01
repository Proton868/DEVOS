"""
API — Research route (Agency OS Master Plan §5 Deep Research).

Exposes the DeepResearchAgent as a REST API. Supports both synchronous
research (quick) and background research with polling (long).
"""
import json
import uuid
from fastapi import APIRouter, Depends, Request, Query, BackgroundTasks
from pydantic import BaseModel, Field
from typing import Optional

from api.routes.auth import get_current_user
from core.database import get_db

router = APIRouter(prefix="/api/research", tags=["research"])

# In-memory store for background research jobs
_research_jobs: dict[str, dict] = {}


class ResearchRequest(BaseModel):
    question: str = Field(..., min_length=3, max_length=2000)
    max_sources: int = Field(default=5, ge=1, le=20)
    depth: str = Field(default="standard")
    provider: Optional[str] = None
    model: Optional[str] = None


@router.post("/start")
async def start_research(req: ResearchRequest, request: Request, db=Depends(get_db)):
    """Start a deep research job. Returns immediately with a job_id for polling."""
    await get_current_user(request, db)

    job_id = str(uuid.uuid4())
    _research_jobs[job_id] = {
        "status": "running",
        "question": req.question,
        "max_sources": req.max_sources,
        "depth": req.depth,
        "report": None,
        "error": None,
    }

    # Fire and forget — the backfill runs in background
    import asyncio
    asyncio.create_task(_run_research_job(
        job_id, req.question, req.max_sources, req.depth,
        req.provider, req.model,
    ))

    return {"job_id": job_id, "status": "running"}


@router.get("/jobs/{job_id}")
async def get_job(job_id: str, request: Request, db=Depends(get_db)):
    """Poll a research job by ID."""
    await get_current_user(request, db)
    job = _research_jobs.get(job_id)
    if not job:
        return {"error": f"Research job not found: {job_id}"}
    return job


@router.get("/jobs")
async def list_jobs(request: Request, db=Depends(get_db)):
    """List all research jobs."""
    await get_current_user(request, db)
    return {
        "jobs": [
            {"job_id": k, "question": v["question"], "status": v["status"]}
            for k, v in _research_jobs.items()
        ],
        "count": len(_research_jobs),
    }


@router.post("/quick")
async def quick_research(req: ResearchRequest, request: Request, db=Depends(get_db)):
    """Run research synchronously (returns when done). Best for short queries."""
    await get_current_user(request, db)

    from brain.research import DeepResearchAgent
    agent = DeepResearchAgent(provider=req.provider, model=req.model)
    report = await agent.research(
        question=req.question,
        max_sources=req.max_sources,
        depth=req.depth,
    )

    return {
        "report": report.to_dict(),
        "sources_count": len(report.sources),
        "citations_count": len(report.citations),
        "confidence": report.confidence,
    }


async def _run_research_job(job_id: str, question: str, max_sources: int,
                             depth: str, provider: Optional[str],
                             model: Optional[str]):
    """Background task: run deep research and store result."""
    try:
        from brain.research import DeepResearchAgent
        agent = DeepResearchAgent(provider=provider, model=model)
        report = await agent.research(
            question=question,
            max_sources=max_sources,
            depth=depth,
        )
        _research_jobs[job_id] = {
            "status": "done",
            "question": question,
            "max_sources": max_sources,
            "depth": depth,
            "report": report.to_dict(),
            "error": None,
        }
    except Exception as e:
        _research_jobs[job_id] = {
            "status": "failed",
            "question": question,
            "max_sources": max_sources,
            "depth": depth,
            "report": None,
            "error": str(e),
        }