"""Files route — IDE file tree, read, write, create, rename, delete.
Scoped to data/projects/{user_id}/{project_id}/, the same convention
brain/builder.py uses, so the IDE and Project Builder share one file tree.
Destructive actions (delete) go through the same HITL gate as autonomous
Brain actions, since a file is a file regardless of who deleted it.
"""
from fastapi import APIRouter, Depends, Request, HTTPException
from fastapi.responses import FileResponse, Response, HTMLResponse
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

from api.routes.auth import (
    make_preview_token,
    decode_preview_token,
    decode_local_token,
    PREVIEW_TOKEN_TTL_SECONDS,
)

_PREVIEW_BLOCKED_NAMES = {
    ".env", ".env.local", ".env.production", ".env.development", ".git",
    "id_rsa", "id_ed25519", "credentials.json", "service-account.json",
}
_PREVIEW_BLOCKED_SUFFIXES = (".pem", ".key", ".p12", ".pfx", ".crt", ".cer")
_PREVIEW_ALLOWED_SUFFIXES = {
    ".html", ".htm", ".css", ".js", ".mjs", ".cjs", ".json",
    ".svg", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico",
    ".woff", ".woff2", ".ttf", ".otf", ".txt", ".md", ".map", ".wasm",
}

READINESS_UNKNOWN = "UNKNOWN"
READINESS_PENDING = "PENDING"
READINESS_READY = "READY"
READINESS_INVALID = "INVALID"
READINESS_UNSUPPORTED = "UNSUPPORTED"


def _preview_blocked(rel_path: str) -> bool:
    name = (rel_path or "").replace("\\", "/").split("/")[-1].lower()
    if name in _PREVIEW_BLOCKED_NAMES or name.startswith('.env'):
        return True
    if name.startswith('credentials.') or name.startswith('secrets.'):
        return True
    if any(name.endswith(s) for s in _PREVIEW_BLOCKED_SUFFIXES):
        return True
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
        ".wasm": "application/wasm",
    }
    for suf, mime in explicit.items():
        if lower.endswith(suf):
            return mime
    guessed, _ = mimetypes.guess_type(path)
    return guessed or "application/octet-stream"


def _inject_html_base(html: str, base_href: str) -> str:
    if re.search(r"<base\s", html, re.I):
        return html
    tag = f'<base href="{base_href}">'
    if re.search(r"<head[^>]*>", html, re.I):
        return re.sub(r"(<head[^>]*>)", r"\1" + tag, html, count=1, flags=re.I)
    return tag + html


def _preview_csp() -> str:
    return (
        "default-src 'none'; "
        "img-src 'self' data: blob:; "
        "style-src 'self' 'unsafe-inline'; "
        "script-src 'self'; "
        "font-src 'self' data:; "
        "connect-src 'none'; "
        "media-src 'self'; "
        "worker-src 'none'; "
        "child-src 'none'; "
        "frame-src 'none'; "
        "object-src 'none'; "
        "base-uri 'none'; "
        "form-action 'none'; "
        "frame-ancestors 'self'"
    )


