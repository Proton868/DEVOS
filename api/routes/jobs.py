from fastapi import APIRouter, Depends, Request, HTTPException
from pydantic import BaseModel
from typing import Any
from api.routes.auth import get_current_user
from governance.tenant_store import ensure_personal_tenant, list_jobs_for_user
from governance.request_identity import get_tenant_context
from core.database import get_db
from workers.job_queue import enqueue

router = APIRouter(prefix="/api/jobs", tags=["jobs"])

class EnqueueReq(BaseModel):
    job_type: str = "script"
    payload: dict[str, Any] = {}
    priority: int = 100
    max_attempts: int = 3

@router.get("")
async def list_jobs(request: Request, db=Depends(get_db), limit: int = 50):
    user = await get_current_user(request, db)
    await ensure_personal_tenant(db, user)
    ctx = await get_tenant_context(request, db, user)
    jobs = await list_jobs_for_user(db, user.id, tenant_id=ctx.tenant_id, limit=limit)
    return {"jobs":[{"id":j.id,"job_type":j.job_type,"status":j.status,"priority":j.priority,
        "attempts":j.attempts,"tenant_id":j.tenant_id,"worker_id":getattr(j,"worker_id",None)} for j in jobs], "count":len(jobs)}

@router.post("")
async def create_job(req: EnqueueReq, request: Request, db=Depends(get_db)):
    user = await get_current_user(request, db)
    await ensure_personal_tenant(db, user)
    ctx = await get_tenant_context(request, db, user)
    job = await enqueue(owner_id=user.id, tenant_id=ctx.tenant_id, job_type=req.job_type,
        payload=req.payload, actor_id=ctx.identity.actor_id, priority=req.priority, max_attempts=req.max_attempts)
    return {"id":job.id,"status":job.status,"tenant_id":job.tenant_id}

@router.get("/{job_id}")
async def get_job(job_id: str, request: Request, db=Depends(get_db)):
    user = await get_current_user(request, db)
    await ensure_personal_tenant(db, user)
    from sqlalchemy import select
    from core.database import ExecutionJob
    from governance.tenant_store import is_tenant_member
    r = await db.execute(select(ExecutionJob).where(ExecutionJob.id==job_id))
    job = r.scalar_one_or_none()
    if not job: raise HTTPException(404, "job not found")
    if job.owner_id != user.id and not getattr(user,"is_admin",False):
        if not job.tenant_id or not await is_tenant_member(db, user.id, job.tenant_id):
            raise HTTPException(403, "forbidden")
    return {"id":job.id,"job_type":job.job_type,"status":job.status,"payload":job.payload,
        "result":job.result,"error":job.error,"attempts":job.attempts,"isolation":job.isolation}
