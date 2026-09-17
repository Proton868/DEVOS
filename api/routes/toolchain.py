"""Toolchain status and governed Flutter SDK provisioning API."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from core.database import get_db
from api.routes.auth import get_current_user
from governance.tenant_store import ensure_personal_tenant

router = APIRouter(prefix="/api/toolchain", tags=["toolchain"])


class FlutterProvisionReq(BaseModel):
    version: Optional[str] = None
    channel: str = "stable"
    platform: str = "linux"
    # Clients cannot self-authorize; HITL is mandatory for provision.
    require_hitl: bool = Field(default=True, description="Always true for ordinary users")


@router.get("")
async def list_toolchains(request: Request, db=Depends(get_db)):
    user = await get_current_user(request, db)
    await ensure_personal_tenant(db, user)
    from execution.toolchain import list_registered_profiles
    from brain.project_bootstrap import ensure_default_profiles
    ensure_default_profiles()
    return {"profiles": list_registered_profiles()}


@router.get("/flutter")
async def flutter_status(request: Request, db=Depends(get_db)):
    user = await get_current_user(request, db)
    await ensure_personal_tenant(db, user)
    from execution.flutter_toolchain import (
        probe_flutter_runtime,
        flutter_command_plan,
        CAP_FLUTTER_SDK_PROVISION,
        DEFAULT_SDK_ROOT,
        flutter_project_status,
    )
    from execution.files import FileService

    info = probe_flutter_runtime()
    # Optional project context from query
    project_id = (request.query_params.get("project_id") or "default").strip() or "default"
    try:
        fs = FileService(str(user.id), project_id)
        status = flutter_project_status(fs, runtime_info=info)
    except Exception:
        from execution.toolchain import structured_status, TOOLCHAIN_UNAVAILABLE
        status = structured_status(
            kind="flutter",
            detected=False,
            available=bool(info.flutter_available),
            installed_version=info.flutter_version,
            status="ready" if info.flutter_available else "unavailable",
            error=None if info.flutter_available else TOOLCHAIN_UNAVAILABLE,
            platform_limits=info.platform_limits,
            extra={"runtime": info.to_dict()},
        )
    status["provision_capability"] = CAP_FLUTTER_SDK_PROVISION
    status["install_root"] = str(DEFAULT_SDK_ROOT)
    status["plan"] = flutter_command_plan() if info.ok else None
    # Never expose host secrets
    return status


@router.post("/flutter/provision")
async def flutter_provision(req: FlutterProvisionReq, request: Request, db=Depends(get_db)):
    """Governed Flutter SDK install — HITL required. No sudo/apt/curl|bash."""
    user = await get_current_user(request, db)
    await ensure_personal_tenant(db, user)
    from execution.flutter_toolchain import (
        DEFAULT_FLUTTER_VERSION,
        request_sdk_provision_hitl,
    )
    version = (req.version or DEFAULT_FLUTTER_VERSION).strip()
    # Ordinary API users cannot bypass HITL (security boundary).
    return await request_sdk_provision_hitl(
        version=version,
        channel=req.channel,
        platform=req.platform,
        actor_id=str(user.id),
        user_id=str(user.id),
        loop_id="api-toolchain",
        reason="API-requested Flutter SDK provisioning",
    )
