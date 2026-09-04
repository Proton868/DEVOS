from __future__ import annotations
from typing import Optional
from fastapi import APIRouter, Depends, Request, HTTPException
from pydantic import BaseModel
from core.database import get_db
from api.routes.auth import get_current_user
from governance.tenant_store import ensure_personal_tenant
from execution.github_client import GitHubClient, GitHubError
from execution.vcs import GitService

router = APIRouter()


@router.get("/status")
async def github_status(request: Request, db=Depends(get_db)):
    await get_current_user(request, db)
    client = GitHubClient()
    try:
        user = await client.whoami()
        return {"connected": True, "user": {"login": user.get("login"), "id": user.get("id")}}
    except GitHubError as e:
        return {"connected": False, "error": e.code, "message": str(e)}


class CreateRepoReq(BaseModel):
    name: str
    private: bool = True
    description: str = ""


@router.post("/repos")
async def create_repo(body: CreateRepoReq, request: Request, db=Depends(get_db)):
    user = await get_current_user(request, db)
    await ensure_personal_tenant(db, user)
    try:
        return await GitHubClient().create_repo(body.name, private=body.private, description=body.description)
    except GitHubError as e:
        raise HTTPException(400 if e.http < 500 else 502, detail={"code": e.code, "message": str(e)})


class CreatePRReq(BaseModel):
    owner: str
    repo: str
    title: str
    head: str
    base: str = "main"
    body: str = ""


@router.post("/pulls")
async def create_pr(body: CreatePRReq, request: Request, db=Depends(get_db)):
    user = await get_current_user(request, db)
    await ensure_personal_tenant(db, user)
    try:
        return await GitHubClient().create_pr(
            body.owner, body.repo, title=body.title, head=body.head, base=body.base, body=body.body
        )
    except GitHubError as e:
        raise HTTPException(400 if e.http < 500 else 502, detail={"code": e.code, "message": str(e)})


@router.get("/repos/{owner}/{repo}/branches")
async def branches(owner: str, repo: str, request: Request, db=Depends(get_db)):
    await get_current_user(request, db)
    try:
        return {"branches": await GitHubClient().list_branches(owner, repo)}
    except GitHubError as e:
        raise HTTPException(400, detail={"code": e.code, "message": str(e)})


class ImportReq(BaseModel):
    clone_url: str
    project_id: str
    branch: Optional[str] = None


@router.post("/import")
async def import_repo(body: ImportReq, request: Request, db=Depends(get_db)):
    user = await get_current_user(request, db)
    await ensure_personal_tenant(db, user)
    gs = GitService(user.id, body.project_id)
    # clone into workspace root
    args = ["clone", body.clone_url, "."]
    if body.branch:
        args = ["clone", "--branch", body.branch, body.clone_url, "."]
    # use git via process — workspace must be empty-ish
    import asyncio
    proc = await asyncio.create_subprocess_exec(
        "git", *args, cwd=str(gs.root),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate()
    if proc.returncode != 0:
        raise HTTPException(400, detail={"code": "GITHUB_ERROR", "message": err.decode(errors="replace")[:400]})
    return {"ok": True, "stdout": out.decode(errors="replace")[:500]}
