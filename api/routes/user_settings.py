"""Settings routes — user preferences and workspace layout persistence."""
import logging
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select, delete as sa_delete
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_db, UserSettings, WorkspaceLayout
from api.routes.auth import get_current_user
from governance.tenant_store import ensure_personal_tenant
from api.deps import tenant_ctx


logger = logging.getLogger("devos.settings")
router = APIRouter(prefix="/api/settings", tags=["settings"])


# ── User settings (key-value) ────────────────────────────────────────────────

class SettingsUpdate(BaseModel):
    settings: dict = Field(default_factory=dict, description="Key-value pairs to merge into existing settings")


@router.get("")
async def get_settings(request: Request, db: AsyncSession = Depends(get_db)):
    user = await get_current_user(request, db)
    await ensure_personal_tenant(db, user)
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
    await ensure_personal_tenant(db, user)
    # Never persist client-supplied authority fields (governance is authoritative)
    forbidden = {
        "trust_level", "authority", "extra_caps", "is_admin", "user_id",
        "tenant_id", "owner_id", "capabilities", "autonomy_override",
    }
    incoming = {k: v for k, v in (req.settings or {}).items() if k not in forbidden}
    r = await db.execute(
        select(UserSettings).where(UserSettings.user_id == user.id)
    )
    row = r.scalar_one_or_none()
    if row:
        row.settings_json = {**row.settings_json, **incoming}
    else:
        row = UserSettings(user_id=user.id, settings_json=incoming)
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
    await ensure_personal_tenant(db, user)
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
    await ensure_personal_tenant(db, user)
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
    await ensure_personal_tenant(db, user)

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
    await ensure_personal_tenant(db, user)
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
    await ensure_personal_tenant(db, user)
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

# ── Structured category endpoints (thin wrappers over settings_json) ─────────

_CATEGORY_DEFAULTS = {
    "account": {"display_name": "", "avatar_url": ""},
    "appearance": {
        "theme": "dark", "accent": "#58a6ff", "density": "comfortable",
        "fontSize": 13, "motion": True, "sidebar": "expanded",
    },
    "ai": {
        "temperature": 0.7, "streaming": True, "verbosity": "normal",
        "response_style": "balanced", "confirm_tools": True,
    },
    "models": {
        "default_chat": "", "default_coding": "", "default_reasoning": "",
        "default_fast": "", "default_vision": "", "per_provider": {},
    },
    "providers": {
        # User preferences only — never stores raw API keys here.
        # Keys go through /api/secrets with names PROVIDER_<ID>_KEY
        "enabled": {},
        "default_provider": "",
        "endpoints": {},
    },
    "workspace": {
        "default_workspace": "default", "terminal_shell": "bash",
        "editor_word_wrap": True,
    },
    "notifications": {"email": False, "desktop": True, "hitl": True},
    "privacy": {"share_telemetry": False, "retain_chat_days": 90},
    "assistant": {
        "visible": True, "position": "right", "size": "normal", "density": "comfortable",
    },
}


async def _get_row(db, user_id):
    r = await db.execute(select(UserSettings).where(UserSettings.user_id == user_id))
    return r.scalar_one_or_none()


async def _merge_category(db, user, category: str, patch: dict) -> dict:
    forbidden = {
        "trust_level", "authority", "extra_caps", "is_admin", "user_id",
        "tenant_id", "owner_id", "capabilities", "autonomy_override", "api_key", "apiKey",
    }
    clean = {k: v for k, v in (patch or {}).items() if k not in forbidden}
    row = await _get_row(db, user.id)
    current = dict(row.settings_json) if row else {}
    section = {**_CATEGORY_DEFAULTS.get(category, {}), **(current.get(category) or {}), **clean}
    current[category] = section
    if row:
        row.settings_json = current
    else:
        row = UserSettings(user_id=user.id, settings_json=current)
        db.add(row)
    await db.commit()
    await db.refresh(row)
    return row.settings_json[category]


@router.get("/account")
async def get_account_settings(request: Request, db: AsyncSession = Depends(get_db)):
    user = await get_current_user(request, db)
    await ensure_personal_tenant(db, user)
    row = await _get_row(db, user.id)
    data = {**_CATEGORY_DEFAULTS["account"], **((row.settings_json if row else {}).get("account") or {})}
    # Identity fields from auth — not overridable by arbitrary local state alone
    data["email"] = user.email
    data["username"] = user.username
    data["supabase_linked"] = bool(getattr(user, "supabase_id", None))
    data["default_tenant_id"] = getattr(user, "default_tenant_id", None)
    return {"account": data}


