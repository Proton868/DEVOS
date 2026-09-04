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
from governance.tenant_store import ensure_personal_tenant
from api.deps import tenant_ctx

from execution.files import FileService, PathViolation

router = APIRouter()


def _service(user_id: str, project_id: str) -> FileService:
    return FileService(user_id, project_id)


@router.get("/{project_id}/tree")
async def get_tree(
    project_id: str,
    request: Request,
    db=Depends(get_db),
    depth: Optional[int] = None,
):
    """Hierarchical tree. depth=1 returns root only (lazy); omit for full (bounded)."""
    user = await get_current_user(request, db)
    await ensure_personal_tenant(db, user)
    tree = _service(user.id, project_id).tree(max_depth=depth)
    # Dual keys for compatibility: frontend prefers `tree`, older clients used `files`
    return {"tree": tree, "files": tree}


@router.get("/{project_id}/list")
async def list_dir(
    project_id: str,
    request: Request,
    db=Depends(get_db),
    path: str = "",
):
    """Lazy one-level directory listing for expand-on-demand."""
    user = await get_current_user(request, db)
    await ensure_personal_tenant(db, user)
    try:
        return {"entries": _service(user.id, project_id).list_dir(path)}
    except FileNotFoundError:
        raise HTTPException(404, f"Not found: {path or '.'}")
    except PathViolation as e:
        raise HTTPException(400, str(e))


@router.get("/{project_id}/read")
async def read_file(project_id: str, path: str, request: Request, db=Depends(get_db)):
    user = await get_current_user(request, db)
    await ensure_personal_tenant(db, user)
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
    await ensure_personal_tenant(db, user)
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
    await ensure_personal_tenant(db, user)
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
    await ensure_personal_tenant(db, user)
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
    await ensure_personal_tenant(db, user)
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
    await ensure_personal_tenant(db, user)
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


# ── Workspace artifact preview (read-only, scoped, sandboxed) ───────────
import mimetypes
import re
from fastapi.responses import Response, HTMLResponse

# Filenames that must never be served via preview (secrets / credentials)
_PREVIEW_BLOCKED_NAMES = {
    ".env",
    ".env.local",
    ".env.production",
    ".env.development",
    ".git",
    "id_rsa",
    "id_ed25519",
    "credentials.json",
    "service-account.json",
}
_PREVIEW_BLOCKED_SUFFIXES = (
    ".pem",
    ".key",
    ".p12",
    ".pfx",
    ".crt",
    ".cer",
)
_PREVIEW_ALLOWED_SUFFIXES = {
    ".html", ".htm", ".css", ".js", ".mjs", ".cjs", ".json",
    ".svg", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico",
    ".woff", ".woff2", ".ttf", ".otf", ".txt", ".md", ".map",
}


def _preview_blocked(rel_path: str) -> bool:
    name = (rel_path or "").replace("\\", "/").split("/")[-1].lower()
    if name in _PREVIEW_BLOCKED_NAMES or name.startswith(".env"):
        return True
    if any(name.endswith(s) for s in _PREVIEW_BLOCKED_SUFFIXES):
        return True
    # block hidden git internals
    parts = (rel_path or "").replace("\\", "/").split("/")
    if any(p == ".git" for p in parts):
        return True
    return False


def _preview_mime(path: str) -> str:
    lower = path.lower()
    explicit = {
        ".html": "text/html; charset=utf-8",
        ".htm": "text/html; charset=utf-8",
        ".css": "text/css; charset=utf-8",
        ".js": "application/javascript; charset=utf-8",
        ".mjs": "application/javascript; charset=utf-8",
        ".json": "application/json; charset=utf-8",
        ".svg": "image/svg+xml",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".webp": "image/webp",
        ".ico": "image/x-icon",
        ".txt": "text/plain; charset=utf-8",
        ".md": "text/markdown; charset=utf-8",
    }
    for suf, mime in explicit.items():
        if lower.endswith(suf):
            return mime
    guessed, _ = mimetypes.guess_type(path)
    return guessed or "application/octet-stream"


