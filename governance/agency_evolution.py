"""Agency evolution — evidence → evaluation → competency → human-gated promotion.

Fail-closed: if trust cannot be loaded, workers must not execute.
Promotion is proposed only; humans approve permanent or time-bounded autonomy.
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Optional, Any

from governance.identity_context import AutonomyProfile
from governance.ucip import TrustLevel

logger = logging.getLogger("devos.agency_evolution")

# Global promotion proposals (never auto-applied)
_PROMOTE_PROPOSALS = [
    # min_success, max_fail_rate, max_unauth, min_avg, min_floor, min_samples_any, trust, autonomy
    (50, 0.15, 0, 0.70, 0.55, 10, "operator", AutonomyProfile.BOUNDED.value),
    (500, 0.05, 0, 0.85, 0.60, 25, "autonomous", AutonomyProfile.AUTONOMOUS.value),
    (4000, 0.01, 0, 0.92, 0.70, 50, "autonomous", AutonomyProfile.FULL_AUTONOMOUS.value),
]

ALWAYS_HUMAN_GATED = {
    "ucip:system.shell",
    "ucip:secret.read",
    "ucip:filesystem.delete",
    "ucip:vcs.push",
    "financial.transfer",
    "governance.change",
}

# High-risk caps need stronger evidence even when not permanently gated
HIGH_RISK_CAPS = {
    "ucip:agent.spawn",
    "ucip:network.outbound",
    "ucip:api.call",
    "ucip:filesystem.write",
    "ucip:vcs.write",
}

# Supervised workers may only use these (read-ish / low risk) until promoted
SUPERVISED_CAPS = {
    "ucip:memory.read",
    "ucip:search.web",
    "ucip:filesystem.read",
    "ucip:general",
}

CAP_AUTONOMY_THRESHOLD = 0.80
CAP_AUTONOMY_MIN_SAMPLES = 25
HIGH_RISK_THRESHOLD = 0.90
HIGH_RISK_MIN_SAMPLES = 100
BOUNDED_THRESHOLD = 0.80
BOUNDED_MIN_SAMPLES = 10

# Prior for EMA — conservative; new caps start uncertain, not trusted
COMPETENCY_PRIOR = 0.40


class TrustLoadError(Exception):
    """Trust store unavailable or corrupt — must fail closed."""


@dataclass
class TrustSnapshot:
    """Immutable authority snapshot for one execution job.

    load trust → snapshot → execute → evidence → update trust
    All phases share execution_job_id for multi-node consistency.
    """
    execution_job_id: str
    tenant_id: str
    worker_id: str
    trust_level: str
    autonomy: str
    competency: dict
    permitted_caps: list
    granted_caps: list
    success_count: int = 0
    failure_count: int = 0
    unauthorized_attempts: int = 0
    snapshot_at: str = ""

    def to_dict(self) -> dict:
        return {
            "execution_job_id": self.execution_job_id,
            "tenant_id": self.tenant_id,
            "worker_id": self.worker_id,
            "trust_level": self.trust_level,
            "autonomy": self.autonomy,
            "competency": self.competency,
            "permitted_caps": self.permitted_caps,
            "granted_caps": self.granted_caps,
            "success_count": self.success_count,
            "failure_count": self.failure_count,
            "unauthorized_attempts": self.unauthorized_attempts,
            "snapshot_at": self.snapshot_at,
        }


def capability_autonomy_level(row, capability: str) -> str:
    """Per-capability autonomy derived from competency + global floor."""
    if capability in ALWAYS_HUMAN_GATED:
        return AutonomyProfile.SUPERVISED.value
    global_auto = _effective_autonomy(row)
    if global_auto == AutonomyProfile.SUPERVISED.value:
        return AutonomyProfile.SUPERVISED.value
    if capability in SUPERVISED_CAPS:
        # low-risk: at least global level
        return global_auto
    entry = {}
    comp = row.competency if isinstance(getattr(row, "competency", None), dict) else {}
    if isinstance(comp.get(capability), dict):
        entry = comp[capability]
    # explicit override on entry if human set it
    if entry.get("autonomy_override"):
        return str(entry["autonomy_override"])
    high = capability in HIGH_RISK_CAPS
    if _cap_earned(entry, high_risk=high, bounded=False):
        return global_auto if global_auto in (
            AutonomyProfile.AUTONOMOUS.value, AutonomyProfile.FULL_AUTONOMOUS.value
        ) else AutonomyProfile.AUTONOMOUS.value
    if _cap_earned(entry, high_risk=high, bounded=True):
        return AutonomyProfile.BOUNDED.value
    return AutonomyProfile.SUPERVISED.value


async def snapshot_trust(
    db, tenant_id: str, worker_id: str, *, execution_job_id: str, persona_caps: set
) -> "TrustSnapshot":
    """Load trust, compute permitted caps, return immutable snapshot for this job."""
    row = await get_or_create_trust(db, tenant_id, worker_id)
    permitted = filter_autonomous_caps(set(persona_caps or set()), row)
    return TrustSnapshot(
        execution_job_id=execution_job_id,
        tenant_id=tenant_id,
        worker_id=worker_id,
        trust_level=row.trust_level or "supervised",
        autonomy=_effective_autonomy(row),
        competency=dict(row.competency or {}),
        permitted_caps=sorted(permitted),
        granted_caps=list(row.granted_caps or []),
        success_count=int(row.success_count or 0),
        failure_count=int(row.failure_count or 0),
        unauthorized_attempts=int(row.unauthorized_attempts or 0),
        snapshot_at=datetime.now(timezone.utc).isoformat(),
    )



@dataclass
class EvaluationResult:
    success: bool
    correctness: float = 0.0
    policy_compliance: float = 1.0
    side_effect_score: float = 1.0
    efficiency: float = 0.5
    unauthorized_attempt: bool = False
    capabilities_used: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    evidence_ref: Optional[str] = None
    evidence_hash: Optional[str] = None

    @property
    def competency_delta(self) -> float:
        if self.unauthorized_attempt:
            return 0.0
        if not self.success:
            return max(0.0, 0.15 * self.policy_compliance)
        return (
            0.50 * self.correctness
            + 0.25 * self.policy_compliance
            + 0.15 * self.side_effect_score
            + 0.10 * self.efficiency
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
            "evidence_ref": self.evidence_ref,
            "evidence_hash": self.evidence_hash,
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
    user_feedback: Optional[float] = None,
) -> EvaluationResult:
    """Evaluate task outcome. Schema match is authoritative when present.

    Without a schema, completed runs get conservative partial credit only —
    not treated as proven correctness.
    """
    notes: list[str] = []
    violations = sandbox_violations or []
    caps = list(capabilities_used or [])

    if expected_outcome_met is True:
        correctness = 1.0
        notes.append("expected_outcome_met")
    elif expected_outcome_met is False:
        correctness = 0.05
        notes.append("expected_outcome_missed")
        success = False
    elif success and status in (None, "success", "ok", "completed", "complete"):
        # Fallback only — not real evaluation
        correctness = 0.55
        notes.append("completed_without_schema_partial_credit")
    elif success:
        correctness = 0.45
        notes.append("completed_ambiguous")
    else:
        correctness = 0.05
        notes.append("execution_failed")

    if user_feedback is not None:
        correctness = 0.4 * correctness + 0.6 * max(0.0, min(1.0, user_feedback))
        notes.append(f"user_feedback={user_feedback}")

    policy = 1.0
    if unauthorized:
        policy = 0.0
        notes.append("unauthorized_capability")
    if violations:
        policy = min(policy, 0.3)
        notes.append(f"sandbox_violations={len(violations)}")

    side = 1.0
    for v in violations:
        vs = str(v).lower()
        if "critical" in vs or "isolation" in vs:
            side = min(side, 0.1)
        elif "timeout" in vs:
            side = min(side, 0.5)

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
    """Load trust row or create supervised baseline. Raises TrustLoadError on DB failure."""
    try:
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
    except TrustLoadError:
        raise
    except Exception as e:
        logger.error("trust load failed: %s", e)
        raise TrustLoadError(f"cannot load WorkerTrustRecord: {e}") from e


def require_trust_row(row) -> None:
    if row is None:
        raise TrustLoadError("WorkerTrustRecord missing")


def _effective_autonomy(row) -> str:
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
        entry = dict(
            comp.get(cap)
            or {
                "success": 0,
                "failure": 0,
                "competency": COMPETENCY_PRIOR,
                "samples": 0,
            }
        )
        if evaluation.success and not evaluation.unauthorized_attempt:
            entry["success"] = int(entry.get("success", 0)) + 1
        else:
            entry["failure"] = int(entry.get("failure", 0)) + 1
        samples = int(entry.get("samples", 0)) + 1
        prev = float(entry.get("competency", COMPETENCY_PRIOR))
        # EMA — prior pulls toward uncertainty early; samples dominate later
        alpha = min(0.25, 0.10 + samples * 0.005)
        entry["competency"] = round((1 - alpha) * prev + alpha * delta, 4)
        entry["samples"] = samples
        entry["last_score"] = round(delta, 4)
        comp[cap] = entry
    row.competency = comp


def _avg_competency(row) -> float:
    comp = row.competency if isinstance(getattr(row, "competency", None), dict) else {}
    vals = []
    for v in comp.values():
        if not isinstance(v, dict):
            continue
        try:
            vals.append(float(v.get("competency", 0) or 0))
        except (TypeError, ValueError):
            continue
    return sum(vals) / len(vals) if vals else 0.0


def _min_competency(row) -> float:
    comp = row.competency if isinstance(getattr(row, "competency", None), dict) else {}
    vals = []
    for v in comp.values():
        if not isinstance(v, dict):
            continue
        try:
            samples = int(v.get("samples", 0) or 0)
            if samples < 5:
                continue
            vals.append(float(v.get("competency", 0) or 0))
        except (TypeError, ValueError):
            continue
    return min(vals) if vals else 0.0


def _min_samples(row) -> int:
    """Minimum samples across well-formed competency entries. Never raises."""
    comp = row.competency if isinstance(getattr(row, "competency", None), dict) else {}
    samples_list = []
    for v in comp.values():
        if not isinstance(v, dict):
            continue
        try:
            samples_list.append(int(v.get("samples", 0) or 0))
        except (TypeError, ValueError):
            continue
    if not samples_list:
        return 0
    return min(samples_list)


def _propose_promotion_if_eligible(row) -> None:
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
    floor_c = _min_competency(row)
    min_s = _min_samples(row)
    best = None
    for min_succ, max_fr, max_u, min_avg, min_floor, min_samp, tl, auto in _PROMOTE_PROPOSALS:
        if (
            (row.success_count or 0) >= min_succ
            and fail_rate <= max_fr
            and unauth <= max_u
            and avg_c >= min_avg
            and floor_c >= min_floor
            and min_s >= min_samp
        ):
            best = (tl, auto, min_succ, avg_c, floor_c)

    if not best:
        return
    tl, auto, min_succ, avg_c, floor_c = best
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
        "reason": (
            f"eligible: successes>={min_succ}, avg={avg_c:.3f}, "
            f"floor={floor_c:.3f}, fail_rate={fail_rate:.3f}"
        ),
        "success_count": row.success_count,
        "avg_competency": round(avg_c, 4),
        "min_competency": round(floor_c, 4),
    }


async def _persist_durable_evidence(
    db,
    tenant_id: str,
    worker_id: str,
    owner_id: Optional[str],
    evaluation: EvaluationResult,
    goal: str = "",
    execution_job_id: Optional[str] = None,
    trust_snapshot: Optional[dict] = None,
) -> tuple[Optional[str], Optional[str]]:
    """Write EvidenceRecord + optional EvidenceChain node; return (id, hash)."""
    try:
        from core.database import EvidenceRecord, gen_id
        body = {
            "worker_id": worker_id,
            "tenant_id": tenant_id,
            "evaluation": evaluation.to_dict(),
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "execution_job_id": execution_job_id,
            "trust_snapshot": trust_snapshot,
        }
        payload = json.dumps(body, sort_keys=True, separators=(",", ":"), default=str)
        ehash = hashlib.sha256(payload.encode()).hexdigest()
        body["hash"] = ehash
        rec = EvidenceRecord(
            id=gen_id(),
            owner_id=owner_id or worker_id,
            tenant_id=tenant_id,
            goal=goal or f"worker:{worker_id}",
            body=body,
        )
        db.add(rec)
        await db.flush()
        # File-backed evidence chain for audit replay
        try:
            from governance.evidence import EvidenceChainManager
            chain = EvidenceChainManager.create(goal=goal or f"worker:{worker_id} evaluation")
            chain.add_node(
                action="agency.evaluate",
                actor_id=worker_id,
                status="success" if evaluation.success else "failed",
                metadata={"evaluation": evaluation.to_dict(), "evidence_hash": ehash},
            )
            chain.save()
            body["chain_id"] = chain.chain_id
        except Exception as e:
            logger.warning("evidence chain write skipped: %s", e)
        return rec.id, ehash
    except Exception as e:
        logger.warning("durable evidence write failed: %s", e)
        return None, None


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
    owner_id: Optional[str] = None,
    goal: str = "",
    execution_job_id: Optional[str] = None,
    trust_snapshot: Optional[dict] = None,
    **eval_kwargs,
):
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

    evidence_id, evidence_hash = await _persist_durable_evidence(
        db, tenant_id, worker_id, owner_id, evaluation, goal=goal,
        execution_job_id=execution_job_id, trust_snapshot=trust_snapshot,
    )
    evaluation.evidence_ref = evidence_id
    evaluation.evidence_hash = evidence_hash

    row = await get_or_create_trust(db, tenant_id, worker_id)
    if evaluation.success:
        row.success_count = (row.success_count or 0) + 1
    else:
        row.failure_count = (row.failure_count or 0) + 1
    if evaluation.unauthorized_attempt:
        row.unauthorized_attempts = (row.unauthorized_attempts or 0) + 1
    _update_competency(row, evaluation)
    # Stamp per-capability autonomy labels for observability / future gates
    comp = dict(row.competency or {})
    for cap, entry in list(comp.items()):
        if isinstance(entry, dict):
            entry = dict(entry)
            entry["autonomy_level"] = capability_autonomy_level(row, cap)
            comp[cap] = entry
    row.competency = comp

    # Operational recent buffer (non-authoritative); durable truth is EvidenceRecord
    hist = list((row.evidence or {}).get("recent") or [])
    hist.append(
        {
            "at": datetime.now(timezone.utc).isoformat(),
            "evidence_ref": evidence_id,
            "evidence_hash": evidence_hash,
            **evaluation.to_dict(),
        }
    )
    refs = list((row.evidence or {}).get("evidence_refs") or [])
    if evidence_id:
        refs.append(evidence_id)
    row.evidence = {
        **(row.evidence or {}),
        "recent": hist[-50:],
        "evidence_refs": refs[-200:],
    }
    _propose_promotion_if_eligible(row)
    row.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(row)
    return row


async def approve_promotion(
    db, tenant_id, worker_id, *, approved_by: str, permanent: bool = True, duration_hours: Optional[int] = None
):
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


async def reject_promotion(db, tenant_id, worker_id, *, rejected_by: str):
    row = await get_or_create_trust(db, tenant_id, worker_id)
    row.pending_promotion = None
    row.evidence = {
        **(row.evidence or {}),
        "last_rejection": {"by": rejected_by, "at": datetime.now(timezone.utc).isoformat()},
    }
    row.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(row)
    return row


async def demote_worker(db, tenant_id, worker_id, *, demoted_by: str, reason: str = "human_demotion", autonomy: str = "supervised"):
    row = await get_or_create_trust(db, tenant_id, worker_id)
    row.autonomy = autonomy
    row.trust_level = "supervised" if autonomy == "supervised" else row.trust_level
    row.pending_promotion = None
    row.promotion_expires_at = None
    row.evidence = {
        **(row.evidence or {}),
        "last_demotion": {"by": demoted_by, "at": datetime.now(timezone.utc).isoformat(), "reason": reason},
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
    if _effective_autonomy(row) == AutonomyProfile.SUPERVISED.value:
        return TrustLevel.ASSISTANT
    return mapping.get((row.trust_level or "").lower(), TrustLevel.OPERATOR)


def _cap_earned(entry: dict, *, high_risk: bool = False, bounded: bool = False) -> bool:
    score = float(entry.get("competency", 0))
    samples = int(entry.get("samples", 0))
    if high_risk:
        return samples >= HIGH_RISK_MIN_SAMPLES and score >= HIGH_RISK_THRESHOLD
    if bounded:
        return samples >= BOUNDED_MIN_SAMPLES and score >= BOUNDED_THRESHOLD
    return samples >= CAP_AUTONOMY_MIN_SAMPLES and score >= CAP_AUTONOMY_THRESHOLD


def filter_autonomous_caps(caps: set[str], row=None) -> set[str]:
    """Authority filter — fail safe + per-capability autonomy.

    SUPERVISED worker: only SUPERVISED_CAPS.
    Otherwise each capability is gated by capability_autonomy_level():
      SUPERVISED cap → only if in SUPERVISED_CAPS
      BOUNDED cap → earned bounded threshold
      AUTONOMOUS+ → earned autonomous threshold
    ALWAYS_HUMAN_GATED never included.
    """
    if row is None:
        return set(caps) & SUPERVISED_CAPS

    out: set[str] = set()
    for c in caps:
        if c in ALWAYS_HUMAN_GATED:
            continue
        level = capability_autonomy_level(row, c)
        if level == AutonomyProfile.SUPERVISED.value:
            if c in SUPERVISED_CAPS:
                out.add(c)
        elif level == AutonomyProfile.BOUNDED.value:
            entry = (row.competency or {}).get(c) if isinstance(row.competency, dict) else {}
            if not isinstance(entry, dict):
                entry = {}
            if c in SUPERVISED_CAPS or _cap_earned(entry, high_risk=c in HIGH_RISK_CAPS, bounded=True):
                out.add(c)
        else:
            # AUTONOMOUS / FULL for this capability
            entry = (row.competency or {}).get(c) if isinstance(row.competency, dict) else {}
            if not isinstance(entry, dict):
                entry = {}
            if _cap_earned(entry, high_risk=c in HIGH_RISK_CAPS, bounded=False):
                out.add(c)
            elif c in SUPERVISED_CAPS:
                out.add(c)
    return out


def autonomy_allows_without_hitl(row, capability: str) -> bool:
    if capability in ALWAYS_HUMAN_GATED:
        return False
    auto = _effective_autonomy(row)
    if auto == AutonomyProfile.SUPERVISED.value:
        return False
    entry = (row.competency or {}).get(capability) or {}
    high = capability in HIGH_RISK_CAPS
    if auto == AutonomyProfile.BOUNDED.value:
        return _cap_earned(entry, high_risk=high, bounded=True)
    return _cap_earned(entry, high_risk=high, bounded=False)
