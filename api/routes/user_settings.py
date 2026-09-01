"""Settings routes — user preferences and workspace layout persistence."""
import logging
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select, delete as sa_delete
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_db, UserSettings, WorkspaceLayout
from api.routes.auth import get_current_user

logger = logging.getLogger("devos.settings")
router = APIRouter(prefix="/api/settings", tags=["settings"])


# ── User settings (key-value) ────────────────────────────────────────────────

class SettingsUpdate(BaseModel):
    settings: dict = Field(default_factory=dict, description="Key-value pairs to merge into existing settings")


@router.get("")
async def get_settings(request: Request, db: AsyncSession = Depends(get_db)):
    user = await get_current_user(request, db)
    r = await db.execute(
        select(UserSettings).where(UserSettings.user_id == user.id)
    )
    row = r.scalar_one_or_none()
    return {"settings": row.settings_json if row else {}}


@router.put("")
async def put_settings(req: SettingsUpdate, request: Request, db: AsyncSession = Depends(get_db)):
    """Merge the provided key-value pairs into the user's settings. Existing
    keys not mentioned in the request are preserved."""
    user = await get_current_user(request, db)
    r = await db.execute(
        select(UserSettings).where(UserSettings.user_id == user.id)
    )
    row = r.scalar_one_or_none()
    if row:
        row.settings_json = {**row.settings_json, **req.settings}
    else:
        row = UserSettings(user_id=user.id, settings_json=req.settings)
        db.add(row)
    await db.commit()
    await db.refresh(row)
    return {"settings": row.settings_json}


# ── Workspace layouts ────────────────────────────────────────────────────────

class LayoutCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    layout_json: dict = Field(..., description="Window/panel positions and sizes")
    is_default: bool = False


class LayoutUpdate(BaseModel):
    name: str | None = None
    layout_json: dict | None = None
    is_default: bool | None = None


@router.get("/workspace-layouts")
async def list_layouts(request: Request, db: AsyncSession = Depends(get_db)):
    user = await get_current_user(request, db)
    r = await db.execute(
        select(WorkspaceLayout)
        .where(WorkspaceLayout.user_id == user.id)
        .order_by(WorkspaceLayout.updated_at.desc())
    )
    return {
        "layouts": [
            {"id": l.id, "name": l.name, "layout_json": l.layout_json,
             "is_default": l.is_default, "created_at": l.created_at.isoformat(),
             "updated_at": l.updated_at.isoformat()}
            for l in r.scalars().all()
        ]
    }


@router.get("/workspace-layouts/{layout_id}")
async def get_layout(layout_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    user = await get_current_user(request, db)
    r = await db.execute(
        select(WorkspaceLayout).where(
            WorkspaceLayout.id == layout_id,
            WorkspaceLayout.user_id == user.id,
        )
    )
    layout = r.scalar_one_or_none()
    if not layout:
        raise HTTPException(404, "Layout not found")
    return {
        "id": layout.id, "name": layout.name, "layout_json": layout.layout_json,
        "is_default": layout.is_default,
        "created_at": layout.created_at.isoformat(),
        "updated_at": layout.updated_at.isoformat(),
    }


@router.post("/workspace-layouts")
async def create_layout(req: LayoutCreate, request: Request, db: AsyncSession = Depends(get_db)):
    user = await get_current_user(request, db)

    # If this layout is marked as default, clear any existing default
    if req.is_default:
        existing = await db.execute(
            select(WorkspaceLayout).where(
                WorkspaceLayout.user_id == user.id,
                WorkspaceLayout.is_default == True,  # noqa: E712
            )
        )
        for old in existing.scalars().all():
            old.is_default = False

    layout = WorkspaceLayout(
        user_id=user.id,
        name=req.name,
        layout_json=req.layout_json,
        is_default=req.is_default,
    )
    db.add(layout)
    await db.commit()
    await db.refresh(layout)
    return {
        "id": layout.id, "name": layout.name, "layout_json": layout.layout_json,
        "is_default": layout.is_default,
        "created_at": layout.created_at.isoformat(),
        "updated_at": layout.updated_at.isoformat(),
    }


@router.put("/workspace-layouts/{layout_id}")
async def update_layout(layout_id: str, req: LayoutUpdate, request: Request, db: AsyncSession = Depends(get_db)):
    user = await get_current_user(request, db)
    r = await db.execute(
        select(WorkspaceLayout).where(
            WorkspaceLayout.id == layout_id,
            WorkspaceLayout.user_id == user.id,
        )
    )
    layout = r.scalar_one_or_none()
    if not layout:
        raise HTTPException(404, "Layout not found")

    if req.name is not None:
        layout.name = req.name
    if req.layout_json is not None:
        layout.layout_json = req.layout_json

    # If marking as default, clear any other default
    if req.is_default is True:
        existing = await db.execute(
            select(WorkspaceLayout).where(
                WorkspaceLayout.user_id == user.id,
                WorkspaceLayout.is_default == True,  # noqa: E712
                WorkspaceLayout.id != layout_id,
            )
        )
        for old in existing.scalars().all():
            old.is_default = False
        layout.is_default = True
    elif req.is_default is False:
        layout.is_default = False

    await db.commit()
    await db.refresh(layout)
    return {
        "id": layout.id, "name": layout.name, "layout_json": layout.layout_json,
        "is_default": layout.is_default,
        "created_at": layout.created_at.isoformat(),
        "updated_at": layout.updated_at.isoformat(),
    }


@router.delete("/workspace-layouts/{layout_id}")
async def delete_layout(layout_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    user = await get_current_user(request, db)
    result = await db.execute(
        sa_delete(WorkspaceLayout).where(
            WorkspaceLayout.id == layout_id,
            WorkspaceLayout.user_id == user.id,
        )
    )
    await db.commit()
    if result.rowcount == 0:
        raise HTTPException(404, "Layout not found")
    return {"deleted": True}