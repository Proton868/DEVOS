"""HTTP API for governed remote SSH inspection."""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from api.routes.auth import get_current_user
from core.database import get_db
from governance.tenant_store import ensure_personal_tenant
from governance.ssh_inspect import inspect_remote, list_inspect_kinds, InspectError, InspectKind

logger = logging.getLogger("devos.ssh_inspect.route")
router = APIRouter()


class InspectBody(BaseModel):
    lines: int = Field(default=50, ge=1, le=200)
    unit: Optional[str] = Field(default=None, max_length=128)


@router.get("/inspect/kinds")
async def get_inspect_kinds(request: Request, db=Depends(get_db)):
    await get_current_user(request, db)
    return {"kinds": list_inspect_kinds()}


@router.get("/connections/{connection_id}/inspect/{kind}")
async def inspect_connection(
    connection_id: str,
    kind: str,
    request: Request,
    db=Depends(get_db),
    lines: int = Query(default=50, ge=1, le=200),
    unit: Optional[str] = Query(default=None, max_length=128),
):
    user = await get_current_user(request, db)
    await ensure_personal_tenant(db, user)
    try:
        result = await inspect_remote(
            owner_id=user.id,
            connection_id=connection_id,
            kind=kind,
            actor="user",
            lines=lines,
            unit=unit,
        )
    except InspectError as e:
        raise HTTPException(400, e.code)
    except Exception as e:
        logger.warning("ssh_inspect_failed kind=%s err=%s", kind, type(e).__name__)
        raise HTTPException(400, "inspect_failed")
    if result.status == "denied":
        raise HTTPException(403, "inspect_denied")
    return result.to_public()


@router.post("/connections/{connection_id}/inspect/{kind}")
async def inspect_connection_post(
    connection_id: str,
    kind: str,
    body: InspectBody,
    request: Request,
    db=Depends(get_db),
):
    user = await get_current_user(request, db)
    await ensure_personal_tenant(db, user)
    try:
        result = await inspect_remote(
            owner_id=user.id,
            connection_id=connection_id,
            kind=kind,
            actor="user",
            lines=body.lines,
            unit=body.unit,
        )
    except InspectError as e:
        raise HTTPException(400, e.code)
    except Exception:
        raise HTTPException(400, "inspect_failed")
    if result.status == "denied":
        raise HTTPException(403, "inspect_denied")
    return result.to_public()
