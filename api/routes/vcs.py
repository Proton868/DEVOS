"""Git route — status/init/stage/commit/push/pull/checkout/log for the IDE's
git panel. Scoped to data/projects/{user_id}/{project_id}/."""
from fastapi import APIRouter, Depends, Request, HTTPException
from pydantic import BaseModel
from typing import Optional

from core.database import get_db
from api.routes.auth import get_current_user
from execution.vcs import GitService

router = APIRouter()


def _service(user_id: str, project_id: str) -> GitService:
    return GitService(user_id, project_id)


@router.get("/{project_id}/status")
async def status(project_id: str, request: Request, db=Depends(get_db)):
    user = await get_current_user(request, db)
    return await _service(user.id, project_id).status()


@router.post("/{project_id}/init")
async def init(project_id: str, request: Request, db=Depends(get_db)):
    user = await get_current_user(request, db)
    return await _service(user.id, project_id).init()


class PathsReq(BaseModel):
    paths: Optional[list[str]] = None


@router.post("/{project_id}/stage")
async def stage(project_id: str, req: PathsReq, request: Request, db=Depends(get_db)):
    user = await get_current_user(request, db)
    return await _service(user.id, project_id).stage(req.paths)


@router.post("/{project_id}/unstage")
async def unstage(project_id: str, req: PathsReq, request: Request, db=Depends(get_db)):
    user = await get_current_user(request, db)
    return await _service(user.id, project_id).unstage(req.paths)


class CommitReq(BaseModel):
    message: str
    author: Optional[str] = None


@router.post("/{project_id}/commit")
async def commit(project_id: str, req: CommitReq, request: Request, db=Depends(get_db)):
    user = await get_current_user(request, db)
    return await _service(user.id, project_id).commit(req.message, req.author)


class RemoteReq(BaseModel):
    remote: str = "origin"
    branch: Optional[str] = None


@router.post("/{project_id}/push")
async def push(project_id: str, req: RemoteReq, request: Request, db=Depends(get_db)):
    user = await get_current_user(request, db)
    return await _service(user.id, project_id).push(req.remote, req.branch)


@router.post("/{project_id}/pull")
async def pull(project_id: str, req: RemoteReq, request: Request, db=Depends(get_db)):
    user = await get_current_user(request, db)
    return await _service(user.id, project_id).pull(req.remote, req.branch)


class CheckoutReq(BaseModel):
    branch: str
    create: bool = False


@router.post("/{project_id}/checkout")
async def checkout(project_id: str, req: CheckoutReq, request: Request, db=Depends(get_db)):
    user = await get_current_user(request, db)
    return await _service(user.id, project_id).checkout(req.branch, req.create)


@router.get("/{project_id}/log")
async def log(project_id: str, limit: int = 20, request: Request = None, db=Depends(get_db)):
    user = await get_current_user(request, db)
    return await _service(user.id, project_id).log(limit)


@router.get("/{project_id}/diff")
async def diff(project_id: str, path: Optional[str] = None, staged: bool = False,
                request: Request = None, db=Depends(get_db)):
    user = await get_current_user(request, db)
    return await _service(user.id, project_id).diff(path, staged)


class DiscardReq(BaseModel):
    path: str


@router.post("/{project_id}/discard")
async def discard(project_id: str, req: DiscardReq, request: Request, db=Depends(get_db)):
    """Irreversible for uncommitted work — routed through the same HITL gate
    delete_file uses, same trust-model reasoning: destructive is destructive
    regardless of whether a human or an agent triggered it."""
    user = await get_current_user(request, db)
    from governance.hitl import HITLQueue
    queue = HITLQueue()
    hitl_req = await queue.submit(
        loop_id="ide-direct", agent_id="human-ide-user", user_id=user.id,
        action="git_discard", action_input=req.path,
        description=f"Discard uncommitted changes to '{req.path}' in project {project_id}",
        cap_required="ucip:vcs.write",
        reason="Irreversible for uncommitted work",
    )
    approved = await queue.wait_for_decision(hitl_req.id)
    if not approved:
        raise HTTPException(403, "Discard not approved (denied or timed out)")
    return await _service(user.id, project_id).discard(req.path)
