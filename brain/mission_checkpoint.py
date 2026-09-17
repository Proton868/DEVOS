"""
Durable checkpoints for long-running coding missions.

Survives provider failures and process interruption by persisting:
  - lifecycle status (truthful)
  - completed / pending steps
  - retry counts
  - provider / model info
  - artifact + evidence references
  - idempotency keys for destructive ops

Resume from the last durable checkpoint — never blindly restart.

States:
  queued | planning | executing | waiting | retrying | validating
  | completed | failed | cancelled | recovery
"""
from __future__ import annotations

import hashlib
import logging
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

logger = logging.getLogger("devos.mission_checkpoint")


class MissionLifecycle(str, Enum):
    QUEUED = "queued"
    PLANNING = "planning"
    EXECUTING = "executing"
    WAITING = "waiting"
    RETRYING = "retrying"
    VALIDATING = "validating"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    RECOVERY = "recovery"


TERMINAL = frozenset({
    MissionLifecycle.COMPLETED,
    MissionLifecycle.FAILED,
    MissionLifecycle.CANCELLED,
})

# Allowed transitions (fail-closed: unknown transitions rejected)
_TRANSITIONS: dict[MissionLifecycle, frozenset[MissionLifecycle]] = {
    MissionLifecycle.QUEUED: frozenset({
        MissionLifecycle.PLANNING, MissionLifecycle.CANCELLED, MissionLifecycle.RECOVERY,
    }),
    MissionLifecycle.PLANNING: frozenset({
        MissionLifecycle.EXECUTING, MissionLifecycle.WAITING, MissionLifecycle.FAILED,
        MissionLifecycle.CANCELLED, MissionLifecycle.RECOVERY,
    }),
    MissionLifecycle.EXECUTING: frozenset({
        MissionLifecycle.WAITING, MissionLifecycle.RETRYING, MissionLifecycle.VALIDATING,
        MissionLifecycle.FAILED, MissionLifecycle.CANCELLED, MissionLifecycle.RECOVERY,
        MissionLifecycle.COMPLETED,  # only after validate path sets truth
    }),
    MissionLifecycle.WAITING: frozenset({
        MissionLifecycle.EXECUTING, MissionLifecycle.RETRYING, MissionLifecycle.CANCELLED,
        MissionLifecycle.RECOVERY, MissionLifecycle.FAILED,
    }),
    MissionLifecycle.RETRYING: frozenset({
        MissionLifecycle.EXECUTING, MissionLifecycle.FAILED, MissionLifecycle.CANCELLED,
        MissionLifecycle.WAITING, MissionLifecycle.RECOVERY,
    }),
    MissionLifecycle.VALIDATING: frozenset({
        MissionLifecycle.COMPLETED, MissionLifecycle.FAILED, MissionLifecycle.EXECUTING,
        MissionLifecycle.CANCELLED, MissionLifecycle.RECOVERY,
    }),
    MissionLifecycle.RECOVERY: frozenset({
        MissionLifecycle.EXECUTING, MissionLifecycle.VALIDATING, MissionLifecycle.RETRYING,
        MissionLifecycle.WAITING, MissionLifecycle.FAILED, MissionLifecycle.CANCELLED,
        MissionLifecycle.COMPLETED,
    }),
    MissionLifecycle.COMPLETED: frozenset(),
    MissionLifecycle.FAILED: frozenset({MissionLifecycle.RECOVERY}),  # explicit re-open only
    MissionLifecycle.CANCELLED: frozenset(),
}


def _as_lifecycle(val: MissionLifecycle | str) -> MissionLifecycle:
    if isinstance(val, MissionLifecycle):
        return val
    return MissionLifecycle(str(val))


def can_transition(frm: MissionLifecycle | str, to: MissionLifecycle | str) -> bool:
    a = _as_lifecycle(frm)
    b = _as_lifecycle(to)
    if a == b:
        return True
    return b in _TRANSITIONS.get(a, frozenset())


@dataclass
class MissionStepRecord:
    step_id: str
    name: str
    status: str = "pending"  # pending | running | completed | failed | skipped
    attempt: int = 0
    max_attempts: int = 3
    provider: Optional[str] = None
    model: Optional[str] = None
    idempotency_key: Optional[str] = None
    result_summary: Optional[str] = None
    error: Optional[str] = None
    destructive: bool = False
    completed_at: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "MissionStepRecord":
        return cls(
            step_id=str(d.get("step_id") or ""),
            name=str(d.get("name") or ""),
            status=str(d.get("status") or "pending"),
            attempt=int(d.get("attempt") or 0),
            max_attempts=int(d.get("max_attempts") or 3),
            provider=d.get("provider"),
            model=d.get("model"),
            idempotency_key=d.get("idempotency_key"),
            result_summary=d.get("result_summary"),
            error=d.get("error"),
            destructive=bool(d.get("destructive")),
            completed_at=d.get("completed_at"),
        )


