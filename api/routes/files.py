"""Files route — IDE file tree, read, write, create, rename, delete.
Scoped to data/projects/{user_id}/{project_id}/, the same convention
brain/builder.py uses, so the IDE and Project Builder share one file tree.
Destructive actions (delete) go through the same HITL gate as autonomous
Brain actions, since a file is a file regardless of who deleted it.
"""
from fastapi import APIRouter, Depends, Request, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import Optional

from core.database import get_db
from api.routes.auth import get_current_user
from execution.files import FileService, PathViolation

router = APIRouter()


def _service(user_id: str, project_id: str) -> FileService:
    return FileService(user_id, project_id)


@router.get("/{project_id}/tree")
async def get_tree(project_id: str, request: Request, db=Depends(get_db)):
    user = await get_current_user(request, db)
    return {"files": _service(user.id, project_id).tree()}


@router.get("/{project_id}/read")
async def read_file(project_id: str, path: str, request: Request, db=Depends(get_db)):
    user = await get_current_user(request, db)
    try:
        return _service(user.id, project_id).read(path)
    except FileNotFoundError:
        raise HTTPException(404, f"Not found: {path}")
    except PathViolation as e:
        raise HTTPException(400, str(e))
    except ValueError as e:
        raise HTTPException(413, str(e))


@router.get("/{project_id}/download")
async def download_file(project_id: str, path: str, request: Request, db=Depends(get_db)):
    user = await get_current_user(request, db)
    try:
        file_path = _service(user.id, project_id)._resolve(path)
    except PathViolation as e:
        raise HTTPException(400, str(e))
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(404, f"Not found: {path}")
    return FileResponse(file_path, media_type="application/octet-stream", filename=file_path.name)


class WriteReq(BaseModel):
    path: str
    content: str


@router.post("/{project_id}/write")
async def write_file(project_id: str, req: WriteReq, request: Request, db=Depends(get_db)):
    user = await get_current_user(request, db)
    try:
        return _service(user.id, project_id).write(req.path, req.content)
    except PathViolation as e:
        raise HTTPException(400, str(e))
    except ValueError as e:
        # security-audit P3g: FileService.write() now enforces the same
        # MAX_READ_BYTES cap read() already had — surface it as 413
        # (Payload Too Large) rather than a generic 500.
        raise HTTPException(413, str(e))


class CreateReq(BaseModel):
    path: str
    is_dir: bool = False


@router.post("/{project_id}/create")
async def create_file(project_id: str, req: CreateReq, request: Request, db=Depends(get_db)):
    user = await get_current_user(request, db)
    try:
        return _service(user.id, project_id).create(req.path, req.is_dir)
    except FileExistsError:
        raise HTTPException(409, f"Already exists: {req.path}")
    except PathViolation as e:
        raise HTTPException(400, str(e))


class RenameReq(BaseModel):
    path: str
    new_path: str


@router.post("/{project_id}/rename")
async def rename_file(project_id: str, req: RenameReq, request: Request, db=Depends(get_db)):
    user = await get_current_user(request, db)
    try:
        return _service(user.id, project_id).rename(req.path, req.new_path)
    except FileNotFoundError:
        raise HTTPException(404, f"Not found: {req.path}")
    except PathViolation as e:
        raise HTTPException(400, str(e))


@router.delete("/{project_id}/delete")
async def delete_file(project_id: str, path: str, request: Request, db=Depends(get_db)):
    """Irreversible — routed through the same HITL gate the Brain's
    delete_file tool contract uses, so a human deleting via the IDE and
    an agent deleting autonomously are held to the same standard."""
    user = await get_current_user(request, db)
    from governance.hitl import HITLQueue
    queue = HITLQueue()
    hitl_req = await queue.submit(
        loop_id="ide-direct", agent_id="human-ide-user", user_id=user.id,
        action="delete_file", action_input=path,
        description=f"Delete file/dir '{path}' in project {project_id} via IDE",
        cap_required="ucip:filesystem.delete",
        reason="Irreversible filesystem action",
    )
    approved = await queue.wait_for_decision(hitl_req.id)
    if not approved:
        raise HTTPException(403, "Deletion not approved (denied or timed out)")
    try:
        return _service(user.id, project_id).delete(path)
    except FileNotFoundError:
        raise HTTPException(404, f"Not found: {path}")
    except PathViolation as e:
        raise HTTPException(400, str(e))
