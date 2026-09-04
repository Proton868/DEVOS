"""Persona registry API — Nuha + specialists (intelligence profiles only)."""
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from typing import Optional
from sqlalchemy import select

from core.database import get_db, UserSettings
from api.routes.auth import get_current_user
from governance.tenant_store import ensure_personal_tenant
from brain.personas import (
    DEFAULT_PERSONA_ID,
    get_persona,
    list_personas,
    resolve_system_prompt,
    suggest_personas_for_goal,
    classify_intent_heuristic,
    should_orchestrate_execution,
)

router = APIRouter()


def _prefs(settings_json: dict) -> dict:
    personas = (settings_json or {}).get("personas") or {}
    return {
        "default_persona": personas.get("default_persona") or DEFAULT_PERSONA_ID,
        "enabled": personas.get("enabled") or {},
    }


@router.get("")
async def personas_list(request: Request, db=Depends(get_db)):
    user = await get_current_user(request, db)
    await ensure_personal_tenant(db, user)
    r = await db.execute(select(UserSettings).where(UserSettings.user_id == user.id))
    row = r.scalar_one_or_none()
    prefs = _prefs(row.settings_json if row else {})
    enabled_map = prefs["enabled"]
    items = []
    for p in list_personas():
        en = enabled_map.get(p.id, p.enabled_by_default)
        d = p.to_dict()
        d["enabled"] = bool(en)
        items.append(d)
    return {
        "default_persona": prefs["default_persona"],
        "personas": items,
    }


@router.get("/default")
async def personas_default(request: Request, db=Depends(get_db)):
    user = await get_current_user(request, db)
    await ensure_personal_tenant(db, user)
    r = await db.execute(select(UserSettings).where(UserSettings.user_id == user.id))
    row = r.scalar_one_or_none()
    prefs = _prefs(row.settings_json if row else {})
    pid = prefs["default_persona"]
    p = get_persona(pid) or get_persona(DEFAULT_PERSONA_ID)
    return {"default_persona": p.id if p else DEFAULT_PERSONA_ID, "persona": p.to_dict() if p else None}


@router.get("/{persona_id}")
async def personas_get(persona_id: str, request: Request, db=Depends(get_db)):
    user = await get_current_user(request, db)
    await ensure_personal_tenant(db, user)
    p = get_persona(persona_id)
    if not p:
        raise HTTPException(404, "persona not found")
    return p.to_dict()


class PersonaPrefsUpdate(BaseModel):
    default_persona: Optional[str] = None
    enabled: Optional[dict] = None


@router.put("/prefs")
async def personas_update_prefs(req: PersonaPrefsUpdate, request: Request, db=Depends(get_db)):
    user = await get_current_user(request, db)
    await ensure_personal_tenant(db, user)
    if req.default_persona and not get_persona(req.default_persona):
        raise HTTPException(400, "unknown default_persona")
    r = await db.execute(select(UserSettings).where(UserSettings.user_id == user.id))
    row = r.scalar_one_or_none()
    settings = dict(row.settings_json) if row and row.settings_json else {}
    personas = dict(settings.get("personas") or {})
    if req.default_persona:
        personas["default_persona"] = req.default_persona.lower()
    if req.enabled is not None:
        personas["enabled"] = {str(k).lower(): bool(v) for k, v in req.enabled.items()}
    settings["personas"] = personas
    if row:
        row.settings_json = settings
    else:
        row = UserSettings(user_id=user.id, settings_json=settings)
        db.add(row)
    await db.commit()
    return {"ok": True, "personas": personas}


class ClassifyReq(BaseModel):
    message: str = Field(..., min_length=1)


@router.post("/classify")
async def personas_classify(req: ClassifyReq, request: Request, db=Depends(get_db)):
    """Heuristic intent classification for Nuha (does not authorize execution)."""
    user = await get_current_user(request, db)
    await ensure_personal_tenant(db, user)
    classes = classify_intent_heuristic(req.message)
    return {
        "classes": classes,
        "should_orchestrate": should_orchestrate_execution(req.message),
        "suggested_personas": suggest_personas_for_goal(req.message),
        "default_persona": DEFAULT_PERSONA_ID,
    }


@router.get("/{persona_id}/system-prompt")
async def personas_system_prompt(persona_id: str, request: Request, db=Depends(get_db)):
    user = await get_current_user(request, db)
    await ensure_personal_tenant(db, user)
    if not get_persona(persona_id):
        raise HTTPException(404, "persona not found")
    return {"persona_id": persona_id, "system_prompt": resolve_system_prompt(persona_id)}
