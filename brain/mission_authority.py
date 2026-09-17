"""Authoritative mission execution state.

Design rules (do not dilute):

1. MissionCheckpoint.status (MissionLifecycle) is the authoritative execution
   lifecycle for durable coding/orchestration missions that use checkpoints.
2. evaluate_mission_acceptance() is the only gate that may authorize transition
   to COMPLETED for artifact-producing coding missions.
3. Child records must NOT independently declare final mission success:
     - AgentTask.status
     - CodingLoopState.acceptance (provisional only)
     - NodeExecutionResult.success
     - ProviderAttemptState
     - ExecutionJob
     - SSE / frontend codingMission projections
4. Terminal states are sticky: COMPLETED and CANCELLED have no outbound
   transitions; FAILED may only reopen via explicit RECOVERY.
5. Cancellation wins over pending success when both race.
6. Provider exhaustion and provisional CodingLoop ACCEPT never imply COMPLETED.

State ownership matrix (concept → authority):

  Mission lifecycle status
    authority: MissionCheckpoint.status (MissionLifecycle)
    persist:   checkpoint store (in-proc + durable adapters)
    writers:   mission_authority.transition / declare_mission_outcome
    readers:   engine, recovery, SSE projector
    projection: coding_progress.status, plan.status (secondary)
    recovery:  checkpoint
    stale ok?: no for terminal; version must match for writers

  Mission.meta / plan.status (orchestration plan)
    authority: secondary projection of checkpoint when present; else plan-local
    persist:   orchestration_store
    writers:   mission_engine (must not set completed without acceptance path)
    recovery:  plan store + checkpoint preferred

  CodingLoopState
    authority: stage progress + provisional acceptance only
    persist:   durable coding loop blob (per mission)
    writers:   coding_loop_bridge
    must not:  set MissionLifecycle.COMPLETED

  AgentTask
    authority: single agent tool-loop run
    persist:   agent_task_store
    writers:   AgentRuntime
    must not:  set mission completed

  ProviderAttemptState
    authority: provider candidate rotation history
    persist:   attempt state / checkpoint.provider_errors
    writers:   provider_routing
    must not:  set mission completed (exhaustion → failed)

  Evidence / acceptance decision
    authority: coding_evidence + evaluate_mission_acceptance result
    persist:   evidence chain + checkpoint.evidence_refs / acceptance blob
    writers:   coding_evidence, mission_acceptance, declare_mission_outcome
    recovery:  evidence refs on checkpoint

  SSE / frontend codingMission
    authority: none (projection only)
    writers:   build_coding_progress (must include acceptance for success claims)
    recovery:  re-fetch checkpoint + acceptance

  A2A / delegation
    authority: delegation record for routing; not mission terminal
    writers:   a2a / delegation modules

  ExecutionJob / workers
    authority: job queue item lifecycle; child of mission
    writers:   job_queue
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from brain.mission_checkpoint import (
    TERMINAL,
    MissionCheckpoint,
    MissionLifecycle,
    can_transition,
    get_checkpoint,
    transition_status,
    begin_recovery,
)

logger = logging.getLogger("devos.mission_authority")

# Canonical public lifecycle names (match MissionLifecycle values)
LIFECYCLE_NAMES = tuple(m.value for m in MissionLifecycle)

# Terminal sticky rules
TERMINAL_STICKY = frozenset({
    MissionLifecycle.COMPLETED,
    MissionLifecycle.CANCELLED,
})


class MissionAuthorityError(Exception):
    """Illegal or concurrent mission state mutation."""

    def __init__(self, code: str, message: str, *, status: Optional[str] = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status

    def to_dict(self) -> dict:
        return {"code": self.code, "message": self.message, "status": self.status}


@dataclass
class MissionDecision:
    """Result of an authoritative outcome attempt."""
    ok: bool
    status: str
    version: int
    reason: str = ""
    acceptance: Optional[dict] = None
    rejected: bool = False
    code: str = ""

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "status": self.status,
            "version": self.version,
            "reason": self.reason,
            "acceptance": self.acceptance,
            "rejected": self.rejected,
            "code": self.code,
        }


def ownership_matrix() -> list[dict[str, Any]]:
    """Machine-readable state ownership matrix for audits and tests."""
    return [
        {
            "concept": "mission_lifecycle",
            "authoritative_source": "MissionCheckpoint.status",
            "persistence": "mission_checkpoint (+ durable adapter)",
            "writers": ["mission_authority.transition", "declare_mission_outcome"],
            "readers": ["mission_engine", "recovery", "SSE projector"],
            "projection": "plan.status, coding_progress.status",
            "recovery_source": "MissionCheckpoint",
            "can_be_stale": False,
            "independent_terminal": True,
        },
        {
            "concept": "coding_loop_acceptance",
            "authoritative_source": "provisional only (CodingLoopState.acceptance)",
            "persistence": "coding loop durable blob",
            "writers": ["coding_loop_bridge"],
            "readers": ["mission_acceptance input"],
            "projection": "SSE stage events",
            "recovery_source": "CodingLoopState",
            "can_be_stale": True,
            "independent_terminal": False,
        },
        {
            "concept": "agent_task",
            "authoritative_source": "AgentTask.status (single run)",
            "persistence": "agent_task_store",
            "writers": ["AgentRuntime"],
            "readers": ["orchestration_runtime"],
            "projection": "agent.* SSE events",
            "recovery_source": "AgentTask + checkpoint",
            "can_be_stale": True,
            "independent_terminal": False,
        },
        {
            "concept": "provider_attempts",
            "authoritative_source": "ProviderAttemptState / checkpoint.provider_errors",
            "persistence": "checkpoint.provider_errors",
            "writers": ["provider_routing", "record_provider_failure"],
            "readers": ["mission recovery"],
            "projection": "coding_progress.provider",
            "recovery_source": "checkpoint",
            "can_be_stale": False,
            "independent_terminal": False,
        },
        {
            "concept": "evidence_acceptance",
            "authoritative_source": "evaluate_mission_acceptance",
            "persistence": "evidence chain + checkpoint.evidence_refs",
            "writers": ["declare_mission_outcome"],
            "readers": ["SSE final, frontend"],
            "projection": "coding_progress.acceptance",
            "recovery_source": "evidence refs",
            "can_be_stale": False,
            "independent_terminal": True,
        },
        {
            "concept": "sse_frontend",
            "authoritative_source": "none (projection)",
            "persistence": "none",
            "writers": ["build_coding_progress"],
            "readers": ["browser"],
            "projection": "self",
            "recovery_source": "re-fetch checkpoint",
            "can_be_stale": True,
            "independent_terminal": False,
        },
        {
            "concept": "execution_job",
            "authoritative_source": "job queue item (child)",
            "persistence": "job store",
            "writers": ["job_queue"],
            "readers": ["workers"],
            "projection": "job events",
            "recovery_source": "job store + checkpoint",
            "can_be_stale": True,
            "independent_terminal": False,
        },
        {
            "concept": "a2a_delegation",
            "authoritative_source": "delegation record (routing)",
            "persistence": "a2a / mission meta",
            "writers": ["delegation", "a2a"],
            "readers": ["mission_engine"],
            "projection": "optional",
            "recovery_source": "delegation record",
            "can_be_stale": True,
            "independent_terminal": False,
        },
    ]


def is_terminal(status: MissionLifecycle | str) -> bool:
    from brain.mission_checkpoint import _as_lifecycle
    return _as_lifecycle(status) in TERMINAL


def is_sticky_terminal(status: MissionLifecycle | str) -> bool:
    from brain.mission_checkpoint import _as_lifecycle
    return _as_lifecycle(status) in TERMINAL_STICKY


def assert_can_transition(
    current: MissionLifecycle | str,
    target: MissionLifecycle | str,
) -> None:
    if not can_transition(current, target):
        from brain.mission_checkpoint import _as_lifecycle
        c, t = _as_lifecycle(current), _as_lifecycle(target)
        raise MissionAuthorityError(
            "illegal_transition",
            f"illegal transition {c.value} → {t.value}",
            status=c.value,
        )


def transition(
    cp: MissionCheckpoint,
    new_status: MissionLifecycle | str,
    *,
    expected_version: Optional[int] = None,
    reason: str = "",
) -> MissionCheckpoint:
    """Version-aware lifecycle transition. Terminal sticky states cannot leave."""
    if expected_version is not None and int(cp.version) != int(expected_version):
        raise MissionAuthorityError(
            "version_conflict",
            f"expected version {expected_version}, found {cp.version}",
            status=cp.status.value if isinstance(cp.status, MissionLifecycle) else str(cp.status),
        )
    if is_sticky_terminal(cp.status):
        target = new_status
        from brain.mission_checkpoint import _as_lifecycle
        if _as_lifecycle(target) != _as_lifecycle(cp.status):
            raise MissionAuthorityError(
                "terminal_sticky",
                f"cannot leave sticky terminal state {cp.status}",
                status=cp.status.value if isinstance(cp.status, MissionLifecycle) else str(cp.status),
            )
        return cp
    assert_can_transition(cp.status, new_status)
    cp = transition_status(cp, new_status)
    if reason:
        meta = getattr(cp, "meta", None)
        if isinstance(meta, dict):
            meta["last_transition_reason"] = reason[:300]
        else:
            try:
                cp.recovery_note = (cp.recovery_note or "") + f"|{reason[:120]}"
            except Exception:
                pass
    logger.info(
        "mission_transition mission=%s status=%s version=%s reason=%s",
        cp.mission_id, cp.status, cp.version, reason[:80],
    )
    return cp


def declare_mission_outcome(
    cp: MissionCheckpoint,
    *,
    desired: MissionLifecycle | str,
    acceptance: Optional[dict] = None,
    execution_ok: Optional[bool] = None,
    provider_exhausted: bool = False,
    cancelled: bool = False,
    expected_version: Optional[int] = None,
    files_changed: Optional[list] = None,
    coding_evidence: Optional[dict] = None,
    user_id: Optional[str] = None,
) -> MissionDecision:
    """Apply a terminal or failure outcome under authority rules.

    COMPLETED requires acceptance.ok (or non-artifact path with execution_ok and
    no provider exhaustion). CANCELLED always wins over pending success.
    """
    from brain.mission_checkpoint import _as_lifecycle
    from brain.mission_acceptance import evaluate_mission_acceptance

    target = _as_lifecycle(desired)

    if expected_version is not None and int(cp.version) != int(expected_version):
        return MissionDecision(
            ok=False,
            status=cp.status.value if isinstance(cp.status, MissionLifecycle) else str(cp.status),
            version=cp.version,
            reason="version_conflict",
            rejected=True,
            code="version_conflict",
        )

    # Sticky terminal: no overwrite
    if is_sticky_terminal(cp.status):
        return MissionDecision(
            ok=cp.status == target,
            status=cp.status.value if isinstance(cp.status, MissionLifecycle) else str(cp.status),
            version=cp.version,
            reason="already_terminal",
            rejected=cp.status != target,
            code="terminal_sticky",
            acceptance=acceptance,
        )

    # Cancellation wins
    if cancelled or target == MissionLifecycle.CANCELLED:
        if can_transition(cp.status, MissionLifecycle.CANCELLED):
            transition(cp, MissionLifecycle.CANCELLED, reason="cancelled")
        return MissionDecision(
            ok=True,
            status=MissionLifecycle.CANCELLED.value,
            version=cp.version,
            reason="cancelled",
            code="cancelled",
        )

    # Provider exhaustion cannot become success
    if provider_exhausted and target == MissionLifecycle.COMPLETED:
        if can_transition(cp.status, MissionLifecycle.FAILED):
            transition(cp, MissionLifecycle.FAILED, reason="provider_exhausted")
        return MissionDecision(
            ok=False,
            status=MissionLifecycle.FAILED.value,
            version=cp.version,
            reason="provider_exhausted",
            rejected=True,
            code="provider_exhausted",
        )

    if target == MissionLifecycle.COMPLETED:
        # Must pass through acceptance
        acc = acceptance
        if acc is None:
            acc = evaluate_mission_acceptance(
                execution_ok=bool(execution_ok),
                status="accepted",
                files_changed=files_changed or [],
                mission_id=cp.mission_id,
                user_id=user_id or getattr(cp, "user_id", None),
                coding_evidence=coding_evidence,
                require_coding_evidence=bool(coding_evidence is not None or files_changed),
            )
        if not (acc or {}).get("ok"):
            # Acceptance denied → failed or stay validating
            if can_transition(cp.status, MissionLifecycle.FAILED):
                try:
                    transition(cp, MissionLifecycle.FAILED, reason="acceptance_denied")
                except MissionAuthorityError:
                    pass
            return MissionDecision(
                ok=False,
                status=cp.status.value if isinstance(cp.status, MissionLifecycle) else str(cp.status),
                version=cp.version,
                reason=(acc or {}).get("reason") or "acceptance_denied",
                acceptance=acc,
                rejected=True,
                code="acceptance_denied",
            )
        # Route via VALIDATING if needed
        if cp.status == MissionLifecycle.EXECUTING and can_transition(
            cp.status, MissionLifecycle.VALIDATING
        ):
            transition(cp, MissionLifecycle.VALIDATING, reason="pre_complete_validate")
        if can_transition(cp.status, MissionLifecycle.COMPLETED):
            transition(cp, MissionLifecycle.COMPLETED, reason="acceptance_ok")
            return MissionDecision(
                ok=True,
                status=MissionLifecycle.COMPLETED.value,
                version=cp.version,
                reason="accepted",
                acceptance=acc,
                code="completed",
            )
        return MissionDecision(
            ok=False,
            status=cp.status.value if isinstance(cp.status, MissionLifecycle) else str(cp.status),
            version=cp.version,
            reason="cannot_transition_to_completed",
            acceptance=acc,
            rejected=True,
            code="illegal_transition",
        )

    if target == MissionLifecycle.FAILED:
        if can_transition(cp.status, MissionLifecycle.FAILED):
            transition(cp, MissionLifecycle.FAILED, reason="failed")
        return MissionDecision(
            ok=True,
            status=MissionLifecycle.FAILED.value,
            version=cp.version,
            reason="failed",
            code="failed",
        )

    # Non-terminal desired status
    try:
        transition(cp, target, expected_version=None, reason="explicit")
        return MissionDecision(
            ok=True,
            status=cp.status.value if isinstance(cp.status, MissionLifecycle) else str(cp.status),
            version=cp.version,
            reason="transitioned",
            code="ok",
        )
    except (MissionAuthorityError, ValueError) as e:
        return MissionDecision(
            ok=False,
            status=cp.status.value if isinstance(cp.status, MissionLifecycle) else str(cp.status),
            version=cp.version,
            reason=str(e),
            rejected=True,
            code="illegal_transition",
        )


def project_for_sse(
    cp: Optional[MissionCheckpoint],
    *,
    provisional_acceptance: Optional[dict] = None,
    agent_success: Optional[bool] = None,
    provider_failure: Optional[dict] = None,
) -> dict[str, Any]:
    """SSE/UI projection. Never invents final success without authority."""
    status = "unknown"
    version = 0
    if cp is not None:
        status = cp.status.value if isinstance(cp.status, MissionLifecycle) else str(cp.status)
        version = int(cp.version or 0)

    final_success = status == MissionLifecycle.COMPLETED.value
    # Projections that must NOT flip final_success true:
    if provisional_acceptance and provisional_acceptance.get("ok"):
        # still provisional unless lifecycle is COMPLETED
        pass
    if agent_success:
        pass
    if provider_failure:
        final_success = False
        if status not in (
            MissionLifecycle.FAILED.value,
            MissionLifecycle.CANCELLED.value,
            MissionLifecycle.COMPLETED.value,
        ):
            status = status or MissionLifecycle.FAILED.value

    return {
        "mission_id": getattr(cp, "mission_id", None) if cp else None,
        "status": status,
        "version": version,
        "final_success": final_success,
        "provisional_acceptance": bool((provisional_acceptance or {}).get("ok")),
        "terminal": status in {m.value for m in TERMINAL},
        "authority": "mission_checkpoint",
        "projection": True,
    }


def child_cannot_complete_mission(source: str) -> bool:
    """Documented sources that must not set MissionLifecycle.COMPLETED alone."""
    return source in {
        "coding_loop_accept",
        "agent.completed",
        "agent.agent_completed",
        "provider_response",
        "sse_progress",
        "frontend_infer",
        "node_execution_result",
        "execution_job",
    }