@router.put("/account")
async def put_account_settings(req: SettingsUpdate, request: Request, db: AsyncSession = Depends(get_db)):
    user = await get_current_user(request, db)
    await ensure_personal_tenant(db, user)
    # Only allow safe profile fields
    allowed = {k: v for k, v in (req.settings or {}).items() if k in ("display_name", "avatar_url")}
    data = await _merge_category(db, user, "account", allowed)
    data["email"] = user.email
    data["username"] = user.username
    return {"account": data}


@router.get("/appearance")
async def get_appearance(request: Request, db: AsyncSession = Depends(get_db)):
    user = await get_current_user(request, db)
    await ensure_personal_tenant(db, user)
    row = await _get_row(db, user.id)
    data = {**_CATEGORY_DEFAULTS["appearance"], **((row.settings_json if row else {}).get("appearance") or {})}
    return {"appearance": data}


@router.put("/appearance")
async def put_appearance(req: SettingsUpdate, request: Request, db: AsyncSession = Depends(get_db)):
    user = await get_current_user(request, db)
    await ensure_personal_tenant(db, user)
    return {"appearance": await _merge_category(db, user, "appearance", req.settings)}


@router.get("/ai")
async def get_ai(request: Request, db: AsyncSession = Depends(get_db)):
    user = await get_current_user(request, db)
    await ensure_personal_tenant(db, user)
    row = await _get_row(db, user.id)
    data = {**_CATEGORY_DEFAULTS["ai"], **((row.settings_json if row else {}).get("ai") or {})}
    return {"ai": data}


@router.put("/ai")
async def put_ai(req: SettingsUpdate, request: Request, db: AsyncSession = Depends(get_db)):
    user = await get_current_user(request, db)
    await ensure_personal_tenant(db, user)
    return {"ai": await _merge_category(db, user, "ai", req.settings)}


@router.get("/models")
async def get_models_prefs(request: Request, db: AsyncSession = Depends(get_db)):
    user = await get_current_user(request, db)
    await ensure_personal_tenant(db, user)
    row = await _get_row(db, user.id)
    data = {**_CATEGORY_DEFAULTS["models"], **((row.settings_json if row else {}).get("models") or {})}
    return {"models": data}


@router.put("/models")
async def put_models_prefs(req: SettingsUpdate, request: Request, db: AsyncSession = Depends(get_db)):
    user = await get_current_user(request, db)
    await ensure_personal_tenant(db, user)
    return {"models": await _merge_category(db, user, "models", req.settings)}


@router.get("/providers")
async def get_provider_prefs(request: Request, db: AsyncSession = Depends(get_db)):
    """User provider preferences — never includes raw API keys."""
    user = await get_current_user(request, db)
    await ensure_personal_tenant(db, user)
    row = await _get_row(db, user.id)
    data = {**_CATEGORY_DEFAULTS["providers"], **((row.settings_json if row else {}).get("providers") or {})}
    # Annotate which provider secrets exist (names only)
    from core.database import Secret
    r = await db.execute(select(Secret).where(Secret.owner_id == user.id))
    secret_names = {s.name for s in r.scalars().all()}
    configured = {}
    for name in secret_names:
        if name.startswith("PROVIDER_") and name.endswith("_KEY"):
            pid = name[len("PROVIDER_"):-len("_KEY")].lower()
            configured[pid] = True
    data["credentials_configured"] = configured
    # Strip any accidental key material
    data.pop("api_key", None)
    data.pop("apiKey", None)
    data.pop("keys", None)
    return {"providers": data}


@router.put("/providers")
async def put_provider_prefs(req: SettingsUpdate, request: Request, db: AsyncSession = Depends(get_db)):
    user = await get_current_user(request, db)
    await ensure_personal_tenant(db, user)
    safe = {k: v for k, v in (req.settings or {}).items()
            if k not in ("api_key", "apiKey", "keys", "secret", "token")}
    return {"providers": await _merge_category(db, user, "providers", safe)}


@router.get("/assistant")
async def get_assistant_prefs(request: Request, db: AsyncSession = Depends(get_db)):
    user = await get_current_user(request, db)
    await ensure_personal_tenant(db, user)
    row = await _get_row(db, user.id)
    data = {**_CATEGORY_DEFAULTS["assistant"], **((row.settings_json if row else {}).get("assistant") or {})}
    return {"assistant": data}


@router.put("/assistant")
async def put_assistant_prefs(req: SettingsUpdate, request: Request, db: AsyncSession = Depends(get_db)):
    user = await get_current_user(request, db)
    await ensure_personal_tenant(db, user)
    return {"assistant": await _merge_category(db, user, "assistant", req.settings)}