def _artifact_type(path: str) -> str:
    lower = (path or "").lower()
    if lower.endswith((".html", ".htm")): return "html"
    if lower.endswith(".css"): return "css"
    if lower.endswith((".js", ".mjs", ".cjs")): return "javascript"
    if lower.endswith(".wasm"): return "wasm"
    if lower.endswith((".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".ico")): return "image"
    if lower.endswith((".woff", ".woff2", ".ttf", ".otf")): return "font"
    return "other"


async def _compute_readiness_async(user_id: str, project_id: str, path: str) -> dict:
    rel = (path or "index.html").strip().lstrip("/") or "index.html"
    out = {
        "workspace_id": project_id,
        "path": rel,
        "type": _artifact_type(rel),
        "verification": "UNKNOWN",
        "preview_supported": False,
        "readiness": READINESS_UNKNOWN,
        "entrypoint": rel if rel.lower().endswith((".html", ".htm")) else "index.html",
        "detail": "",
    }
    if _preview_blocked(rel):
        out['readiness'] = READINESS_UNSUPPORTED
        out['detail'] = 'path_blocked'
        return out
    try:
        fs = _service(user_id, project_id)
        resolved = fs._resolve(rel)
    except PathViolation:
        out['readiness'] = READINESS_INVALID
        out['detail'] = 'path_violation'
        return out
    if not resolved.exists() or not resolved.is_file():
        out['readiness'] = READINESS_PENDING
        out['detail'] = 'missing'
        return out
    name = rel.split("/")[-1].lower()
    suf = "." + name.split(".")[-1] if "." in name else ""
    if suf and suf not in _PREVIEW_ALLOWED_SUFFIXES:
        out['readiness'] = READINESS_UNSUPPORTED
        out['detail'] = 'unsupported_type'
        return out
    if out['type'] == 'wasm':
        out['preview_supported'] = False
        out['readiness'] = READINESS_UNSUPPORTED
        out['detail'] = 'wasm_restricted_under_current_csp'
        out['verification'] = 'PRESENT'
        return out
    verification = 'PRESENT'
    try:
        from brain.orchestration_verify import verify_workspace_artifacts
        evd = await verify_workspace_artifacts(
            user_id=user_id, workspace_id=project_id,
            goal='website' if out['type'] == 'html' else '',
            expected_outputs=[rel],
        )
        verification = 'VERIFIED' if evd.get('passed') else 'FAILED'
    except Exception:
        verification = 'PRESENT'
    out['verification'] = verification
    if verification == 'FAILED':
        out['readiness'] = READINESS_INVALID
        out['detail'] = 'verification_failed'
        return out
    out['preview_supported'] = True
    out['readiness'] = READINESS_READY
    out['detail'] = 'ok'
    return out


def _extract_preview_bearer(request: Request) -> Optional[str]:
    auth = request.headers.get("Authorization") or ""
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    try:
        return request.query_params.get("token") or request.query_params.get("access_token")
    except Exception:
        return None


async def _authorize_preview_request(request: Request, db, project_id: str, rel_path: str):
    raw = _extract_preview_bearer(request)
    if not raw:
        user = await get_current_user(request, db)
        return user, "session"
    preview = decode_preview_token(raw)
    if preview:
        if preview.get("project_id") != project_id:
            raise HTTPException(403, "Preview credential not valid for this workspace")
        prefix = (preview.get("path_prefix") or "").lstrip("/")
        target = (rel_path or "").lstrip("/")
        if prefix and not (target == prefix or target.startswith(prefix.rstrip("/") + "/")):
            raise HTTPException(403, "Preview credential path scope mismatch")
        from core.database import User
        from sqlalchemy import select
        r = await db.execute(select(User).where(User.id == preview["sub"]))
        user = r.scalar_one_or_none()
        if not user:
            raise HTTPException(401, "Preview credential user not found")
        return user, "preview_token"
    session = decode_local_token(raw)
    if session:
        from core.database import User
        from sqlalchemy import select
        r = await db.execute(select(User).where(User.id == session["sub"]))
        user = r.scalar_one_or_none()
        if not user:
            raise HTTPException(401, "Invalid token")
        return user, "session"
    user = await get_current_user(request, db)
    return user, "session"


class PreviewSessionReq(BaseModel):
    path: str = "index.html"
    ttl_seconds: Optional[int] = None


@router.post("/{project_id}/preview-session")
async def create_preview_session(
    project_id: str,
    request: Request,
    db=Depends(get_db),
    req: PreviewSessionReq = None,
):
    """Mint a short-lived, workspace-scoped preview credential."""
    if req is None:
        req = PreviewSessionReq()
    user = await get_current_user(request, db)
    await ensure_personal_tenant(db, user)
    path = (req.path or "index.html").strip().lstrip("/") or "index.html"
    readiness = await _compute_readiness_async(user.id, project_id, path)
    if readiness['readiness'] in (READINESS_INVALID, READINESS_UNSUPPORTED):
        raise HTTPException(400, f"Preview not available: {readiness.get('detail')}")
    ttl = req.ttl_seconds if req.ttl_seconds is not None else PREVIEW_TOKEN_TTL_SECONDS
    minted = make_preview_token(user.id, project_id, path_prefix="", ttl_seconds=ttl)
    return {
        **minted,
        "path": path,
        "readiness": readiness,
        "preview_url": f"/api/files/{project_id}/preview/{path}?token={minted['token']}",
    }


@router.get("/{project_id}/preview-readiness")
async def preview_readiness(
    project_id: str,
    request: Request,
    db=Depends(get_db),
    path: str = "index.html",
):
    """Explicit artifact readiness for PreviewSurface (not mission status)."""
    user = await get_current_user(request, db)
    await ensure_personal_tenant(db, user)
    return await _compute_readiness_async(user.id, project_id, path)


@router.get("/{project_id}/preview")
@router.get("/{project_id}/preview/{file_path:path}")
async def preview_workspace_file(
    project_id: str,
    request: Request,
    db=Depends(get_db),
    file_path: str = "index.html",
    token: Optional[str] = None,
):
    """Serve a workspace artifact for in-app / external browser preview."""
    rel = (file_path or "index.html").strip() or "index.html"
    user, auth_kind = await _authorize_preview_request(request, db, project_id, rel)
    await ensure_personal_tenant(db, user)
    if _preview_blocked(rel):
        raise HTTPException(403, "This file cannot be previewed")
    try:
        path = _service(user.id, project_id)._resolve(rel)
    except PathViolation as e:
        raise HTTPException(400, str(e))
    if not path.exists() or not path.is_file():
        raise HTTPException(404, f"Artifact not found: {rel}")
    mime = _preview_mime(rel)
    try:
        size = path.stat().st_size
    except OSError:
        raise HTTPException(404, f"Artifact not found: {rel}")
    if size > 5 * 1024 * 1024:
        raise HTTPException(413, "Preview file too large")
    raw = path.read_bytes()
    preview_tok = token or _extract_preview_bearer(request)
    embed_token = None
    if preview_tok and decode_preview_token(preview_tok):
        embed_token = preview_tok
    headers = {
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": "no-store",
        "Content-Security-Policy": _preview_csp(),
        "X-DevOS-Preview-Auth": auth_kind,
        "Cross-Origin-Resource-Policy": "same-site",
    }
    if mime.startswith("text/html"):
        text = raw.decode("utf-8", errors="replace")
        parent = "/".join(rel.split("/")[:-1])
        base_path = f"/api/files/{project_id}/preview/"
        if parent:
            base_path = f"/api/files/{project_id}/preview/{parent}/"
        if embed_token:
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
                return (
                    f'{attr}="/api/files/{project_id}/preview/{joined}'
                    f'?token={embed_token}"'
                )
            pat = "\\b(href|src)=[\"']([^\"']+)[\"']"
            text = re.sub(pat, _rewrite, text, flags=re.I)
        text = _inject_html_base(text, base_path)
        return HTMLResponse(content=text, headers=headers)
    return Response(content=raw, media_type=mime, headers=headers)

