"""Delivery APIs: application runtime, shares, deployment probes."""
from __future__ import annotations

from typing import Optional
from fastapi import APIRouter, Depends, Request, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

from core.database import get_db
from core.config import settings
from api.routes.auth import get_current_user
from governance.tenant_store import ensure_personal_tenant
from execution.app_runtime import ApplicationRuntime, AppRuntimeSpec, get_runtime
from execution.shares import create_share, get_share, revoke_share, read_share_bytes
from execution.deploy import get_adapter, list_adapters
from execution.app_detect import detect_application
from execution.log_stream import subscribe, recent
from execution.durable_store import list_runtimes, save_deployment, new_id
from execution.cloudflare_tunnel_mgr import start_tunnel, stop_tunnel, tunnel_status, cloudflared_available, TunnelError
from execution.delivery_executor import execute_delivery_plan
import json, time, os

router = APIRouter()


class RuntimeAction(BaseModel):
    action: str  # install|build|start|stop|restart|status
    port: int = 3911


@router.get("/{project_id}/runtime/status")
async def runtime_status(project_id: str, request: Request, db=Depends(get_db)):
    user = await get_current_user(request, db)
    await ensure_personal_tenant(db, user)
    rt = get_runtime(user.id, project_id)
    detection = detect_application(__import__("execution.files", fromlist=["FileService"]).FileService(user.id, project_id))
    if not rt:
        return {"state": "STOPPED", "detection": detection}
    return {**rt.status.to_dict(), "detection": detection}


@router.post("/{project_id}/runtime")
async def runtime_action(project_id: str, body: RuntimeAction, request: Request, db=Depends(get_db)):
    user = await get_current_user(request, db)
    await ensure_personal_tenant(db, user)
    rt = get_runtime(user.id, project_id) or ApplicationRuntime(
        AppRuntimeSpec(user_id=user.id, project_id=project_id)
    )
    act = (body.action or "").lower()
    if act == "install":
        st = await rt.install()
    elif act == "build":
        st = await rt.build()
    elif act == "start":
        st = await rt.start(port=body.port)
    elif act == "stop":
        st = await rt.stop()
    elif act == "restart":
        st = await rt.restart()
    elif act == "status":
        st = rt.status
    else:
        raise HTTPException(400, detail={"code": "INVALID_ACTION", "message": act})
    return st.to_dict()


class ShareReq(BaseModel):
    path: str = "index.html"
    permission: str = "shared"
    ttl_seconds: Optional[int] = 3600


@router.post("/{project_id}/shares")
async def create_share_route(project_id: str, body: ShareReq, request: Request, db=Depends(get_db)):
    user = await get_current_user(request, db)
    await ensure_personal_tenant(db, user)
    try:
        rec = create_share(user.id, project_id, body.path, permission=body.permission, ttl_seconds=body.ttl_seconds)
    except ValueError as e:
        code = str(e).split(":")[0]
        raise HTTPException(400, detail={"code": code, "message": str(e)})
    return {"share": rec.to_dict(), "url": f"/api/delivery/public/share/{rec.id}"}


@router.delete("/shares/{share_id}")
async def revoke_share_route(share_id: str, request: Request, db=Depends(get_db)):
    user = await get_current_user(request, db)
    ok = revoke_share(share_id, user.id)
    if not ok:
        raise HTTPException(404, "Share not found")
    return {"revoked": True}


@router.get("/public/share/{share_id}")
async def public_share(share_id: str):
    try:
        rec, data = read_share_bytes(share_id)
    except LookupError:
        raise HTTPException(404, detail={"code": "SHARE_UNAVAILABLE"})
    except PermissionError:
        raise HTTPException(403, detail={"code": "SECRET_DETECTED"})
    mime = "application/octet-stream"
    if rec.path.endswith(".html"):
        mime = "text/html; charset=utf-8"
    elif rec.path.endswith(".css"):
        mime = "text/css; charset=utf-8"
    elif rec.path.endswith(".js"):
        mime = "application/javascript; charset=utf-8"
    return Response(
        content=data,
        media_type=mime,
        headers={
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'",
        },
    )


class DeployReq(BaseModel):
    provider: str
    token: Optional[str] = None  # optional override; prefer server env


@router.get("/deploy/providers")
async def deploy_providers(request: Request, db=Depends(get_db)):
    await get_current_user(request, db)
    return {"providers": list_adapters()}