@dataclass
class MissionCheckpoint:
    """Durable execution snapshot — source of truth for resume."""

    mission_id: str
    status: MissionLifecycle = MissionLifecycle.QUEUED
    plan_id: Optional[str] = None
    user_id: Optional[str] = None
    tenant_id: Optional[str] = None
    goal: str = ""
    steps: list[MissionStepRecord] = field(default_factory=list)
    current_step_id: Optional[str] = None
    global_retry_count: int = 0
    max_global_retries: int = 8
    last_provider: Optional[str] = None
    last_model: Optional[str] = None
    provider_errors: list[dict] = field(default_factory=list)  # sanitized
    artifact_refs: list[str] = field(default_factory=list)
    evidence_refs: list[str] = field(default_factory=list)
    destructive_ops_done: list[str] = field(default_factory=list)  # idempotency keys
    version: int = 1
    updated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    recovery_note: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "mission_id": self.mission_id,
            "status": self.status.value if isinstance(self.status, MissionLifecycle) else str(self.status),
            "plan_id": self.plan_id,
            "user_id": self.user_id,
            "tenant_id": self.tenant_id,
            "goal": self.goal,
            "steps": [s.to_dict() for s in self.steps],
            "current_step_id": self.current_step_id,
            "global_retry_count": self.global_retry_count,
            "max_global_retries": self.max_global_retries,
            "last_provider": self.last_provider,
            "last_model": self.last_model,
            "provider_errors": list(self.provider_errors or []),
            "artifact_refs": list(self.artifact_refs or []),
            "evidence_refs": list(self.evidence_refs or []),
            "destructive_ops_done": list(self.destructive_ops_done or []),
            "version": self.version,
            "updated_at": self.updated_at,
            "recovery_note": self.recovery_note,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "MissionCheckpoint":
        st = d.get("status") or MissionLifecycle.QUEUED.value
        try:
            status = MissionLifecycle(str(st))
        except Exception:
            status = MissionLifecycle.QUEUED
        steps = [MissionStepRecord.from_dict(x) for x in (d.get("steps") or []) if isinstance(x, dict)]
        return cls(
            mission_id=str(d.get("mission_id") or ""),
            status=status,
            plan_id=d.get("plan_id"),
            user_id=d.get("user_id"),
            tenant_id=d.get("tenant_id"),
            goal=str(d.get("goal") or ""),
            steps=steps,
            current_step_id=d.get("current_step_id"),
            global_retry_count=int(d.get("global_retry_count") or 0),
            max_global_retries=int(d.get("max_global_retries") or 8),
            last_provider=d.get("last_provider"),
            last_model=d.get("last_model"),
            provider_errors=list(d.get("provider_errors") or []),
            artifact_refs=list(d.get("artifact_refs") or []),
            evidence_refs=list(d.get("evidence_refs") or []),
            destructive_ops_done=list(d.get("destructive_ops_done") or []),
            version=int(d.get("version") or 1),
            updated_at=str(d.get("updated_at") or datetime.now(timezone.utc).isoformat()),
            recovery_note=d.get("recovery_note"),
        )

    def completed_steps(self) -> list[MissionStepRecord]:
        return [s for s in self.steps if s.status == "completed"]

    def pending_steps(self) -> list[MissionStepRecord]:
        return [s for s in self.steps if s.status in ("pending", "running")]


# In-process cache; durable copy lives on Mission.meta["coding_checkpoint"]
_CHECKPOINTS: dict[str, MissionCheckpoint] = {}


def step_idempotency_key(*, mission_id: str, step_id: str, op: str) -> str:
    raw = f"{mission_id}:{step_id}:{op}"
    return "mstep_" + hashlib.sha256(raw.encode()).hexdigest()[:32]


