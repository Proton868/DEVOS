"""Artifact import/export API — extends FileService authority."""
from __future__ import annotations

from typing import Optional, List

from fastapi import APIRouter, Depends, Request, HTTPException, UploadFile, File, Form
from fastapi.responses import Response
from pydantic import BaseModel

from core.database import get_db
from api.routes.auth import get_current_user
from governance.tenant_store import ensure_personal_tenant
from execution.files import FileService, PathViolation
from execution.artifacts import (
    write_bytes,
    extract_archive,
    export_project_zip,
    list_artifact_metas,
    ArtifactError,
    MAX_UPLOAD_SIZE,
    is_secret_path,
)
from execution.app_detect import detect_application

router = APIRouter()


def _fs(user_id: str, project_id: str) -> FileService:
    return FileService(user_id, project_id)


@router.get("/{project_id}/artifacts")
async def list_artifacts(project_id: str, request: Request, db=Depends(get_db), limit: int = 500):
    user = await get_current_user(request, db)
    await ensure_personal_tenant(db, user)
    metas = list_artifact_metas(_fs(user.id, project_id), limit=min(limit, 2000))
    return {"artifacts": [m.to_dict() for m in metas], "count": len(metas)}


@router.get("/{project_id}/app-detect")
async def app_detect(project_id: str, request: Request, db=Depends(get_db)):
    user = await get_current_user(request, db)
    await ensure_personal_tenant(db, user)
    return detect_application(_fs(user.id, project_id))


@router.post("/{project_id}/upload")
async def upload_file(
    project_id: str,
    request: Request,
    db=Depends(get_db),
    file: UploadFile = File(...),
    path: Optional[str] = Form(None),
):
    user = await get_current_user(request, db)
    await ensure_personal_tenant(db, user)
    data = await file.read()
    if len(data) > MAX_UPLOAD_SIZE:
        raise HTTPException(413, detail={"code": "UPLOAD_TOO_LARGE", "message": "Upload too large"})
    rel = (path or file.filename or "upload.bin").lstrip("/")
    try:
        meta = write_bytes(_fs(user.id, project_id), rel, data)
    except PathViolation as e:
        raise HTTPException(400, detail={"code": "PATH_VIOLATION", "message": str(e)})
    except ArtifactError as e:
        raise HTTPException(400, detail={"code": e.code, "message": str(e)})
    return {"artifact": meta.to_dict()}


@router.post("/{project_id}/upload-archive")
async def upload_archive(
    project_id: str,
    request: Request,
    db=Depends(get_db),
    file: UploadFile = File(...),
    dest_prefix: str = Form(""),
):
    user = await get_current_user(request, db)
    await ensure_personal_tenant(db, user)
    data = await file.read()
    if len(data) > MAX_UPLOAD_SIZE:
        raise HTTPException(413, detail={"code": "UPLOAD_TOO_LARGE", "message": "Upload too large"})
    name = file.filename or "archive.zip"
    try:
        metas = extract_archive(_fs(user.id, project_id), name, data, dest_prefix=dest_prefix or "")
    except ArtifactError as e:
        raise HTTPException(400, detail={"code": e.code, "message": str(e)})
    except PathViolation as e:
        raise HTTPException(400, detail={"code": "PATH_VIOLATION", "message": str(e)})
    return {
        "extracted": len(metas),
        "artifacts": [m.to_dict() for m in metas[:100]],
        "truncated": len(metas) > 100,
    }


@router.get("/{project_id}/export")
async def export_project(
    project_id: str,
    request: Request,
    db=Depends(get_db),
    exclude_secrets: bool = True,
):
    user = await get_current_user(request, db)
    await ensure_personal_tenant(db, user)
    try:
        blob = export_project_zip(_fs(user.id, project_id), exclude_secrets=exclude_secrets)
    except Exception as e:
        raise HTTPException(500, detail={"code": "EXPORT_FAILED", "message": str(e)[:200]})
    return Response(
        content=blob,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{project_id}.zip"',
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "no-store",
        },
    )


class SecretScanReq(BaseModel):
    paths: Optional[List[str]] = None


@router.post("/{project_id}/secret-scan")
async def secret_scan(project_id: str, request: Request, db=Depends(get_db), req: SecretScanReq = None):
    """Heuristic secret path scan before share/push/export. Not perfect."""
    user = await get_current_user(request, db)
    await ensure_personal_tenant(db, user)
    fs = _fs(user.id, project_id)
    hits = []
    for m in list_artifact_metas(fs, limit=2000):
        if is_secret_path(m.path):
            hits.append({"path": m.path, "reason": "secret_path_pattern"})
    return {"hits": hits, "blocked": len(hits) > 0, "note": "Heuristic only — not a complete secret scanner"}