@router.post("/{project_id}/deploy")
async def deploy_project(project_id: str, body: DeployReq, request: Request, db=Depends(get_db)):
    """UCIP-class EXTERNAL_SIDE_EFFECT — credentials server-side only."""
    user = await get_current_user(request, db)
    await ensure_personal_tenant(db, user)
    try:
        adapter = get_adapter(body.provider)
    except KeyError:
        raise HTTPException(400, detail={"code": "UNKNOWN_PROVIDER"})
    # Prefer env credentials — never require client to send tokens
    import os
    creds = {}
    if body.provider == "vercel":
        creds["VERCEL_TOKEN"] = body.token or os.environ.get("VERCEL_TOKEN") or getattr(settings, "VERCEL_TOKEN", None)
    elif body.provider == "netlify":
        creds["NETLIFY_TOKEN"] = body.token or os.environ.get("NETLIFY_TOKEN") or getattr(settings, "NETLIFY_TOKEN", None)
    elif body.provider == "cloudflare_tunnel":
        creds["CLOUDFLARE_TOKEN"] = body.token or os.environ.get("CLOUDFLARE_TOKEN")
    result = await adapter.deploy(project_path=project_id, meta={"user_id": user.id}, credentials=creds)
    return result.to_dict()


@router.get("/plan")
async def delivery_plan(request: Request, db=Depends(get_db), goal: str = "preview"):
    await get_current_user(request, db)
    from execution.delivery_dag import build_delivery_dag
    return build_delivery_dag(goal)


from fastapi.responses import StreamingResponse


@router.get("/{project_id}/runtime/logs")
async def runtime_logs_sse(project_id: str, request: Request, db=Depends(get_db), runtime_id: Optional[str] = None):
    """SSE stream of application runtime logs (replay + live)."""
    user = await get_current_user(request, db)
    await ensure_personal_tenant(db, user)
    rt = get_runtime(user.id, project_id)
    rid = runtime_id or (rt.runtime_id if rt else None)
    if not rid:
        # fallback: most recent durable runtime for project
        rows = list_runtimes(project_id)
        rid = rows[0]["runtime_id"] if rows else f"proj:{project_id}"

    async def gen():
        async for item in subscribe(rid):
            if await request.is_disconnected():
                break
            yield {"event": "log", "data": json.dumps(item)}

    return StreamingResponse(
        _sse_gen(rid, request), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _sse_gen(rid, request):
    async for item in subscribe(rid):
        if await request.is_disconnected():
            break
        payload = json.dumps(item)
        yield f"event: log\ndata: {payload}\n\n"


@router.get("/{project_id}/runtime/logs/recent")
async def runtime_logs_recent(project_id: str, request: Request, db=Depends(get_db), runtime_id: Optional[str] = None):
    user = await get_current_user(request, db)
    await ensure_personal_tenant(db, user)
    rt = get_runtime(user.id, project_id)
    rid = runtime_id or (rt.runtime_id if rt else f"proj:{project_id}")
    return {"logs": recent(rid)}


class TunnelReq(BaseModel):
    local_port: int = 3911
    hostname: Optional[str] = None


@router.post("/{project_id}/tunnel/start")
async def tunnel_start(project_id: str, body: TunnelReq, request: Request, db=Depends(get_db)):
    user = await get_current_user(request, db)
    await ensure_personal_tenant(db, user)
    try:
        return await start_tunnel(user_id=user.id, project_id=project_id, local_port=body.local_port, hostname=body.hostname)
    except TunnelError as e:
        raise HTTPException(400, detail={"code": e.code, "message": str(e)})


@router.post("/tunnel/{tunnel_id}/stop")
async def tunnel_stop(tunnel_id: str, request: Request, db=Depends(get_db)):
    await get_current_user(request, db)
    return await stop_tunnel(tunnel_id)


@router.get("/tunnel/{tunnel_id}")
async def tunnel_get(tunnel_id: str, request: Request, db=Depends(get_db)):
    await get_current_user(request, db)
    return tunnel_status(tunnel_id)


@router.get("/cloudflared/info")
async def cloudflared_info(request: Request, db=Depends(get_db)):
    await get_current_user(request, db)
    return cloudflared_available()


class DeliveryRunReq(BaseModel):
    goal: str = "preview"
    provider: Optional[str] = None


@router.post("/{project_id}/delivery/run")
async def delivery_run(project_id: str, body: DeliveryRunReq, request: Request, db=Depends(get_db)):
    """Execute delivery DAG nodes through Application Runtime / adapters (Mission-compatible)."""
    user = await get_current_user(request, db)
    await ensure_personal_tenant(db, user)
    result = await execute_delivery_plan(
        user_id=user.id, project_id=project_id, goal=body.goal, provider=body.provider,
    )
    return result


@router.get("/tracing/health")
async def tracing_health_route(request: Request, db=Depends(get_db)):
    await get_current_user(request, db)
    from observability.tracing import tracing_health
    return tracing_health()


@router.get("/tracing/{trace_id}")
async def tracing_get(trace_id: str, request: Request, db=Depends(get_db)):
    await get_current_user(request, db)
    from observability.tracing import get_trace_spans
    return {"trace_id": trace_id, "spans": get_trace_spans(trace_id)}


@router.get("/saga/{saga_id}")
async def saga_get(saga_id: str, request: Request, db=Depends(get_db)):
    await get_current_user(request, db)
    from execution.saga import load_saga
    s = load_saga(saga_id)
    if not s:
        raise HTTPException(404, "saga not found")
    return s.to_dict()