def create_checkpoint(
    *,
    mission_id: str,
    goal: str = "",
    user_id: Optional[str] = None,
    tenant_id: Optional[str] = None,
    plan_id: Optional[str] = None,
    steps: Optional[list[dict]] = None,
) -> MissionCheckpoint:
    step_recs: list[MissionStepRecord] = []
    for i, s in enumerate(steps or []):
        if isinstance(s, MissionStepRecord):
            step_recs.append(s)
            continue
        sid = str(s.get("step_id") or f"step_{i+1}")
        destructive = bool(s.get("destructive"))
        ikey = s.get("idempotency_key") or (
            step_idempotency_key(mission_id=mission_id, step_id=sid, op=str(s.get("name") or sid))
            if destructive else None
        )
        step_recs.append(MissionStepRecord(
            step_id=sid,
            name=str(s.get("name") or sid),
            status=str(s.get("status") or "pending"),
            max_attempts=int(s.get("max_attempts") or 3),
            destructive=destructive,
            idempotency_key=ikey,
        ))
    cp = MissionCheckpoint(
        mission_id=mission_id,
        status=MissionLifecycle.QUEUED,
        goal=goal,
        user_id=user_id,
        tenant_id=tenant_id,
        plan_id=plan_id,
        steps=step_recs,
        current_step_id=step_recs[0].step_id if step_recs else None,
    )
    _CHECKPOINTS[mission_id] = cp
    return cp


def get_checkpoint(mission_id: str) -> Optional[MissionCheckpoint]:
    return _CHECKPOINTS.get(mission_id)


def transition_status(cp: MissionCheckpoint, new_status: MissionLifecycle | str) -> MissionCheckpoint:
    target = _as_lifecycle(new_status)
    if not can_transition(cp.status, target):
        raise ValueError(f"illegal transition {cp.status.value} → {target.value}")
    cp.status = target
    cp.updated_at = datetime.now(timezone.utc).isoformat()
    cp.version += 1
    _CHECKPOINTS[cp.mission_id] = cp
    return cp


def record_provider_failure(
    cp: MissionCheckpoint,
    *,
    provider: str,
    model: Optional[str] = None,
    status_code: Optional[int] = None,
    error: str = "",
    category: str = "",
) -> MissionCheckpoint:
    """Record sanitized provider failure; mark retrying for 429/5xx."""
    code = status_code
    err_l = (error or "").lower()
    if code is None:
        if "429" in err_l:
            code = 429
        elif any(x in err_l for x in ("500", "502", "503", "504")):
            code = 503
    retryable = code in (429, 500, 502, 503, 504) or category in ("rate_limited", "retryable")
    entry = {
        "provider": provider,
        "model": model,
        "status_code": code,
        "category": category or ("rate_limited" if code == 429 else "provider_error"),
        "retryable": retryable,
        "error": (error or "")[:300],
        "at": datetime.now(timezone.utc).isoformat(),
    }
    cp.provider_errors.append(entry)
    cp.last_provider = provider
    cp.last_model = model
    cp.global_retry_count += 1
    if cp.status in TERMINAL:
        return cp
    if retryable and cp.global_retry_count <= cp.max_global_retries:
        if can_transition(cp.status, MissionLifecycle.RETRYING):
            transition_status(cp, MissionLifecycle.RETRYING)
    else:
        if can_transition(cp.status, MissionLifecycle.FAILED):
            transition_status(cp, MissionLifecycle.FAILED)
        elif cp.status != MissionLifecycle.FAILED:
            # force fail from recovery
            if can_transition(cp.status, MissionLifecycle.FAILED):
                transition_status(cp, MissionLifecycle.FAILED)
    cp.updated_at = datetime.now(timezone.utc).isoformat()
    _CHECKPOINTS[cp.mission_id] = cp
    return cp


def mark_step_running(cp: MissionCheckpoint, step_id: str, *, provider: str = None, model: str = None) -> MissionCheckpoint:
    for s in cp.steps:
        if s.step_id == step_id:
            if s.status == "completed":
                return cp  # do not re-run completed
            s.status = "running"
            s.attempt += 1
            s.provider = provider or s.provider
            s.model = model or s.model
            cp.current_step_id = step_id
            break
    if cp.status not in TERMINAL and can_transition(cp.status, MissionLifecycle.EXECUTING):
        try:
            transition_status(cp, MissionLifecycle.EXECUTING)
        except ValueError:
            pass
    cp.updated_at = datetime.now(timezone.utc).isoformat()
    _CHECKPOINTS[cp.mission_id] = cp
    return cp


