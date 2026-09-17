"""Toolchain status and governed Flutter SDK provisioning API."""
from __future__ import annotations
from typing import Optional
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from core.database import get_db
from api.routes.auth import get_current_user
from governance.tenant_store import ensure_personal_tenant

router = APIRouter(prefix="/api/toolchain", tags=["toolchain"])

class FlutterProvisionReq(BaseModel):
    version: Optional[str] = None
    channel: str = "stable"
    platform: str = "linux"
    authorized: bool = False
    require_hitl: bool = True

@router.get("/flutter")
async def flutter_status(request: Request, db=Depends(get_db)):
    user = await get_current_user(request, db)
    await ensure_personal_tenant(db, user)
    from execution.flutter_toolchain import probe_flutter_runtime, flutter_command_plan, CAP_FLUTTER_SDK_PROVISION, DEFAULT_SDK_ROOT
    info = probe_flutter_runtime()
    return {
        "ok": info.ok,
        "runtime": info.to_dict(),
        "plan": flutter_command_plan() if info.ok else None,
        "provision_capability": CAP_FLUTTER_SDK_PROVISION,
        "install_root": str(DEFAULT_SDK_ROOT),
    }

@router.post("/flutter/provision")
async def flutter_provision(req: FlutterProvisionReq, request: Request, db=Depends(get_db)):
    user = await get_current_user(request, db)
    await ensure_personal_tenant(db, user)
    from execution.flutter_toolchain import (
        DEFAULT_FLUTTER_VERSION, ProvisionRequest, provision_flutter_sdk, request_sdk_provision_hitl,
    )
    version = (req.version or DEFAULT_FLUTTER_VERSION).strip()
    if req.authorized and not req.require_hitl:
        return provision_flutter_sdk(ProvisionRequest(
            version=version, channel=req.channel, platform=req.platform, authorized=True,
        )).to_dict()
    return await request_sdk_provision_hitl(
        version=version, channel=req.channel, platform=req.platform,
        actor_id=str(user.id), user_id=str(user.id), loop_id="api-toolchain",
        reason="API-requested Flutter SDK provisioning",
    )
