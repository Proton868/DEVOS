"""Agency OS repositories — Postgres/Supabase is authoritative.

Filesystem holds artifact bytes only; metadata and orchestration state live here.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from sqlalchemy import select

from core.database import (
    AsyncSessionLocal,
    gen_id,
    Agent,
    AgentWorkHistory,
    Mission,
    MissionTask,
    TaskDelegation,
    Artifact,
    ArtifactVersion,
    PonytailCheck,
    utcnow_naive,
)

logger = logging.getLogger("devos.repo.agency")


async def ensure_agent(*, slug: str, name: str, kind: str = "persona", **meta) -> dict:
    async with AsyncSessionLocal() as db:
        r = await db.execute(select(Agent).where(Agent.slug == slug))
        row = r.scalar_one_or_none()
        if row is None:
            row = Agent(id=gen_id(), slug=slug, name=name, kind=kind, meta=meta or {})
            db.add(row)
            await db.commit()
            await db.refresh(row)
        return {"id": row.id, "slug": row.slug, "name": row.name}


async def record_work_history(
    *,
    agent_id: str,
    user_id: str,
    actor_type: str,
    actor_id: str,
    action: str,
    outcome: str = "unknown",
    tenant_id: Optional[str] = None,
    delegated_by_type: Optional[str] = None,
    delegated_by_id: Optional[str] = None,
    mission_id: Optional[str] = None,
    task_id: Optional[str] = None,
    tools_used: Optional[list] = None,
    files_changed: Optional[list] = None,
    evidence_id: Optional[str] = None,
    ponytail_check_id: Optional[str] = None,
    summary: Optional[str] = None,
    meta: Optional[dict] = None,
) -> str:
    wid = gen_id()
    async with AsyncSessionLocal() as db:
        row = AgentWorkHistory(
            id=wid,
            agent_id=agent_id,
            user_id=user_id,
            tenant_id=tenant_id,
            actor_type=actor_type,
            actor_id=actor_id,
            delegated_by_type=delegated_by_type,
            delegated_by_id=delegated_by_id,
            mission_id=mission_id,
            task_id=task_id,
            action=action,
            tools_used=list(tools_used or []),
            files_changed=list(files_changed or []),
            outcome=outcome,
            evidence_id=evidence_id,
            ponytail_check_id=ponytail_check_id,
            summary=summary,
            meta=meta or {},
        )
        db.add(row)
        await db.commit()
    return wid


async def list_work_history(*, user_id: str, agent_id: Optional[str] = None, limit: int = 50) -> list[dict]:
    async with AsyncSessionLocal() as db:
        q = select(AgentWorkHistory).where(AgentWorkHistory.user_id == user_id)
        if agent_id:
            q = q.where(AgentWorkHistory.agent_id == agent_id)
        q = q.order_by(AgentWorkHistory.created_at.desc()).limit(limit)
        rows = (await db.execute(q)).scalars().all()
        return [
            {
                "id": r.id,
                "agent_id": r.agent_id,
                "actor_type": r.actor_type,
                "actor_id": r.actor_id,
                "delegated_by_type": r.delegated_by_type,
                "delegated_by_id": r.delegated_by_id,
                "mission_id": r.mission_id,
                "task_id": r.task_id,
                "action": r.action,
                "outcome": r.outcome,
                "tools_used": r.tools_used,
                "files_changed": r.files_changed,
                "evidence_id": r.evidence_id,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ]


async def create_mission(
    *,
    user_id: str,
    goal: str,
    workspace_id: str = "default",
    tenant_id: Optional[str] = None,
    plan_id: Optional[str] = None,
    actor_type: str = "nuha",
    actor_id: str = "nuha",
    meta: Optional[dict] = None,
) -> dict:
    mid = gen_id()
    async with AsyncSessionLocal() as db:
        row = Mission(
            id=mid,
            user_id=user_id,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            goal=goal,
            status="pending",
            plan_id=plan_id,
            actor_type=actor_type,
            actor_id=actor_id,
            meta=meta or {},
        )
        db.add(row)
        await db.commit()
    return {"id": mid, "status": "pending", "goal": goal}


async def add_mission_task(
    *,
    mission_id: str,
    description: str,
    persona_key: Optional[str] = None,
    agent_id: Optional[str] = None,
    sequence_no: int = 0,
) -> dict:
    tid = gen_id()
    async with AsyncSessionLocal() as db:
        row = MissionTask(
            id=tid,
            mission_id=mission_id,
            description=description,
            persona_key=persona_key,
            agent_id=agent_id,
            sequence_no=sequence_no,
            status="pending",
        )
        db.add(row)
        await db.commit()
    return {"id": tid, "mission_id": mission_id, "status": "pending"}


async def record_delegation(
    *,
    mission_id: str,
    task_id: str,
    from_actor_type: str,
    from_actor_id: str,
    to_actor_type: str,
    to_actor_id: str,
    reason: Optional[str] = None,
) -> str:
    did = gen_id()
    async with AsyncSessionLocal() as db:
        row = TaskDelegation(
            id=did,
            mission_id=mission_id,
            task_id=task_id,
            from_actor_type=from_actor_type,
            from_actor_id=from_actor_id,
            to_actor_type=to_actor_type,
            to_actor_id=to_actor_id,
            reason=reason,
            status="active",
        )
        db.add(row)
        await db.commit()
    return did


async def get_mission(mission_id: str) -> Optional[dict]:
    async with AsyncSessionLocal() as db:
        row = await db.get(Mission, mission_id)
        if not row:
            return None
        tasks = (
            await db.execute(select(MissionTask).where(MissionTask.mission_id == mission_id))
        ).scalars().all()
        return {
            "id": row.id,
            "user_id": row.user_id,
            "goal": row.goal,
            "status": row.status,
            "plan_id": row.plan_id,
            "actor_type": row.actor_type,
            "actor_id": row.actor_id,
            "tasks": [
                {
                    "id": t.id,
                    "persona_key": t.persona_key,
                    "agent_id": t.agent_id,
                    "status": t.status,
                    "description": t.description,
                }
                for t in tasks
            ],
        }


async def upsert_artifact_metadata(
    *,
    user_id: str,
    project_id: str,
    path: str,
    content_hash: Optional[str] = None,
    size_bytes: Optional[int] = None,
    agent_id: Optional[str] = None,
    mission_id: Optional[str] = None,
    task_id: Optional[str] = None,
    actor_type: Optional[str] = None,
    actor_id: Optional[str] = None,
) -> str:
    async with AsyncSessionLocal() as db:
        r = await db.execute(
            select(Artifact).where(
                Artifact.user_id == user_id,
                Artifact.project_id == project_id,
                Artifact.path == path,
            )
        )
        row = r.scalar_one_or_none()
        if row is None:
            aid = gen_id()
            row = Artifact(
                id=aid,
                user_id=user_id,
                project_id=project_id,
                path=path,
                content_hash=content_hash,
                size_bytes=size_bytes,
                agent_id=agent_id,
                mission_id=mission_id,
                task_id=task_id,
            )
            db.add(row)
            ver = 1
        else:
            aid = row.id
            row.content_hash = content_hash or row.content_hash
            row.size_bytes = size_bytes if size_bytes is not None else row.size_bytes
            row.updated_at = utcnow_naive()
            # next version
            from sqlalchemy import func
            mx = await db.execute(
                select(func.max(ArtifactVersion.version)).where(ArtifactVersion.artifact_id == aid)
            )
            ver = (mx.scalar() or 0) + 1
        db.add(
            ArtifactVersion(
                id=gen_id(),
                artifact_id=aid if row.id else row.id,
                version=ver,
                content_hash=content_hash,
                size_bytes=size_bytes,
                actor_type=actor_type,
                actor_id=actor_id,
                mission_id=mission_id,
                task_id=task_id,
            )
        )
        await db.commit()
        return row.id


async def record_ponytail_check(
    *,
    user_id: str,
    status: str,
    agent_id: Optional[str] = None,
    mission_id: Optional[str] = None,
    task_id: Optional[str] = None,
    stage_results: Optional[list] = None,
    self_check: Optional[str] = None,
    summary: Optional[str] = None,
    evidence_id: Optional[str] = None,
) -> str:
    pid = gen_id()
    async with AsyncSessionLocal() as db:
        row = PonytailCheck(
            id=pid,
            user_id=user_id,
            agent_id=agent_id,
            mission_id=mission_id,
            task_id=task_id,
            status=status,
            stage_results=list(stage_results or []),
            self_check=self_check,
            summary=summary,
            evidence_id=evidence_id,
            finished_at=utcnow_naive() if status in ("passed", "failed", "skipped") else None,
        )
        db.add(row)
        await db.commit()
    return pid