def mark_step_completed(
    cp: MissionCheckpoint,
    step_id: str,
    *,
    summary: str = "",
    artifact_ref: Optional[str] = None,
    evidence_ref: Optional[str] = None,
) -> MissionCheckpoint:
    for s in cp.steps:
        if s.step_id == step_id:
            s.status = "completed"
            s.result_summary = (summary or "")[:500]
            s.error = None
            s.completed_at = datetime.now(timezone.utc).isoformat()
            if s.destructive and s.idempotency_key:
                if s.idempotency_key not in cp.destructive_ops_done:
                    cp.destructive_ops_done.append(s.idempotency_key)
            break
    if artifact_ref and artifact_ref not in cp.artifact_refs:
        cp.artifact_refs.append(artifact_ref)
    if evidence_ref and evidence_ref not in cp.evidence_refs:
        cp.evidence_refs.append(evidence_ref)
    # Advance current pointer
    pending = cp.pending_steps()
    cp.current_step_id = pending[0].step_id if pending else None
    cp.updated_at = datetime.now(timezone.utc).isoformat()
    cp.version += 1
    _CHECKPOINTS[cp.mission_id] = cp
    return cp


def mark_step_failed(cp: MissionCheckpoint, step_id: str, *, error: str = "") -> MissionCheckpoint:
    for s in cp.steps:
        if s.step_id == step_id:
            s.error = (error or "")[:500]
            if s.attempt >= s.max_attempts:
                s.status = "failed"
            else:
                s.status = "pending"  # eligible for retry
            break
    cp.updated_at = datetime.now(timezone.utc).isoformat()
    _CHECKPOINTS[cp.mission_id] = cp
    return cp


def should_skip_destructive(cp: MissionCheckpoint, idempotency_key: str) -> bool:
    """True if this destructive op already completed — prevents duplicate after recovery."""
    return bool(idempotency_key) and idempotency_key in (cp.destructive_ops_done or [])


def begin_recovery(cp: MissionCheckpoint, note: str = "process_interrupt") -> MissionCheckpoint:
    if cp.status in TERMINAL and cp.status != MissionLifecycle.FAILED:
        return cp
    if cp.status != MissionLifecycle.RECOVERY:
        if can_transition(cp.status, MissionLifecycle.RECOVERY):
            transition_status(cp, MissionLifecycle.RECOVERY)
        elif cp.status == MissionLifecycle.FAILED and can_transition(cp.status, MissionLifecycle.RECOVERY):
            transition_status(cp, MissionLifecycle.RECOVERY)
    cp.recovery_note = note
    cp.updated_at = datetime.now(timezone.utc).isoformat()
    _CHECKPOINTS[cp.mission_id] = cp
    return cp


def resume_plan(cp: MissionCheckpoint) -> dict:
    """
    Decide how to resume from durable checkpoint (no process memory required).

    Returns {action, status, next_step_id, skip_destructive_keys, reason}.
    """
    if cp.status == MissionLifecycle.COMPLETED:
        return {"action": "noop", "status": cp.status.value, "reason": "already_completed"}
    if cp.status == MissionLifecycle.CANCELLED:
        return {"action": "noop", "status": cp.status.value, "reason": "cancelled"}

    # Enter recovery if interrupted mid-flight
    if cp.status in (
        MissionLifecycle.EXECUTING,
        MissionLifecycle.WAITING,
        MissionLifecycle.RETRYING,
        MissionLifecycle.PLANNING,
        MissionLifecycle.VALIDATING,
    ):
        begin_recovery(cp, note="resume_from_checkpoint")

    pending = cp.pending_steps()
    failed = [s for s in cp.steps if s.status == "failed"]

    if not pending and not failed and cp.completed_steps():
        # All steps done — need validation before completed
        if cp.status != MissionLifecycle.COMPLETED:
            if can_transition(cp.status, MissionLifecycle.VALIDATING):
                transition_status(cp, MissionLifecycle.VALIDATING)
            return {
                "action": "validate",
                "status": cp.status.value,
                "next_step_id": None,
                "skip_destructive_keys": list(cp.destructive_ops_done),
                "reason": "all_steps_completed_validate",
            }

    if failed and not pending:
        if can_transition(cp.status, MissionLifecycle.FAILED):
            try:
                transition_status(cp, MissionLifecycle.FAILED)
            except ValueError:
                cp.status = MissionLifecycle.FAILED
        return {
            "action": "fail",
            "status": cp.status.value,
            "next_step_id": None,
            "skip_destructive_keys": list(cp.destructive_ops_done),
            "reason": "steps_exhausted",
        }

    next_step = pending[0] if pending else None
    if cp.global_retry_count > 0 and next_step:
        if can_transition(cp.status, MissionLifecycle.RETRYING):
            try:
                transition_status(cp, MissionLifecycle.RETRYING)
            except ValueError:
                pass
        action = "retry_step"
    else:
        if can_transition(cp.status, MissionLifecycle.EXECUTING):
            try:
                transition_status(cp, MissionLifecycle.EXECUTING)
            except ValueError:
                pass
        action = "execute_step"

    return {
        "action": action,
        "status": cp.status.value,
        "next_step_id": next_step.step_id if next_step else None,
        "skip_destructive_keys": list(cp.destructive_ops_done),
        "reason": "resume_from_checkpoint",
        "last_provider": cp.last_provider,
        "last_model": cp.last_model,
        "global_retry_count": cp.global_retry_count,
    }


