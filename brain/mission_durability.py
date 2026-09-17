"""Mission durability helpers: outbox events, saga steps, reconciliation.

At-least-once outbox delivery + idempotent consumers. Not exactly-once.
"""
from __future__ import annotations

import hashlib
import logging
from typing import Any, Optional

logger = logging.getLogger("devos.mission_durability")

# Canonical durable mission phases (map onto Mission.status strings)
PHASE_PENDING = "pending"
PHASE_DELEGATED = "delegated"
PHASE_RUNNING = "running"
PHASE_VERIFYING = "verifying"
PHASE_ACCEPTING = "accepting"
PHASE_SUCCEEDED = "succeeded"
PHASE_FAILED = "failed"
PHASE_BLOCKED = "blocked"
PHASE_CANCELLED = "cancelled"

# Long-running coding mission lifecycle (see brain/mission_checkpoint.py)
PHASE_QUEUED = "queued"
PHASE_PLANNING = "planning"
PHASE_EXECUTING = "executing"
PHASE_WAITING = "waiting"
PHASE_RETRYING = "retrying"
PHASE_VALIDATING = "validating"
PHASE_COMPLETED = "completed"
PHASE_RECOVERY = "recovery"


def scoped_idempotency_key(*, user_id: str, client_key: str, scope: str = "mission") -> str:
    """Owner-scoped idempotency key. Never global-only."""
    raw = f"{scope}:{user_id}:{client_key.strip()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:48]


def emit_mission_event(
    event_type: str,
    *,
    mission_id: str,
    user_id: Optional[str] = None,
    task_id: Optional[str] = None,
    plan_id: Optional[str] = None,
    payload: Optional[dict] = None,
) -> Optional[str]:
    """Enqueue durable outbox event (idempotent per event_type+mission+task)."""
    try:
        from execution.outbox import enqueue

        body = dict(payload or {})
        body.setdefault("mission_id", mission_id)
        if task_id:
            body.setdefault("task_id", task_id)
        if plan_id:
            body.setdefault("plan_id", plan_id)
        ikey = f"mission:{mission_id}:{event_type}:{task_id or '-'}"
        return enqueue(
            event_type,
            body,
            aggregate_type="mission",
            aggregate_id=mission_id,
            user_id=user_id,
            trace_id=mission_id,
            idempotency_key=ikey,
        )
    except Exception as e:
        logger.warning("outbox emit failed: %s", type(e).__name__)
        return None


def begin_mission_saga(
    *,
    mission_id: str,
    plan_id: Optional[str] = None,
) -> Optional[Any]:
    try:
        from execution.saga import create_saga

        return create_saga(mission_id=mission_id, plan_id=plan_id)
    except Exception as e:
        logger.warning("saga create failed: %s", type(e).__name__)
        return None


def reconcile_mission_state(
    *,
    status: str,
    files_changed: Optional[list] = None,
    ponytail: Optional[dict] = None,
    evidence_refs: Optional[list] = None,
    execution_ok: bool = False,
) -> dict:
    """Pure recovery decision from durable facts (no process memory).

    Returns {action, target_status, reason}.
    """
    from brain.mission_acceptance import evaluate_mission_acceptance

    st = (status or "pending").lower()
    if st in ("succeeded", "completed", "accepted", "failed", "cancelled", "canceled", "denied", "blocked"):
        return {"action": "noop", "target_status": st, "reason": "already_terminal"}

    pt = ponytail or {}
    refs = list(evidence_refs or [])
    files = list(files_changed or [])

    if pt.get("passed") and refs and files:
        acc = evaluate_mission_acceptance(
            execution_ok=True,
            status="accepted",
            files_changed=files,
            ponytail=pt,
            evidence_refs=refs,
        )
        if acc["ok"]:
            return {"action": "complete", "target_status": "succeeded", "reason": "reconcile_accepted"}
        return {"action": "fail", "target_status": "failed", "reason": acc.get("reason")}

    if pt.get("passed") and not refs:
        return {"action": "resume_evidence", "target_status": "accepting", "reason": "evidence_missing"}

    if files and not pt:
        return {"action": "resume_ponytail", "target_status": "verifying", "reason": "ponytail_pending"}

    if execution_ok and not files:
        return {"action": "resume_execution", "target_status": "running", "reason": "no_artifact_yet"}

    if st in ("delegated", "running", "pending", "plan_ready",
              "queued", "planning", "executing", "waiting", "retrying", "recovery"):
        return {"action": "resume_execution", "target_status": st or "executing", "reason": "interrupted"}

    if st == "validating":
        return {"action": "resume_ponytail", "target_status": "validating", "reason": "validation_interrupted"}

    return {"action": "noop", "target_status": st, "reason": "unknown"}


def a2a_message_idempotency_id(
    *,
    mission_id: str,
    task_id: str,
    message_type: str,
    round_i: int = 0,
) -> str:
    """Stable A2A message id for retries of the same logical delivery."""
    raw = f"{mission_id}:{task_id}:{message_type}:{round_i}"
    return "a2a_" + hashlib.sha256(raw.encode()).hexdigest()[:32]
