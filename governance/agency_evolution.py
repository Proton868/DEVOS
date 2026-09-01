"""Agency evolution — evidence → evaluation → competency → human-gated promotion.

Workers never self-promote. The trust engine proposes; a human approves
(permanent or time-bounded). Demotion on policy violations is automatic.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Optional, Any

from governance.identity_context import AutonomyProfile
from governance.ucip import TrustLevel

logger = logging.getLogger("devos.agency_evolution")

# Global thresholds only *propose* promotion — never auto-apply
_PROMOTE_PROPOSALS = [
    # (min_success, max_fail_rate, max_unauthorized, min_avg_competency, trust, autonomy)
    (50, 0.15, 0, 0.70, "operator", AutonomyProfile.BOUNDED.value),
    (500, 0.05, 0, 0.85, "autonomous", AutonomyProfile.AUTONOMOUS.value),
    (4000, 0.01, 0, 0.92, "autonomous", AutonomyProfile.FULL_AUTONOMOUS.value),
]

ALWAYS_HUMAN_GATED = {
    "ucip:system.shell",
    "ucip:secret.read",
    "ucip:filesystem.delete",
    "ucip:vcs.push",
    "financial.transfer",
    "governance.change",
}

# Minimum competency for a capability to be exercised autonomously
CAP_AUTONOMY_THRESHOLD = 0.80


@dataclass
class EvaluationResult:
    """Multi-signal evaluation of a single task outcome."""
    success: bool
    correctness: float = 0.0       # 0-1
    policy_compliance: float = 1.0  # 0-1
    side_effect_score: float = 1.0  # 1 = clean, 0 = harmful side effects
    efficiency: float = 0.5
    unauthorized_attempt: bool = False
    capabilities_used: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def competency_delta(self) -> float:
        """Weighted composite used to update per-capability competency."""
        if self.unauthorized_attempt:
            return 0.0
        if not self.success:
            return max(0.0, 0.2 * self.policy_compliance)
        return (
            0.45 * self.correctness
            + 0.25 * self.policy_compliance
            + 0.15 * self.side_effect_score
            + 0.15 * self.efficiency
        )

    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "correctness": self.correctness,
            "policy_compliance": self.policy_compliance,
            "side_effect_score": self.side_effect_score,
            "efficiency": self.efficiency,
            "unauthorized_attempt": self.unauthorized_attempt,
            "capabilities_used": self.capabilities_used,
            "competency_delta": self.competency_delta,
            "notes": self.notes,
        }


def evaluate_execution(
    *,
    success: bool,
    status: Optional[str] = None,
    unauthorized: bool = False,
    capabilities_used: Optional[list[str]] = None,
    sandbox_violations: Optional[list] = None,
    expected_outcome_met: Optional[bool] = None,
    steps: int = 0,
    max_steps: int = 20,
    user_feedback: Optional[float] = None,  # 0-1 if human rated
) -> EvaluationResult:
    """Turn raw execution evidence into an EvaluationResult.

    exit_code/status alone is not enough — we fold in policy, side effects,
    optional outcome-schema match, and efficiency.
    """
    notes: list[str] = []
    violations = sandbox_violations or []
    caps = list(capabilities_used or [])

    # Correctness
    if expected_outcome_met is True:
        correctness = 1.0
        notes.append("expected_outcome_met")
    elif expected_outcome_met is False:
        correctness = 0.15
        notes.append("expected_outcome_missed")
        success = False
    elif success and status in (None, "success", "ok", "completed"):
        correctness = 0.75  # completed without schema check — partial credit
        notes.append("completed_no_schema")
    elif success:
        correctness = 0.6
    else:
        correctness = 0.1
        notes.append("execution_failed")

    if user_feedback is not None:
        correctness = 0.5 * correctness + 0.5 * max(0.0, min(1.0, user_feedback))
        notes.append(f"user_feedback={user_feedback}")

    # Policy compliance
    policy = 1.0
    if unauthorized:
        policy = 0.0
        notes.append("unauthorized_capability")
    if violations:
        policy = min(policy, 0.3)
        notes.append(f"sandbox_violations={len(violations)}")

    # Side effects — critical violations tank the score
    side = 1.0
    for v in violations:
        vs = str(v).lower()
        if "critical" in vs or "isolation" in vs:
            side = min(side, 0.1)
        elif "timeout" in vs:
            side = min(side, 0.5)

    # Efficiency
    if max_steps > 0 and steps > 0:
        efficiency = max(0.2, 1.0 - (steps / max(max_steps, 1)) * 0.5)
    else:
        efficiency = 0.5 if success else 0.3

    return EvaluationResult(
        success=bool(success) and not unauthorized,
        correctness=correctness,
        policy_compliance=policy,
        side_effect_score=side,
        efficiency=efficiency,
        unauthorized_attempt=bool(unauthorized),
        capabilities_used=caps,
        notes=notes,
    )


async def get_or_create_trust(db, tenant_id: str, worker_id: str):
    from sqlalchemy import select
    from core.database import WorkerTrustRecord, gen_id
    r = await db.execute(
        select(WorkerTrustRecord).where(
            WorkerTrustRecord.tenant_id == tenant_id,
            WorkerTrustRecord.worker_id == worker_id,
        )
    )
    row = r.scalar_one_or_none()
    if row:
        return row
    row = WorkerTrustRecord(
        id=gen_id(),
        tenant_id=tenant_id,
        worker_id=worker_id,
        trust_level="supervised",
        autonomy=AutonomyProfile.SUPERVISED.value,
        granted_caps=[],
        competency={},
        evidence={},
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


def _effective_autonomy(row) -> str:
    """Autonomy may expire if promotion was temporary."""
    exp = getattr(row, "promotion_expires_at", None)
    if exp is not None:
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) > exp:
            return AutonomyProfile.SUPERVISED.value
    return row.autonomy or AutonomyProfile.SUPERVISED.value


def _update_competency(row, evaluation: EvaluationResult) -> None:
    comp = dict(row.competency or {})
    delta = evaluation.competency_delta
    for cap in evaluation.capabilities_used or ["ucip:general"]:
        if cap in ALWAYS_HUMAN_GATED:
            continue
        entry = dict(comp.get(cap) or {"success": 0, "failure": 0, "competency": 0.5, "samples": 0})
        if evaluation.success and not evaluation.unauthorized_attempt:
            entry["success"] = int(entry.get("success", 0)) + 1
        else:
            entry["failure"] = int(entry.get("failure", 0)) + 1
        samples = int(entry.get("samples", 0)) + 1
        prev = float(entry.get("competency", 0.5))
        # Exponential moving average toward this task's score
        entry["competency"] = round(0.85 * prev + 0.15 * delta, 4)
        entry["samples"] = samples
        entry["last_score"] = round(delta, 4)
        comp[cap] = entry
    row.competency = comp


def _avg_competency(row) -> float:
    comp = row.competency or {}
    if not comp:
        return 0.0
    vals = [float(v.get("competency", 0)) for v in comp.values() if isinstance(v, dict)]
    return sum(vals) / len(vals) if vals else 0.0


def _propose_promotion_if_eligible(row) -> None:
    """Never auto-apply. Only set pending_promotion for human approval."""
    # Auto-demote on bad behavior (restriction is automatic)
    total = (row.success_count or 0) + (row.failure_count or 0)
    if total == 0:
        return
    fail_rate = (row.failure_count or 0) / total
    unauth = row.unauthorized_attempts or 0
    if unauth > 0 or fail_rate > 0.3:
        row.trust_level = "supervised"
        row.autonomy = AutonomyProfile.SUPERVISED.value
        row.pending_promotion = None
        row.promotion_expires_at = None
        row.evidence = {
            **(row.evidence or {}),
            "last_demotion": datetime.now(timezone.utc).isoformat(),
            "demotion_reason": "unauthorized" if unauth > 0 else "high_failure_rate",
        }
        return

    avg_c = _avg_competency(row)
    best = None
    for min_s, max_fr, max_u, min_comp, tl, auto in _PROMOTE_PROPOSALS:
        if (
            (row.success_count or 0) >= min_s
            and fail_rate <= max_fr
            and unauth <= max_u
            and avg_c >= min_comp
        ):
            best = (tl, auto, min_s, avg_c)

    if not best:
        return
    tl, auto, min_s, avg_c = best
    # Don't re-propose same or lower level
    order = {
        AutonomyProfile.SUPERVISED.value: 0,
        AutonomyProfile.BOUNDED.value: 1,
        AutonomyProfile.AUTONOMOUS.value: 2,
        AutonomyProfile.FULL_AUTONOMOUS.value: 3,
    }
    current = order.get(_effective_autonomy(row), 0)
    proposed = order.get(auto, 0)
    if proposed <= current and not row.pending_promotion:
        return
    if row.pending_promotion and row.pending_promotion.get("autonomy") == auto:
        return
    row.pending_promotion = {
        "trust_level": tl,
        "autonomy": auto,
        "proposed_at": datetime.now(timezone.utc).isoformat(),
        "reason": f"eligible: successes>={min_s}, avg_competency={avg_c:.3f}, fail_rate={fail_rate:.3f}",
        "success_count": row.success_count,
        "avg_competency": round(avg_c, 4),
    }


async def record_outcome(
    db,
    tenant_id: str,
    worker_id: str,
    *,
    evaluation: Optional[EvaluationResult] = None,
    success: bool = False,
    unauthorized: bool = False,
    capability: Optional[str] = None,
    capabilities_used: Optional[list[str]] = None,
    **eval_kwargs,
):
    """Record execution evidence and update competency. May propose promotion."""
    if evaluation is None:
        caps = list(capabilities_used or [])
        if capability:
            caps.append(capability)
        evaluation = evaluate_execution(
            success=success,
            unauthorized=unauthorized,
            capabilities_used=caps,
            **eval_kwargs,
        )
    row = await get_or_create_trust(db, tenant_id, worker_id)
    if evaluation.success:
        row.success_count = (row.success_count or 0) + 1
    else:
        row.failure_count = (row.failure_count or 0) + 1
    if evaluation.unauthorized_attempt:
        row.unauthorized_attempts = (row.unauthorized_attempts or 0) + 1
    _update_competency(row, evaluation)
    hist = list((row.evidence or {}).get("recent") or [])
    hist.append({"at": datetime.now(timezone.utc).isoformat(), **evaluation.to_dict()})
    row.evidence = {**(row.evidence or {}), "recent": hist[-50:]}
    _propose_promotion_if_eligible(row)
    row.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(row)
    return row


async def approve_promotion(
    db,
    tenant_id: str,
    worker_id: str,
    *,
    approved_by: str,
    permanent: bool = True,
    duration_hours: Optional[int] = None,
) -> Any:
    """Human approves pending promotion (permanent or time-bounded)."""
    row = await get_or_create_trust(db, tenant_id, worker_id)
    pending = row.pending_promotion
    if not pending:
        raise ValueError("no pending promotion")
    row.trust_level = pending.get("trust_level") or row.trust_level
    row.autonomy = pending.get("autonomy") or row.autonomy
    row.pending_promotion = None
    row.approved_by = approved_by
    row.approved_at = datetime.now(timezone.utc)
    if permanent or not duration_hours:
        row.promotion_expires_at = None
    else:
        row.promotion_expires_at = datetime.now(timezone.utc) + timedelta(hours=int(duration_hours))
    row.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(row)
    return row


async def reject_promotion(db, tenant_id: str, worker_id: str, *, rejected_by: str) -> Any:
    row = await get_or_create_trust(db, tenant_id, worker_id)
    row.pending_promotion = None
    row.evidence = {
        **(row.evidence or {}),
        "last_rejection": {
            "by": rejected_by,
            "at": datetime.now(timezone.utc).isoformat(),
        },
    }
    row.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(row)
    return row


async def demote_worker(
    db,
    tenant_id: str,
    worker_id: str,
    *,
    demoted_by: str,
    reason: str = "human_demotion",
    autonomy: str = "supervised",
) -> Any:
    row = await get_or_create_trust(db, tenant_id, worker_id)
    row.autonomy = autonomy
    row.trust_level = "supervised" if autonomy == "supervised" else row.trust_level
    row.pending_promotion = None
    row.promotion_expires_at = None
    row.evidence = {
        **(row.evidence or {}),
        "last_demotion": {
            "by": demoted_by,
            "at": datetime.now(timezone.utc).isoformat(),
            "reason": reason,
        },
    }
    row.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(row)
    return row


def trust_level_from_record(row) -> TrustLevel:
    mapping = {
        "read_only": TrustLevel.READ_ONLY,
        "assistant": TrustLevel.ASSISTANT,
        "supervised": TrustLevel.ASSISTANT,
        "operator": TrustLevel.OPERATOR,
        "autonomous": TrustLevel.AUTONOMOUS,
        "root": TrustLevel.ROOT,
    }
    # Expired temporary promotion falls back
    if _effective_autonomy(row) == AutonomyProfile.SUPERVISED.value:
        return TrustLevel.ASSISTANT
    return mapping.get((row.trust_level or "").lower(), TrustLevel.OPERATOR)


def filter_autonomous_caps(caps: set[str], row=None) -> set[str]:
    """Capability-aware filter: always-gated removed; others need competency."""
    out = set()
    for c in caps:
        if c in ALWAYS_HUMAN_GATED:
            continue
        if row is None:
            out.add(c)
            continue
        if _effective_autonomy(row) in (
            AutonomyProfile.SUPERVISED.value,
            AutonomyProfile.BOUNDED.value,
        ):
            # Bounded/supervised: allow but runtime may still HITL
            out.add(c)
            continue
        entry = (row.competency or {}).get(c) or {}
        score = float(entry.get("competency", 0))
        samples = int(entry.get("samples", 0))
        # Autonomous for this cap only if earned competency
        if samples >= 5 and score >= CAP_AUTONOMY_THRESHOLD:
            out.add(c)
        elif samples < 5:
            # Not enough samples — still allow under supervision path
            out.add(c)
    return out


def autonomy_allows_without_hitl(row, capability: str) -> bool:
    """Whether this worker may exercise capability without HITL."""
    if capability in ALWAYS_HUMAN_GATED:
        return False
    auto = _effective_autonomy(row)
    if auto == AutonomyProfile.SUPERVISED.value:
        return False
    if auto == AutonomyProfile.BOUNDED.value:
        entry = (row.competency or {}).get(capability) or {}
        return float(entry.get("competency", 0)) >= CAP_AUTONOMY_THRESHOLD and int(entry.get("samples", 0)) >= 10
    # AUTONOMOUS / FULL_AUTONOMOUS
    entry = (row.competency or {}).get(capability) or {}
    return float(entry.get("competency", 0)) >= CAP_AUTONOMY_THRESHOLD and int(entry.get("samples", 0)) >= 5
