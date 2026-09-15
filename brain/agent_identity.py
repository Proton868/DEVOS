"""
Persistent agent identity, soul, and work history.

Layers (never mixed):
  1. immutable identity   — agents row (id, slug, kind)
  2. role / capabilities  — executable contract + profile.stats.capabilities
  3. durable preferences  — agent_souls.preferences (explicit updates only)
  4. lessons              — agent_souls.lessons (only from validated outcomes / explicit learn)
  5. task history         — agent_work_history + agent_events
  6. artifact history     — artifacts / artifact_versions
  7. validation history   — ponytail_checks

Provenance chains (first-class):
  human → agent
  human → nuha → agent
  agent → agent

Soul/profile MUST NOT fabricate memories from arbitrary task text.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger("devos.agent_identity")

# Meaningful event types for durable agent timeline
EVENT_TASK_ASSIGNED = "task_assigned"
EVENT_TASK_ACCEPTED = "task_accepted"
EVENT_TOOL_EXECUTION = "tool_execution"
EVENT_FILE_CREATED = "file_created"
EVENT_FILE_MODIFIED = "file_modified"
EVENT_ARTIFACT_PRODUCED = "artifact_produced"
EVENT_PONYTAIL_VALIDATION = "ponytail_validation"
EVENT_CORRECTION_REQUESTED = "correction_requested"
EVENT_CORRECTION_COMPLETED = "correction_completed"
EVENT_TASK_COMPLETED = "task_completed"
EVENT_TASK_FAILED = "task_failed"


@dataclass
class Provenance:
    """Who directed the work — never inferred from LLM prose."""

    chain: str  # "human→agent" | "human→nuha→agent" | "agent→agent"
    requesting_actor_type: str  # human | nuha | agent
    requesting_actor_id: str
    delegated_by_type: Optional[str] = None
    delegated_by_id: Optional[str] = None
    executing_agent_id: str = ""

    def to_dict(self) -> dict:
        return {
            "chain": self.chain,
            "requesting_actor_type": self.requesting_actor_type,
            "requesting_actor_id": self.requesting_actor_id,
            "delegated_by_type": self.delegated_by_type,
            "delegated_by_id": self.delegated_by_id,
            "executing_agent_id": self.executing_agent_id,
        }

    @classmethod
    def human_to_agent(cls, human_id: str, agent_id: str) -> "Provenance":
        return cls(
            chain="human→agent",
            requesting_actor_type="human",
            requesting_actor_id=human_id,
            executing_agent_id=agent_id,
        )

    @classmethod
    def human_via_nuha(cls, human_id: str, agent_id: str) -> "Provenance":
        return cls(
            chain="human→nuha→agent",
            requesting_actor_type="human",
            requesting_actor_id=human_id,
            delegated_by_type="nuha",
            delegated_by_id="nuha",
            executing_agent_id=agent_id,
        )

    @classmethod
    def agent_to_agent(cls, from_agent_id: str, to_agent_id: str) -> "Provenance":
        return cls(
            chain="agent→agent",
            requesting_actor_type="agent",
            requesting_actor_id=from_agent_id,
            delegated_by_type="agent",
            delegated_by_id=from_agent_id,
            executing_agent_id=to_agent_id,
        )


async def ensure_agent_identity(
    *,
    persona_key: str,
    user_id: str,
    tenant_id: Optional[str] = None,
) -> dict:
    """
    Ensure durable agent + per-user profile + soul shells exist.
    Identity is immutable; profile/soul are created empty — no fabricated content.
    """
    from core.repositories.agency import ensure_agent
    from core.database import (
        AsyncSessionLocal,
        gen_id,
        AgentProfileRecord,
        AgentSoul,
        AgentPersonaRecord,
        utcnow_naive,
    )
    from sqlalchemy import select
    from brain.executable_agents import build_registry

    reg = build_registry()
    contract = reg.get(persona_key)
    name = persona_key
    caps: list = []
    tools: list = []
    role = "specialist"
    if contract:
        name = contract.persona_id
        caps = list(contract.capabilities)
        tools = list(contract.runtime_tools)
        role = contract.role

    agent = await ensure_agent(
        slug=f"persona-{persona_key}",
        name=f"{persona_key} agent",
        kind="persona",
        capabilities=caps,
        tools=tools,
    )
    agent_id = agent["id"]

    async with AsyncSessionLocal() as db:
        # persona link
        r = await db.execute(
            select(AgentPersonaRecord).where(AgentPersonaRecord.persona_key == persona_key)
        )
        if r.scalar_one_or_none() is None:
            db.add(
                AgentPersonaRecord(
                    id=gen_id(),
                    agent_id=agent_id,
                    persona_key=persona_key,
                    role=role,
                    can_delegate=(role == "orchestrator"),
                    agent_slug=contract.agent_slug if contract else None,
                    enabled=True,
                )
            )

        # profile (stats only — no free-text memory)
        r = await db.execute(
            select(AgentProfileRecord).where(
                AgentProfileRecord.agent_id == agent_id,
                AgentProfileRecord.user_id == user_id,
            )
        )
        profile = r.scalar_one_or_none()
        if profile is None:
            profile = AgentProfileRecord(
                id=gen_id(),
                agent_id=agent_id,
                user_id=user_id,
                tenant_id=tenant_id,
                display_name=persona_key,
                stats={
                    "capabilities": caps,
                    "runtime_tools": tools,
                    "tasks_completed": 0,
                    "tasks_failed": 0,
                    "ponytail_passed": 0,
                    "ponytail_failed": 0,
                },
            )
            db.add(profile)

        # soul shell (empty principles/preferences/lessons — never auto-filled from task text)
        r = await db.execute(
            select(AgentSoul).where(
                AgentSoul.agent_id == agent_id,
                AgentSoul.user_id == user_id,
            )
        )
        soul = r.scalar_one_or_none()
        if soul is None:
            db.add(
                AgentSoul(
                    id=gen_id(),
                    agent_id=agent_id,
                    user_id=user_id,
                    principles=[],
                    preferences={},
                    lessons=[],
                    memory_refs=[],
                    version=1,
                )
            )
        await db.commit()

    return {
        "agent_id": agent_id,
        "persona_key": persona_key,
        "role": role,
        "capabilities": caps,
        "runtime_tools": tools,
    }


async def record_agent_event(
    *,
    event_type: str,
    agent_id: str,
    user_id: str,
    provenance: Provenance,
    mission_id: Optional[str] = None,
    task_id: Optional[str] = None,
    payload: Optional[dict] = None,
) -> str:
    """Append structured timeline event. No free-form identity mutation."""
    from core.database import AsyncSessionLocal, gen_id, AgentEvent

    eid = gen_id()
    body = {
        "event_type": event_type,
        "user_id": user_id,
        "provenance": provenance.to_dict(),
        **(payload or {}),
    }
    try:
        async with AsyncSessionLocal() as db:
            db.add(
                AgentEvent(
                    id=eid,
                    agent_id=agent_id,
                    mission_id=mission_id,
                    task_id=task_id,
                    event_type=event_type,
                    payload=body,
                    actor_type=provenance.requesting_actor_type,
                    actor_id=provenance.requesting_actor_id,
                )
            )
            await db.commit()
    except Exception as e:
        logger.warning("agent_event persist failed: %s", e)
    return eid


async def record_task_lifecycle(
    *,
    event_type: str,
    agent_id: str,
    user_id: str,
    provenance: Provenance,
    mission_id: Optional[str] = None,
    task_id: Optional[str] = None,
    tools_used: Optional[list] = None,
    files_changed: Optional[list] = None,
    outcome: str = "unknown",
    summary: Optional[str] = None,
    evidence_id: Optional[str] = None,
    ponytail_check_id: Optional[str] = None,
    payload: Optional[dict] = None,
) -> dict:
    """
    Record work_history + event + optional profile stat bump.
    Summary is operational (what happened), never written into soul.lessons.
    """
    from core.repositories.agency import record_work_history

    wid = await record_work_history(
        agent_id=agent_id,
        user_id=user_id,
        actor_type=provenance.executing_agent_id and "agent" or provenance.requesting_actor_type,
        actor_id=provenance.executing_agent_id or provenance.requesting_actor_id,
        action=event_type,
        outcome=outcome,
        delegated_by_type=provenance.delegated_by_type,
        delegated_by_id=provenance.delegated_by_id,
        mission_id=mission_id,
        task_id=task_id,
        tools_used=tools_used,
        files_changed=files_changed,
        evidence_id=evidence_id,
        ponytail_check_id=ponytail_check_id,
        summary=(summary or "")[:500] or None,
        meta={"provenance": provenance.to_dict(), **(payload or {})},
    )
    eid = await record_agent_event(
        event_type=event_type,
        agent_id=agent_id,
        user_id=user_id,
        provenance=provenance,
        mission_id=mission_id,
        task_id=task_id,
        payload={
            "work_history_id": wid,
            "outcome": outcome,
            "tools_used": tools_used or [],
            "files_changed": files_changed or [],
            "summary": (summary or "")[:300],
        },
    )
    # Stats only (not soul)
    if event_type in (EVENT_TASK_COMPLETED, EVENT_TASK_FAILED, EVENT_PONYTAIL_VALIDATION):
        await _bump_profile_stats(
            agent_id=agent_id,
            user_id=user_id,
            event_type=event_type,
            outcome=outcome,
        )
    return {"work_history_id": wid, "event_id": eid}


async def _bump_profile_stats(
    *,
    agent_id: str,
    user_id: str,
    event_type: str,
    outcome: str,
) -> None:
    from core.database import AsyncSessionLocal, AgentProfileRecord, utcnow_naive
    from sqlalchemy import select

    try:
        async with AsyncSessionLocal() as db:
            r = await db.execute(
                select(AgentProfileRecord).where(
                    AgentProfileRecord.agent_id == agent_id,
                    AgentProfileRecord.user_id == user_id,
                )
            )
            profile = r.scalar_one_or_none()
            if not profile:
                return
            stats = dict(profile.stats or {})
            if event_type == EVENT_TASK_COMPLETED and outcome == "success":
                stats["tasks_completed"] = int(stats.get("tasks_completed") or 0) + 1
                profile.xp = int(profile.xp or 0) + 10
            if event_type == EVENT_TASK_FAILED:
                stats["tasks_failed"] = int(stats.get("tasks_failed") or 0) + 1
            if event_type == EVENT_PONYTAIL_VALIDATION:
                if outcome in ("success", "passed"):
                    stats["ponytail_passed"] = int(stats.get("ponytail_passed") or 0) + 1
                else:
                    stats["ponytail_failed"] = int(stats.get("ponytail_failed") or 0) + 1
            profile.stats = stats
            profile.updated_at = utcnow_naive()
            await db.commit()
    except Exception as e:
        logger.debug("profile stats skip: %s", e)


async def append_validated_lesson(
    *,
    agent_id: str,
    user_id: str,
    lesson: str,
    source_event: str,
    mission_id: Optional[str] = None,
) -> bool:
    """
    ONLY path that mutates soul.lessons.
    Requires an explicit validated source (e.g. ponytail pass / user-accepted).
    Rejects empty or oversized free-form dumps.
    """
    text = (lesson or "").strip()
    if len(text) < 12 or len(text) > 400:
        return False
    if source_event not in (
        EVENT_PONYTAIL_VALIDATION,
        EVENT_TASK_COMPLETED,
        EVENT_CORRECTION_COMPLETED,
    ):
        return False
    from core.database import AsyncSessionLocal, AgentSoul, utcnow_naive
    from sqlalchemy import select

    try:
        async with AsyncSessionLocal() as db:
            r = await db.execute(
                select(AgentSoul).where(
                    AgentSoul.agent_id == agent_id,
                    AgentSoul.user_id == user_id,
                )
            )
            soul = r.scalar_one_or_none()
            if not soul:
                return False
            lessons = list(soul.lessons or [])
            entry = {
                "text": text,
                "source_event": source_event,
                "mission_id": mission_id,
            }
            # de-dupe
            if any(l.get("text") == text for l in lessons if isinstance(l, dict)):
                return True
            lessons.append(entry)
            soul.lessons = lessons[-50:]  # bound
            soul.version = int(soul.version or 1) + 1
            soul.updated_at = utcnow_naive()
            await db.commit()
            return True
    except Exception as e:
        logger.warning("lesson append failed: %s", e)
        return False


async def get_agent_dossier(*, agent_id: str, user_id: str, history_limit: int = 30) -> dict:
    """Structured view for Nuha: identity, profile stats, soul, recent work — no fabrication."""
    from core.database import (
        AsyncSessionLocal,
        Agent,
        AgentProfileRecord,
        AgentSoul,
        AgentWorkHistory,
        AgentEvent,
        PonytailCheck,
    )
    from sqlalchemy import select, desc

    async with AsyncSessionLocal() as db:
        agent = await db.get(Agent, agent_id)
        profile = (
            await db.execute(
                select(AgentProfileRecord).where(
                    AgentProfileRecord.agent_id == agent_id,
                    AgentProfileRecord.user_id == user_id,
                )
            )
        ).scalar_one_or_none()
        soul = (
            await db.execute(
                select(AgentSoul).where(
                    AgentSoul.agent_id == agent_id,
                    AgentSoul.user_id == user_id,
                )
            )
        ).scalar_one_or_none()
        history = (
            await db.execute(
                select(AgentWorkHistory)
                .where(
                    AgentWorkHistory.agent_id == agent_id,
                    AgentWorkHistory.user_id == user_id,
                )
                .order_by(desc(AgentWorkHistory.created_at))
                .limit(history_limit)
            )
        ).scalars().all()
        events = (
            await db.execute(
                select(AgentEvent)
                .where(AgentEvent.agent_id == agent_id)
                .order_by(desc(AgentEvent.created_at))
                .limit(history_limit)
            )
        ).scalars().all()

        return {
            "identity": {
                "id": agent.id if agent else agent_id,
                "slug": agent.slug if agent else None,
                "name": agent.name if agent else None,
                "kind": agent.kind if agent else None,
                "immutable": True,
            },
            "profile": {
                "xp": profile.xp if profile else 0,
                "level": profile.level if profile else 1,
                "stats": (profile.stats if profile else {}) or {},
                "display_name": profile.display_name if profile else None,
            },
            "soul": {
                "principles": (soul.principles if soul else []) or [],
                "preferences": (soul.preferences if soul else {}) or {},
                "lessons": (soul.lessons if soul else []) or [],
                "version": soul.version if soul else 0,
                "note": "Lessons only from validated outcomes — never auto-copied from task text",
            },
            "work_history": [
                {
                    "id": h.id,
                    "action": h.action,
                    "outcome": h.outcome,
                    "mission_id": h.mission_id,
                    "task_id": h.task_id,
                    "actor_type": h.actor_type,
                    "delegated_by_type": h.delegated_by_type,
                    "delegated_by_id": h.delegated_by_id,
                    "tools_used": h.tools_used,
                    "files_changed": h.files_changed,
                    "summary": h.summary,
                    "provenance": (h.meta or {}).get("provenance"),
                    "created_at": h.created_at.isoformat() if h.created_at else None,
                }
                for h in history
            ],
            "events": [
                {
                    "id": e.id,
                    "event_type": e.event_type,
                    "mission_id": e.mission_id,
                    "task_id": e.task_id,
                    "payload": e.payload,
                    "created_at": e.created_at.isoformat() if e.created_at else None,
                }
                for e in events
            ],
        }
