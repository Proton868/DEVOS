"""Persona registry, profiles, XP — intelligence layer only (no UCIP elevation)."""
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from typing import Optional
from sqlalchemy import select

from core.database import get_db, UserSettings, PersonaExperienceEvent
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
from brain.persona_xp import (
    get_or_create_profile,
    profile_to_dict,
    award_xp,
    calculate_xp_to_next_level,
    XP_RULES,
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
        # attach lightweight profile stats
        try:
            prof = await get_or_create_profile(db, user.id, p.id)
            d["display_name"] = prof.display_name or p.name
            d["level"] = prof.level or 1
            d["xp"] = prof.xp or 0
            d["provider"] = prof.provider
            d["model"] = prof.model
        except Exception:
            d["display_name"] = p.name
            d["level"] = 1
            d["xp"] = 0
        items.append(d)
    await db.commit()
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
        from core.database import UserSettings as US
        row = US(user_id=user.id, settings_json=settings)
        db.add(row)
    await db.commit()
    return {"ok": True, "personas": personas}


class ClassifyReq(BaseModel):
    message: str = Field(..., min_length=1)


@router.post("/classify")
async def personas_classify(req: ClassifyReq, request: Request, db=Depends(get_db)):
    user = await get_current_user(request, db)
    await ensure_personal_tenant(db, user)
    classes = classify_intent_heuristic(req.message)
    return {
        "classes": classes,
        "should_orchestrate": should_orchestrate_execution(req.message),
        "suggested_personas": suggest_personas_for_goal(req.message),
        "default_persona": DEFAULT_PERSONA_ID,
    }


@router.get("/xp-rules")
async def personas_xp_rules(request: Request, db=Depends(get_db)):
    user = await get_current_user(request, db)
    await ensure_personal_tenant(db, user)
    return {
        "rules": XP_RULES,
        "note": "XP and level are informational only. They do not grant, expand, or imply UCIP capabilities, trust, HITL bypass, or execution authority. A Level-20 specialist has the same UCIP ceiling as Level-1 for the same agent identity.",
        "security": {
            "grants_capabilities": False,
            "grants_trust": False,
            "bypasses_hitl": False,
            "bypasses_ucip": False,
            "level_is_not_power": True,
        },
    }


@router.get("/{persona_id}")
async def personas_get(persona_id: str, request: Request, db=Depends(get_db)):
    user = await get_current_user(request, db)
    await ensure_personal_tenant(db, user)
    p = get_persona(persona_id)
    if not p:
        raise HTTPException(404, "persona not found")
    return p.to_dict()


@router.get("/{persona_id}/profile")
async def personas_profile(persona_id: str, request: Request, db=Depends(get_db)):
    user = await get_current_user(request, db)
    await ensure_personal_tenant(db, user)
    p = get_persona(persona_id)
    if not p:
        raise HTTPException(404, "persona not found")
    prof = await get_or_create_profile(db, user.id, persona_id)
    await db.commit()
    return profile_to_dict(prof, p.to_dict())


class PersonaProfilePatch(BaseModel):
    display_name: Optional[str] = Field(None, max_length=128)
    description: Optional[str] = Field(None, max_length=2000)
    provider: Optional[str] = Field(None, max_length=64)
    model: Optional[str] = Field(None, max_length=128)


@router.patch("/{persona_id}/profile")
async def personas_patch_profile(persona_id: str, req: PersonaProfilePatch, request: Request, db=Depends(get_db)):
    user = await get_current_user(request, db)
    await ensure_personal_tenant(db, user)
    p = get_persona(persona_id)
    if not p:
        raise HTTPException(404, "persona not found")
    # Validate provider against known system providers if set (no credential storage)
    if req.provider is not None and req.provider.strip():
        from core.config import settings
        allowed = set(settings.available_providers) | {"ollama", "openrouter", "deepseek", "gemini", "openai", "huggingface", "nararouter"}
        if req.provider.strip().lower() not in {a.lower() for a in allowed}:
            # Soft allow custom ids that match editable keys — still no secrets here
            if not req.provider.replace("_", "").isalnum():
                raise HTTPException(400, "invalid provider id")
    prof = await get_or_create_profile(db, user.id, persona_id)
    if req.display_name is not None:
        name = req.display_name.strip()
        prof.display_name = name or None  # empty resets to registry name
    if req.description is not None:
        prof.description = req.description.strip() or None
    if req.provider is not None:
        prof.provider = req.provider.strip().lower() or None
    if req.model is not None:
        prof.model = req.model.strip() or None
    from core.database import utcnow_naive
    prof.updated_at = utcnow_naive()
    await db.commit()
    return profile_to_dict(prof, p.to_dict())


@router.get("/{persona_id}/experience")
async def personas_experience(persona_id: str, request: Request, db=Depends(get_db), limit: int = 40):
    user = await get_current_user(request, db)
    await ensure_personal_tenant(db, user)
    if not get_persona(persona_id):
        raise HTTPException(404, "persona not found")
    prof = await get_or_create_profile(db, user.id, persona_id)
    r = await db.execute(
        select(PersonaExperienceEvent)
        .where(
            PersonaExperienceEvent.user_id == user.id,
            PersonaExperienceEvent.persona_id == persona_id.lower(),
        )
        .order_by(PersonaExperienceEvent.created_at.desc())
        .limit(min(limit, 100))
    )
    events = [
        {
            "id": e.id,
            "event_type": e.event_type,
            "xp": e.xp,
            "reason": e.reason,
            "task_id": e.task_id,
            "evidence_id": e.evidence_id,
            "verified": e.verified,
            "source": e.source,
            "created_at": e.created_at.isoformat() if e.created_at else None,
        }
        for e in r.scalars().all()
    ]
    await db.commit()
    return {
        "persona_id": persona_id,
        "progression": calculate_xp_to_next_level(prof.xp or 0),
        "events": events,
        "authority_note": "XP is informational only and does not grant UCIP capabilities or trust.",
    }


@router.get("/{persona_id}/learning")
async def personas_learning(persona_id: str, request: Request, db=Depends(get_db)):
    user = await get_current_user(request, db)
    await ensure_personal_tenant(db, user)
    if not get_persona(persona_id):
        raise HTTPException(404, "persona not found")
    prof = await get_or_create_profile(db, user.id, persona_id)
    await db.commit()
    return {"persona_id": persona_id, "learning_events": prof.learning_events or []}


@router.get("/{persona_id}/accomplishments")
async def personas_accomplishments(persona_id: str, request: Request, db=Depends(get_db)):
    user = await get_current_user(request, db)
    await ensure_personal_tenant(db, user)
    if not get_persona(persona_id):
        raise HTTPException(404, "persona not found")
    prof = await get_or_create_profile(db, user.id, persona_id)
    await db.commit()
    return {"persona_id": persona_id, "accomplishments": prof.accomplishments or []}


@router.get("/{persona_id}/system-prompt")
async def personas_system_prompt(persona_id: str, request: Request, db=Depends(get_db)):
    user = await get_current_user(request, db)
    await ensure_personal_tenant(db, user)
    if not get_persona(persona_id):
        raise HTTPException(404, "persona not found")
    return {"persona_id": persona_id, "system_prompt": resolve_system_prompt(persona_id)}


class InternalAwardReq(BaseModel):
    """Server-internal style award — still requires auth; validates event type.
    Clients cannot set arbitrary xp amounts."""
    event_type: str
    reason: str
    task_id: Optional[str] = None
    evidence_id: Optional[str] = None
    verified: bool = False
    idempotency_key: str
    specialty_category: Optional[str] = None
    also_award_nuha_orchestration: bool = False


@router.post("/{persona_id}/experience/events")
async def personas_award_event(persona_id: str, req: InternalAwardReq, request: Request, db=Depends(get_db)):
    """Record a trusted XP event. XP amount comes from server XP_RULES only."""
    user = await get_current_user(request, db)
    await ensure_personal_tenant(db, user)
    if not get_persona(persona_id):
        raise HTTPException(404, "persona not found")
    if req.event_type not in XP_RULES:
        raise HTTPException(400, f"unknown event_type; allowed: {sorted(XP_RULES)}")
    try:
        result = await award_xp(
            db,
            user_id=user.id,
            persona_id=persona_id,
            event_type=req.event_type,
            reason=req.reason,
            task_id=req.task_id,
            evidence_id=req.evidence_id,
            source="api",
            verified=req.verified,
            idempotency_key=req.idempotency_key,
            specialty_category=req.specialty_category,
        )
        nuha = None
        if req.also_award_nuha_orchestration and persona_id.lower() != "nuha":
            nuha = await award_xp(
                db,
                user_id=user.id,
                persona_id="nuha",
                event_type="orchestration_success",
                reason=f"Orchestrated specialist {persona_id}: {req.reason}",
                task_id=req.task_id,
                evidence_id=req.evidence_id,
                source="api",
                verified=req.verified,
                idempotency_key=f"nuha-orch:{req.idempotency_key}",
                specialty_category="orchestration",
            )
        await db.commit()
        return {"specialist": result, "nuha": nuha}
    except ValueError as e:
        raise HTTPException(400, str(e))