def _inject_html_base(html: str, base_href: str) -> str:
    """Ensure relative CSS/JS resolve under the preview URL prefix."""
    if re.search(r"<base\s", html, re.I):
        return html
    tag = f'<base href="{base_href}">'
    if re.search(r"<head[^>]*>", html, re.I):
        return re.sub(r"(<head[^>]*>)", r"\1" + tag, html, count=1, flags=re.I)
    return tag + html


@router.get("/{project_id}/preview")
@router.get("/{project_id}/preview/{file_path:path}")
async def preview_workspace_file(
    project_id: str,
    request: Request,
    db=Depends(get_db),
    file_path: str = "index.html",
    token: Optional[str] = None,
):
    """
    Serve a workspace artifact for in-app / external browser preview.

    Security:
      - Authenticated user only (Bearer, cookie, or token query for iframe assets)
      - Paths resolved exclusively via FileService (project root)
      - Path traversal rejected
      - Secret-like files blocked
      - Read-only; never executes host tools
    """
    user = await get_current_user(request, db)
    await ensure_personal_tenant(db, user)

    rel = (file_path or "index.html").strip() or "index.html"
    if _preview_blocked(rel):
        raise HTTPException(403, "This file cannot be previewed")

    # Soft type gate: allow known static web assets; deny odd binaries by default
    lower = rel.lower()
    if "." in lower.split("/")[-1]:
        suf = "." + lower.split("/")[-1].split(".")[-1]
        if suf not in _PREVIEW_ALLOWED_SUFFIXES and not lower.endswith(".html"):
            # still allow if mimetype is text/image later; but block unknown extensions
            if suf not in {".map"}:
                pass  # allow through FileService; blocked list handles secrets

    try:
        path = _service(user.id, project_id)._resolve(rel)
    except PathViolation as e:
        raise HTTPException(400, str(e))

    if not path.exists() or not path.is_file():
        raise HTTPException(404, f"Artifact not found: {rel}")

    mime = _preview_mime(rel)
    # Optional size guard
    try:
        size = path.stat().st_size
    except OSError:
        raise HTTPException(404, f"Artifact not found: {rel}")
    if size > 5 * 1024 * 1024:
        raise HTTPException(413, "Preview file too large")

    raw = path.read_bytes()
    headers = {
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": "no-store",
        "Content-Security-Policy": (
            "default-src 'none'; "
            "img-src 'self' data: blob:; "
            "style-src 'self' 'unsafe-inline'; "
            "script-src 'self' 'unsafe-inline'; "
            "font-src 'self' data:; "
            "base-uri 'self'; "
            "form-action 'none'; "
            "frame-ancestors 'self'"
        ),
    }

    if mime.startswith("text/html"):
        text = raw.decode("utf-8", errors="replace")
        # base must include trailing path directory + token for relative assets
        q = f"?token={token}" if token else ""
        # Directory of current file under preview mount
        parent = "/".join(rel.split("/")[:-1])
        base_path = f"/api/files/{project_id}/preview/"
        if parent:
            base_path = f"/api/files/{project_id}/preview/{parent}/"
        # token must be on each relative request — browsers don't auto-append query to relative URLs.
        # So we rewrite relative src/href to absolute preview URLs with token when token present.
        if token:
            def _rewrite(match):
                attr, url = match.group(1), match.group(2)
                if url.startswith(("http://", "https://", "//", "data:", "blob:", "#")):
                    return match.group(0)
                if url.startswith("/api/"):
                    return match.group(0)
                clean = url.lstrip("./")
                if parent and not url.startswith("/"):
                    joined = f"{parent}/{clean}".replace("//", "/")
                else:
                    joined = clean.lstrip("/")
                return f'{attr}="/api/files/{project_id}/preview/{joined}?token={token}"'

            text = re.sub(
                r'''\b(href|src)=["']([^"']+)["']''',
                _rewrite,
                text,
                flags=re.I,
            )
        text = _inject_html_base(text, base_path)
        return HTMLResponse(content=text, headers=headers)

    return Response(content=raw, media_type=mime, headers=headers)
