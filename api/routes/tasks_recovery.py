from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from api.routes.auth import get_current_user
from core.database import get_db
router = APIRouter()

@router.get("/recovery")
async def recovery_scan(request: Request, db=Depends(get_db)):
    await get_current_user(request, db)
    from core.recovery import scan_recovery
    return scan_recovery().to_dict()

@router.get("/{task_id}/attempts")
async def task_attempts(task_id: str, request: Request, db=Depends(get_db)):
    await get_current_user(request, db)
    from core.task_registry import TaskRegistry
    if not TaskRegistry().get(task_id): raise HTTPException(404, f"No task {task_id}")
    return {"task_id": task_id, "attempts": TaskRegistry().get_attempts(task_id)}

@router.get("/{task_id}/events")
async def task_events(task_id: str, request: Request, db=Depends(get_db)):
    await get_current_user(request, db)
    from core.event_store import EventStore
    return {"task_id": task_id, "events": [e.to_dict() for e in EventStore().for_task(task_id)]}

@router.post("/{task_id}/resume")
async def resume_task(task_id: str, request: Request, db=Depends(get_db)):
    await get_current_user(request, db)
    from core.task_orch import OrchestrationService
    try: req = OrchestrationService().resume_request(task_id)
    except ValueError as e: raise HTTPException(400, str(e))
    return {"next_request": req.to_dict()}

class CancelBody(BaseModel):
    reason: str = "cancelled by user"

@router.post("/{task_id}/cancel")
async def cancel_task(task_id: str, body: CancelBody, request: Request, db=Depends(get_db)):
    await get_current_user(request, db)
    from core.task_orch import OrchestrationService
    return {"cancelled": [r.to_dict() for r in OrchestrationService().cancel_tree(task_id, body.reason)]}

@router.get("/{task_id}/tree")
async def task_tree(task_id: str, request: Request, db=Depends(get_db)):
    await get_current_user(request, db)
    from core.task_registry import TaskRegistry
    return TaskRegistry().tree(task_id)

@router.get("/{task_id}")
async def get_task(task_id: str, request: Request, db=Depends(get_db)):
    await get_current_user(request, db)
    from core.task_registry import TaskRegistry
    data = TaskRegistry().get(task_id)
    if not data: raise HTTPException(404, f"No task {task_id}")
    return data