def complete_mission(cp: MissionCheckpoint, *, verified: bool = True) -> MissionCheckpoint:
    if not verified:
        if can_transition(cp.status, MissionLifecycle.FAILED):
            transition_status(cp, MissionLifecycle.FAILED)
        return cp
    if cp.status != MissionLifecycle.VALIDATING:
        if can_transition(cp.status, MissionLifecycle.VALIDATING):
            transition_status(cp, MissionLifecycle.VALIDATING)
    if can_transition(cp.status, MissionLifecycle.COMPLETED):
        transition_status(cp, MissionLifecycle.COMPLETED)
    return cp


async def persist_checkpoint_to_mission(
    cp: MissionCheckpoint,
    *,
    expected_version: Optional[int] = None,
) -> bool:
    """Write checkpoint into Mission.meta['coding_checkpoint'] (Postgres SoT).

    Optimistic concurrency: if expected_version is set, reject when the stored
    checkpoint version differs (stale worker). Always refuse to overwrite a
    sticky terminal status (completed/cancelled) with a non-matching status.
    """
    try:
        from core.database import AsyncSessionLocal, Mission
        from sqlalchemy import select

        async with AsyncSessionLocal() as db:
            r = await db.execute(select(Mission).where(Mission.id == cp.mission_id))
            row = r.scalar_one_or_none()
            if row is None:
                # Soft persist: memory only when no row (tests)
                _CHECKPOINTS[cp.mission_id] = cp
                return True
            meta = dict(row.meta or {})
            raw = meta.get("coding_checkpoint")
            if isinstance(raw, dict):
                stored_ver = int(raw.get("version") or 0)
                stored_status = str(raw.get("status") or "")
                if expected_version is not None and stored_ver != int(expected_version):
                    logger.warning(
                        "persist_checkpoint version_conflict mission=%s stored=%s expected=%s",
                        cp.mission_id, stored_ver, expected_version,
                    )
                    return False
                # Sticky terminal: do not allow stale non-terminal overwrite
                if stored_status in ("completed", "cancelled"):
                    new_st = cp.status.value if isinstance(cp.status, MissionLifecycle) else str(cp.status)
                    if new_st != stored_status:
                        logger.warning(
                            "persist_checkpoint refused terminal overwrite mission=%s stored=%s new=%s",
                            cp.mission_id, stored_status, new_st,
                        )
                        return False
            meta["coding_checkpoint"] = cp.to_dict()
            row.meta = meta
            # Keep Mission.status aligned with lifecycle (truthful)
            row.status = cp.status.value if isinstance(cp.status, MissionLifecycle) else str(cp.status)
            row.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
            await db.commit()
            _CHECKPOINTS[cp.mission_id] = cp
            return True
    except Exception as e:
        logger.warning("persist_checkpoint failed: %s", type(e).__name__)
        _CHECKPOINTS[cp.mission_id] = cp
        return False


async def load_checkpoint_from_mission(mission_id: str) -> Optional[MissionCheckpoint]:
    if mission_id in _CHECKPOINTS:
        return _CHECKPOINTS[mission_id]
    try:
        from core.database import AsyncSessionLocal, Mission
        from sqlalchemy import select

        async with AsyncSessionLocal() as db:
            r = await db.execute(select(Mission).where(Mission.id == mission_id))
            row = r.scalar_one_or_none()
            if not row:
                return None
            meta = row.meta or {}
            raw = meta.get("coding_checkpoint")
            if not isinstance(raw, dict):
                return None
            cp = MissionCheckpoint.from_dict(raw)
            _CHECKPOINTS[mission_id] = cp
            return cp
    except Exception as e:
        logger.debug("load_checkpoint failed: %s", type(e).__name__)
        return _CHECKPOINTS.get(mission_id)


def reset_checkpoints_for_tests() -> None:
    _CHECKPOINTS.clear()
