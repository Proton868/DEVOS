"""Durable orchestration plan persistence — extends existing DB, not a second store."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger("devos.orchestration_store")

NON_TERMINAL = {
    "planning", "context_gathering", "goal_analysis", "task_decomposition",
    "persona_selection", "capability_analysis", "dependency_analysis",
    "risk_analysis", "verification_design", "plan_ready", "action_requested",
    "authorization_pending", "awaiting_approval", "approved", "authorized",
    "delegating", "delegated", "queued", "running", "verifying",
    "recovering", "replanning", "cancellation_requested", "cancelling",
}


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def persist_plan(plan) -> bool:
    """Upsert plan snapshot. Best-effort; never raises into orchestration loop."""
    try:
        from core.database import AsyncSessionLocal, OrchestrationPlanRecord
        payload = plan.to_dict() if hasattr(plan, "to_dict") else dict(plan)
        async with AsyncSessionLocal() as db:
            row = await db.get(OrchestrationPlanRecord, plan.id)
            if row is None:
                row = OrchestrationPlanRecord(
                    id=plan.id,
                    user_id=plan.user_id,
                    workspace_id=getattr(plan, "workspace_id", None) or "default",
                    goal=plan.goal or "",
                    mode=getattr(plan, "mode", "plan") or "plan",
                    status=plan.status,
                    plan_json=payload,
                    created_at=_now(),
                    updated_at=_now(),
                )
                db.add(row)
            else:
                row.status = plan.status
                row.goal = plan.goal or row.goal
                row.mode = getattr(plan, "mode", row.mode) or row.mode
                row.workspace_id = getattr(plan, "workspace_id", None) or row.workspace_id
                row.plan_json = payload
                row.updated_at = _now()
            await db.commit()
        return True
    except Exception as e:
        logger.warning("persist_plan failed: %s", e)
        return False


async def load_plan(plan_id: str) -> Optional[dict]:
    try:
        from core.database import AsyncSessionLocal, OrchestrationPlanRecord
        async with AsyncSessionLocal() as db:
            row = await db.get(OrchestrationPlanRecord, plan_id)
            if not row:
                return None
            data = dict(row.plan_json or {})
            data["id"] = row.id
            data["status"] = row.status
            data["user_id"] = row.user_id
            data["workspace_id"] = row.workspace_id
            data["goal"] = row.goal
            data["mode"] = row.mode
            return data
    except Exception as e:
        logger.warning("load_plan failed: %s", e)
        return None


async def list_user_plans(user_id: str, limit: int = 30) -> list[dict]:
    try:
        from sqlalchemy import select
        from core.database import AsyncSessionLocal, OrchestrationPlanRecord
        async with AsyncSessionLocal() as db:
            r = await db.execute(
                select(OrchestrationPlanRecord)
                .where(OrchestrationPlanRecord.user_id == user_id)
                .order_by(OrchestrationPlanRecord.updated_at.desc())
                .limit(limit)
            )
            out = []
            for row in r.scalars().all():
                d = dict(row.plan_json or {})
                d["id"] = row.id
                d["status"] = row.status
                d["goal"] = row.goal
                out.append(d)
            return out
    except Exception as e:
        logger.warning("list_user_plans failed: %s", e)
        return []


async def list_recoverable_plans(limit: int = 50) -> list[dict]:
    try:
        from sqlalchemy import select
        from core.database import AsyncSessionLocal, OrchestrationPlanRecord
        async with AsyncSessionLocal() as db:
            r = await db.execute(
                select(OrchestrationPlanRecord)
                .where(OrchestrationPlanRecord.status.in_(list(NON_TERMINAL)))
                .order_by(OrchestrationPlanRecord.updated_at.desc())
                .limit(limit)
            )
            return [
                {
                    "id": row.id,
                    "user_id": row.user_id,
                    "status": row.status,
                    "goal": row.goal,
                    "plan_json": row.plan_json,
                }
                for row in r.scalars().all()
            ]
    except Exception as e:
        logger.warning("list_recoverable failed: %s", e)
        return []
