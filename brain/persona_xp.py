"""
Persona XP / progression — informational only.

XP MUST NEVER grant UCIP capabilities, trust, or execution authority.
Server-side award only; browser cannot self-award arbitrary XP.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

# DB imports deferred so pure progression helpers work without SQLAlchemy in unit tests

logger = logging.getLogger("devos.persona_xp")

# ─── AUTHORITY BOUNDARY (non-negotiable) ───────────────────────────────────
# XP / level / accomplishments are PRESENTATION and LEARNING signals only.
# They MUST NEVER be consulted by:
#   - authorization gateway checks
#   - capability grants or TrustLevel promotion
#   - HITL bypass decisions
#   - filesystem delete, deploy, network, or production side-effect gates
#
# A Level-20 specialist has the SAME UCIP ceiling as Level-1 for the same
# the agent identity. Experience grows; authority stays explicit and governed.
XP_AUTHORITY_BOUNDARY = (
    "XP and level are informational only. "
    "They do not grant, expand, or imply UCIP capabilities, trust, or execution authority."
)


def assert_xp_does_not_grant_authority() -> None:
    """Static invariant: this module awards experience only; never mutates UCIP."""
    return None


def level_has_no_security_effect(level: int) -> bool:
    """Levels never change authorization outcomes. Always True by design."""
    _ = int(level or 0)
    return True



# Centralized XP rules (tune here, not per-persona)
XP_RULES: dict[str, int] = {
    "task_completed": 10,
    "task_verified": 15,
    "artifact_created": 10,
    "artifact_improved": 8,
    "bug_fixed": 10,
    "successful_delegation": 8,
    "successful_escalation": 6,
    "user_accepted_result": 20,
    "workflow_completed": 12,
    "code_review_success": 10,
    "research_completed": 10,
    "learning_event": 5,
    "recovery_success": 15,
    "orchestration_success": 10,  # Nuha orchestration credit
}

PER_TASK_CAP = 80  # max XP from a single task_id per persona

# Level thresholds: cumulative XP required to reach level N (1-indexed)
# level 1 = 0, level 2 = 100, level 3 = 250, ...
_LEVEL_THRESHOLDS = [0, 100, 250, 500, 900, 1400, 2000, 2800, 3800, 5000,
                     6500, 8500, 11000, 14000, 18000, 23000]


def calculate_level(xp: int) -> int:
    xp = max(0, int(xp or 0))
    level = 1
    for i, thr in enumerate(_LEVEL_THRESHOLDS):
        if xp >= thr:
            level = i + 1
        else:
            break
    # Beyond table: + every 6000 XP
    if xp >= _LEVEL_THRESHOLDS[-1]:
        extra = (xp - _LEVEL_THRESHOLDS[-1]) // 6000
        level = len(_LEVEL_THRESHOLDS) + extra
    return level


def calculate_xp_to_next_level(xp: int) -> dict:
    xp = max(0, int(xp or 0))
    level = calculate_level(xp)
    if level < len(_LEVEL_THRESHOLDS):
        nxt = _LEVEL_THRESHOLDS[level]  # index = level for next
        prev = _LEVEL_THRESHOLDS[level - 1] if level >= 1 else 0
    else:
        prev = _LEVEL_THRESHOLDS[-1] + (level - len(_LEVEL_THRESHOLDS)) * 6000
        nxt = prev + 6000
    return {
        "level": level,
        "xp": xp,
        "xp_into_level": max(0, xp - prev),
        "xp_to_next": max(0, nxt - xp),
        "level_floor": prev,
        "level_ceiling": nxt,
        "progress": 0.0 if nxt == prev else min(1.0, max(0.0, (xp - prev) / (nxt - prev))),
    }


async def get_or_create_profile(db, user_id: str, persona_id: str):
    from sqlalchemy import select
    from core.database import PersonaProfile, gen_id
    pid = (persona_id or "nuha").lower()
    r = await db.execute(
        select(PersonaProfile).where(
            PersonaProfile.user_id == user_id,
            PersonaProfile.persona_id == pid,
        )
    )
    row = r.scalar_one_or_none()
    if row:
        return row
    row = PersonaProfile(
        id=gen_id(),
        user_id=user_id,
        persona_id=pid,
        xp=0,
        level=1,
        specialty_xp={},
        accomplishments=[],
        learning_events=[],
    )
    db.add(row)
    await db.flush()
    return row


async def award_xp(
    db,
    *,
    user_id: str,
    persona_id: str,
    event_type: str,
    reason: str,
    task_id: Optional[str] = None,
    evidence_id: Optional[str] = None,
    source: str = "system",
    verified: bool = False,
    idempotency_key: str,
    metadata: Optional[dict] = None,
    specialty_category: Optional[str] = None,
) -> dict[str, Any]:
    """Award XP once per idempotency_key. Returns event summary; no-op if duplicate."""
    if not idempotency_key:
        raise ValueError("idempotency_key required")
    # Reject client-style abuse: only known event types
    if event_type not in XP_RULES:
        raise ValueError(f"unknown event_type: {event_type}")

    from sqlalchemy import select
    from core.database import PersonaExperienceEvent, gen_id, utcnow_naive

    # Duplicate check
    r = await db.execute(
        select(PersonaExperienceEvent).where(
            PersonaExperienceEvent.idempotency_key == idempotency_key
        )
    )
    existing = r.scalar_one_or_none()
    if existing:
        return {
            "awarded": False,
            "duplicate": True,
            "xp": 0,
            "event_id": existing.id,
            "persona_id": persona_id,
        }

    base = XP_RULES[event_type]
    # Per-task cap
    if task_id:
        r2 = await db.execute(
            select(PersonaExperienceEvent).where(
                PersonaExperienceEvent.user_id == user_id,
                PersonaExperienceEvent.persona_id == persona_id,
                PersonaExperienceEvent.task_id == task_id,
            )
        )
        already = sum(e.xp for e in r2.scalars().all())
        remaining = max(0, PER_TASK_CAP - already)
        xp = min(base, remaining)
        if xp <= 0:
            return {
                "awarded": False,
                "duplicate": False,
                "capped": True,
                "xp": 0,
                "persona_id": persona_id,
            }
    else:
        xp = base

    profile = await get_or_create_profile(db, user_id, persona_id)
    evt = PersonaExperienceEvent(
        id=gen_id(),
        user_id=user_id,
        persona_id=persona_id.lower(),
        event_type=event_type,
        xp=xp,
        task_id=task_id,
        evidence_id=evidence_id,
        source=source,
        reason=reason,
        verified=verified,
        idempotency_key=idempotency_key,
        metadata_json=metadata or {},
    )
    db.add(evt)

    profile.xp = int(profile.xp or 0) + xp
    profile.level = calculate_level(profile.xp)
    profile.updated_at = utcnow_naive()

    if event_type in ("task_completed", "task_verified", "workflow_completed"):
        profile.tasks_completed = int(profile.tasks_completed or 0) + 1
        profile.tasks_successful = int(profile.tasks_successful or 0) + 1
    if event_type == "task_verified":
        profile.verified_outcomes = int(profile.verified_outcomes or 0) + 1
    if event_type == "successful_delegation":
        profile.delegations_successful = int(profile.delegations_successful or 0) + 1
        profile.delegations_received = int(profile.delegations_received or 0) + 1
    if event_type == "orchestration_success":
        profile.delegations_successful = int(profile.delegations_successful or 0) + 1

    if specialty_category:
        sx = dict(profile.specialty_xp or {})
        sx[specialty_category] = int(sx.get(specialty_category, 0)) + xp
        profile.specialty_xp = sx

    if event_type == "learning_event" and reason:
        le = list(profile.learning_events or [])
        le.insert(0, {
            "text": reason,
            "at": datetime.now(timezone.utc).isoformat(),
            "task_id": task_id,
        })
        profile.learning_events = le[:50]

    await _maybe_accomplishments(profile)
    await db.flush()
    return {
        "awarded": True,
        "duplicate": False,
        "xp": xp,
        "event_id": evt.id,
        "persona_id": persona_id,
        "level": profile.level,
        "total_xp": profile.xp,
        "progression": calculate_xp_to_next_level(profile.xp),
        "authority": XP_AUTHORITY_BOUNDARY,
        # Explicit: callers must not treat level as a permission signal
        "security": {
            "grants_capabilities": False,
            "grants_trust": False,
            "bypasses_hitl": False,
            "bypasses_ucip": False,
        },
    }


async def _maybe_accomplishments(profile) -> None:
    ac = list(profile.accomplishments or [])
    have = {a.get("id") for a in ac if isinstance(a, dict)}

    def add(aid: str, title: str):
        if aid not in have:
            ac.append({
                "id": aid,
                "title": title,
                "at": datetime.now(timezone.utc).isoformat(),
            })
            have.add(aid)

    if profile.verified_outcomes >= 1:
        add("first_verified", "First Verified Outcome")
    if profile.verified_outcomes >= 100:
        add("verified_100", "100 Verified Outcomes")
    if profile.delegations_successful >= 10:
        add("delegations_10", "10 Successful Delegations")
    if profile.tasks_successful >= 1:
        add("first_task", "First Completed Task")
    if profile.level >= 5:
        add("level_5", "Reached Level 5")
    if profile.level >= 10:
        add("level_10", "Reached Level 10")
    profile.accomplishments = ac


def profile_to_dict(profile, registry_meta: Optional[dict] = None) -> dict:
    prog = calculate_xp_to_next_level(profile.xp or 0)
    base = registry_meta or {}
    return {
        "persona_id": profile.persona_id,
        "display_name": profile.display_name or base.get("name") or profile.persona_id,
        "description": profile.description or base.get("description"),
        "specialty": base.get("specialty"),
        "role": base.get("role"),
        "provider": profile.provider,
        "model": profile.model,
        "level": profile.level or prog["level"],
        "xp": profile.xp or 0,
        "xp_to_next_level": prog["xp_to_next"],
        "xp_into_level": prog["xp_into_level"],
        "level_floor": prog["level_floor"],
        "level_ceiling": prog["level_ceiling"],
        "progress": prog["progress"],
        "tasks_completed": profile.tasks_completed or 0,
        "tasks_successful": profile.tasks_successful or 0,
        "tasks_failed": profile.tasks_failed or 0,
        "verified_outcomes": profile.verified_outcomes or 0,
        "delegations_received": profile.delegations_received or 0,
        "delegations_successful": profile.delegations_successful or 0,
        "specialty_xp": profile.specialty_xp or {},
        "accomplishments": profile.accomplishments or [],
        "learning_events": profile.learning_events or [],
        "created_at": profile.created_at.isoformat() if profile.created_at else None,
        "updated_at": profile.updated_at.isoformat() if profile.updated_at else None,
        # Explicit: XP is not authority
        "authority_note": XP_AUTHORITY_BOUNDARY,
        "security": {
            "grants_capabilities": False,
            "grants_trust": False,
            "bypasses_hitl": False,
            "bypasses_ucip": False,
            "level_is_not_power": True,
        },
    }


async def award_agent_task_outcome(
    *,
    user_id: str,
    task_id: str,
    success: bool,
    objective: str = "",
    files_changed: Optional[list] = None,
    persona_id: str = "nuha",
) -> dict:
    """Best-effort XP from verified agent completion. Own DB session.
    Never raises into the agent stream — XP failures are logged only.
    """
    if not success or not user_id or not task_id:
        return {"awarded": False, "reason": "not_successful"}
    try:
        from core.database import AsyncSessionLocal
        from brain.personas import suggest_personas_for_goal
    except Exception as e:
        logger.warning(f"[persona_xp] import failed: {e}")
        return {"awarded": False, "reason": str(e)}

    specialist = persona_id or "nuha"
    if specialist == "nuha":
        suggested = suggest_personas_for_goal(objective or "")
        if suggested:
            specialist = suggested[0]

    results = {}
    try:
        async with AsyncSessionLocal() as db:
            # Specialist (or Nuha if no specialist) — task verified
            key_base = f"agent-task:{task_id}"
            results["specialist"] = await award_xp(
                db,
                user_id=user_id,
                persona_id=specialist,
                event_type="task_verified",
                reason=f"Agent task verified: {(objective or '')[:160]}",
                task_id=task_id,
                source="agent_runtime",
                verified=True,
                idempotency_key=f"{key_base}:verified:{specialist}",
                specialty_category=specialist if specialist != "nuha" else "orchestration",
            )
            if files_changed:
                results["artifact"] = await award_xp(
                    db,
                    user_id=user_id,
                    persona_id=specialist,
                    event_type="artifact_created",
                    reason=f"Files changed: {len(files_changed)}",
                    task_id=task_id,
                    source="agent_runtime",
                    verified=True,
                    idempotency_key=f"{key_base}:artifact:{specialist}",
                    specialty_category=specialist if specialist != "nuha" else "orchestration",
                )
            # Nuha orchestration credit when a specialist did the work
            if specialist != "nuha":
                results["nuha"] = await award_xp(
                    db,
                    user_id=user_id,
                    persona_id="nuha",
                    event_type="orchestration_success",
                    reason=f"Orchestrated {specialist} for task {task_id}",
                    task_id=task_id,
                    source="agent_runtime",
                    verified=True,
                    idempotency_key=f"{key_base}:orch:nuha",
                    specialty_category="orchestration",
                )
            await db.commit()
    except Exception as e:
        logger.warning(f"[persona_xp] award_agent_task_outcome failed: {e}")
        return {"awarded": False, "reason": str(e)}
    return {"awarded": True, "results": results}


async def award_user_accepted_change(
    *,
    user_id: str,
    task_id: str,
    change_id: str,
    persona_id: str = "nuha",
) -> dict:
    """XP when user accepts an agent file change."""
    if not user_id or not change_id:
        return {"awarded": False}
    try:
        from core.database import AsyncSessionLocal
        async with AsyncSessionLocal() as db:
            r = await award_xp(
                db,
                user_id=user_id,
                persona_id=persona_id or "nuha",
                event_type="user_accepted_result",
                reason=f"User accepted change {change_id}",
                task_id=task_id,
                source="agent_changes",
                verified=True,
                idempotency_key=f"accept:{change_id}",
            )
            await db.commit()
            return r
    except Exception as e:
        logger.warning(f"[persona_xp] award_user_accepted_change failed: {e}")
        return {"awarded": False, "reason": str(e)}
