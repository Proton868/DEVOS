"""
Saga lifecycle coordination — not orchestration authority.
Mission Engine decides what/when; Saga records what succeeded and compensation.
"""
from __future__ import annotations

import json
import threading
import time
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Callable, Optional

from execution.saga_compensation import CompensationPolicy, CompensationMode, CompensationOutcome, policy_for, evaluate_conditions
from execution.compensation_ucip import authorize_compensation

_LOCK = threading.Lock()

SAGA_STATUSES = (
    "PENDING", "RUNNING", "COMPLETED", "COMPENSATING", "COMPENSATED",
    "PARTIALLY_COMPENSATED", "FAILED", "CANCELLED", "MANUAL_REMEDIATION",
)
STEP_STATUSES = (
    "PENDING", "RUNNING", "COMPLETED", "FAILED", "COMPENSATING",
    "COMPENSATED", "SKIPPED", "MANUAL_REMEDIATION",
)



def init_saga_db() -> None:
    """Tables via SQLAlchemy metadata / migrations."""
    return


def _backend() -> str:
    from core.sync_session import store_backend
    return store_backend()


def classify_step_phase(action: str, *, pivot_seen: bool = False) -> str:
    a = (action or "").lower()
    if a in _PIVOT_ACTIONS:
        return SAGA_PHASE_PIVOT
    if pivot_seen:
        return SAGA_PHASE_POST_PIVOT
    return SAGA_PHASE_COMPENSABLE



@dataclass
class SagaStep:
    step_id: str
    saga_id: str
    node_id: str
    action: str
    status: str = "PENDING"
    compensation_policy: Optional[dict] = None
    evidence_id: Optional[str] = None
    trace_id: Optional[str] = None
    span_id: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    error: Optional[str] = None
    meta: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["meta"] = self.meta
        return d


@dataclass
class Saga:
    saga_id: str
    plan_id: Optional[str] = None
    mission_id: Optional[str] = None
    status: str = "PENDING"
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    failure: Optional[str] = None
    trace_id: Optional[str] = None
    pivot_reached: bool = False
    pivot_step_id: Optional[str] = None
    pivot_action: Optional[str] = None
    pivot_at: Optional[float] = None
    steps: list[SagaStep] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "saga_id": self.saga_id,
            "plan_id": self.plan_id,
            "mission_id": self.mission_id,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "failure": self.failure,
            "trace_id": self.trace_id,
            "pivot_reached": self.pivot_reached,
            "pivot_step_id": self.pivot_step_id,
            "pivot_action": self.pivot_action,
            "pivot_at": self.pivot_at,
            "steps": [s.to_dict() for s in self.steps],
        }



def _save_saga_row(s: Saga) -> None:
    from core.sync_session import get_sync_session
    from core.database import SagaRecord, utcnow_naive

    with get_sync_session() as db:
        row = db.get(SagaRecord, s.saga_id)
        meta = {
            "trace_id": s.trace_id,
            "pivot_reached": s.pivot_reached,
            "pivot_step_id": s.pivot_step_id,
            "pivot_action": s.pivot_action,
            "pivot_at": s.pivot_at,
            "started_at": s.started_at,
            "completed_at": s.completed_at,
            "created_at": s.created_at,
        }
        if row is None:
            db.add(
                SagaRecord(
                    id=s.saga_id,
                    plan_id=s.plan_id,
                    mission_id=s.mission_id,
                    status=s.status,
                    failure=s.failure,
                    meta=meta,
                    created_at=utcnow_naive(),
                    updated_at=utcnow_naive(),
                )
            )
        else:
            row.plan_id = s.plan_id
            row.mission_id = s.mission_id
            row.status = s.status
            row.failure = s.failure
            row.meta = meta
            row.updated_at = utcnow_naive()
        db.commit()


def _save_step_row(st: SagaStep) -> None:
    from core.sync_session import get_sync_session
    from core.database import SagaStepRecord, utcnow_naive

    with get_sync_session() as db:
        row = db.get(SagaStepRecord, st.step_id)
        meta = dict(st.meta or {})
        meta["compensation_policy"] = st.compensation_policy
        meta["trace_id"] = st.trace_id
        meta["span_id"] = st.span_id
        meta["started_at"] = st.started_at
        meta["completed_at"] = st.completed_at
        meta["created_at"] = st.created_at
        if row is None:
            db.add(
                SagaStepRecord(
                    id=st.step_id,
                    saga_id=st.saga_id,
                    node_id=st.node_id,
                    action=st.action,
                    status=st.status,
                    phase=None,
                    attempts=0,
                    error=st.error,
                    evidence_id=st.evidence_id,
                    meta=meta,
                    created_at=utcnow_naive(),
                    updated_at=utcnow_naive(),
                )
            )
        else:
            row.status = st.status
            row.error = st.error
            row.evidence_id = st.evidence_id
            row.meta = meta
            row.updated_at = utcnow_naive()
        db.commit()



def load_saga(saga_id: str) -> Optional[Saga]:
    from core.sync_session import get_sync_session
    from core.database import SagaRecord, SagaStepRecord
    from sqlalchemy import select

    with get_sync_session() as db:
        row = db.get(SagaRecord, saga_id)
        if not row:
            return None
        meta = row.meta or {}
        steps_rows = db.execute(
            select(SagaStepRecord).where(SagaStepRecord.saga_id == saga_id)
        ).scalars().all()
        steps = []
        for st in steps_rows:
            sm = st.meta or {}
            steps.append(
                SagaStep(
                    step_id=st.id,
                    saga_id=st.saga_id,
                    node_id=st.node_id or "",
                    action=st.action or "",
                    status=st.status,
                    compensation_policy=sm.get("compensation_policy"),
                    evidence_id=st.evidence_id,
                    trace_id=sm.get("trace_id"),
                    span_id=sm.get("span_id"),
                    created_at=float(sm.get("created_at") or 0),
                    started_at=sm.get("started_at"),
                    completed_at=sm.get("completed_at"),
                    error=st.error,
                    meta={k: v for k, v in sm.items() if k not in ("compensation_policy", "trace_id", "span_id", "started_at", "completed_at", "created_at")},
                )
            )
        return Saga(
            saga_id=row.id,
            plan_id=row.plan_id,
            mission_id=row.mission_id,
            status=row.status,
            created_at=float(meta.get("created_at") or 0),
            failure=row.failure,
            trace_id=meta.get("trace_id"),
            pivot_reached=bool(meta.get("pivot_reached")),
            pivot_step_id=meta.get("pivot_step_id"),
            pivot_action=meta.get("pivot_action"),
            pivot_at=meta.get("pivot_at"),
            started_at=meta.get("started_at"),
            completed_at=meta.get("completed_at"),
            steps=steps,
        )


def create_saga(*, plan_id: Optional[str] = None, mission_id: Optional[str] = None,
                trace_id: Optional[str] = None) -> Saga:
    s = Saga(
        saga_id=uuid.uuid4().hex,
        plan_id=plan_id,
        mission_id=mission_id,
        status="PENDING",
        trace_id=trace_id,
    )
    _save_saga_row(s)
    return s


def load_saga(saga_id: str) -> Optional[Saga]:
    with _LOCK:
        c = _conn()
        try:
            row = c.execute("SELECT * FROM sagas WHERE saga_id=?", (saga_id,)).fetchone()
            if not row:
                return None
            steps = c.execute(
                "SELECT * FROM saga_steps WHERE saga_id=? ORDER BY created_at ASC", (saga_id,)
            ).fetchall()
        finally:
            c.close()
    s = Saga(
        saga_id=row["saga_id"], plan_id=row["plan_id"], mission_id=row["mission_id"],
        status=row["status"], created_at=row["created_at"], updated_at=row["updated_at"],
        started_at=row["started_at"], completed_at=row["completed_at"],
        failure=row["failure"], trace_id=row["trace_id"],
        pivot_reached=bool(row["pivot_reached"] if "pivot_reached" in row.keys() else 0),
        pivot_step_id=row["pivot_step_id"] if "pivot_step_id" in row.keys() else None,
        pivot_action=row["pivot_action"] if "pivot_action" in row.keys() else None,
        pivot_at=row["pivot_at"] if "pivot_at" in row.keys() else None,
    )
    for r in steps:
        s.steps.append(SagaStep(
            step_id=r["step_id"], saga_id=r["saga_id"], node_id=r["node_id"] or "",
            action=r["action"] or "", status=r["status"],
            compensation_policy=json.loads(r["compensation_policy"] or "{}"),
            evidence_id=r["evidence_id"], trace_id=r["trace_id"], span_id=r["span_id"],
            created_at=r["created_at"], updated_at=r["updated_at"],
            started_at=r["started_at"], completed_at=r["completed_at"],
            error=r["error"], meta=json.loads(r["meta_json"] or "{}"),
        ))
    return s


def begin_step(saga: Saga, *, node_id: str, action: str, trace_id: Optional[str] = None,
               span_id: Optional[str] = None, meta: Optional[dict] = None) -> SagaStep:
    if saga.status == "PENDING":
        saga.status = "RUNNING"
        saga.started_at = time.time()
        _save_saga_row(saga)
    pol = policy_for(action)
    pivot_seen = any(
        (s.meta or {}).get('phase') == SAGA_PHASE_PIVOT or s.action.lower() in _PIVOT_ACTIONS
        for s in saga.steps if s.status == 'COMPLETED'
    )
    phase = classify_step_phase(action, pivot_seen=pivot_seen)
    step = SagaStep(
        step_id=uuid.uuid4().hex,
        saga_id=saga.saga_id,
        node_id=node_id,
        action=action,
        status="RUNNING",
        compensation_policy=pol.to_dict(),
        trace_id=trace_id or saga.trace_id,
        span_id=span_id,
        started_at=time.time(),
        meta={**(meta or {}), 'phase': phase, 'resource': (meta or {}).get('resource')},
    )
    saga.steps.append(step)
    _save_step_row(step)
    return step




def record_pivot(saga: Saga, step: SagaStep) -> None:
    """Durably mark pivot reached — crash-safe; resume must not forget external success."""
    if saga.pivot_reached:
        return
    saga.pivot_reached = True
    saga.pivot_step_id = step.step_id
    saga.pivot_action = step.action
    saga.pivot_at = time.time()
    _save_saga_row(saga)
    try:
        from execution.outbox import enqueue
        enqueue(
            "saga.pivoted",
            aggregate_type="saga",
            aggregate_id=saga.saga_id,
            payload={
                "plan_id": saga.plan_id,
                "pivot_step_id": step.step_id,
                "pivot_action": step.action,
            },
            trace_id=saga.trace_id,
            idempotency_key=f"saga.pivoted:{saga.saga_id}:{step.step_id}",
        )
    except Exception:
        pass


def complete_step(step: SagaStep, *, evidence_id: Optional[str] = None, meta: Optional[dict] = None) -> None:
    step.status = "COMPLETED"
    step.completed_at = time.time()
    step.updated_at = time.time()
    if evidence_id:
        step.evidence_id = evidence_id
    if meta:
        step.meta.update(meta)
    _save_step_row(step)
    phase = (step.meta or {}).get("phase") or classify_step_phase(step.action)
    if phase == SAGA_PHASE_PIVOT:
        # load saga to update pivot — step has saga_id
        s = load_saga(step.saga_id)
        if s:
            record_pivot(s, step)
    try:
        from execution.outbox import enqueue
        enqueue("saga.step_completed", aggregate_type="saga", aggregate_id=step.saga_id,
                payload={"step_id": step.step_id, "action": step.action, "status": "COMPLETED"},
                trace_id=step.trace_id,
                idempotency_key=f"saga.step:{step.step_id}:completed")
    except Exception:
        pass


def fail_step(step: SagaStep, error: str, *, evidence_id: Optional[str] = None) -> None:
    step.status = "FAILED"
    step.error = (error or "")[:500]
    step.completed_at = time.time()
    step.updated_at = time.time()
    if evidence_id:
        step.evidence_id = evidence_id
    _save_step_row(step)


def complete_saga(saga: Saga) -> None:
    saga.status = "COMPLETED"
    saga.completed_at = time.time()
    saga.updated_at = time.time()
    _save_saga_row(saga)


def fail_saga(saga: Saga, failure: str) -> None:
    saga.status = "FAILED"
    saga.failure = failure[:500]
    saga.completed_at = time.time()
    saga.updated_at = time.time()
    _save_saga_row(saga)


# Compensation handlers (idempotent)
_COMPENSATED_OPS: set[str] = set()


async def _run_compensation_action(action: str, meta: dict, *, user_id: str = "", project_id: str = "") -> dict:
    op_key = f"{action}:{meta.get('resource_id') or meta.get('runtime_id') or meta.get('share_id') or meta.get('tunnel_id')}"
    if op_key in _COMPENSATED_OPS:
        return {"status": "already_compensated", "action": action}
    result = {"status": "ok", "action": action}
    if action == "STOP_RUNTIME":
        from execution.app_runtime import get_runtime
        rt = get_runtime(user_id, project_id) if user_id and project_id else None
        if rt:
            await rt.stop()
        result["detail"] = "runtime_stopped"
    elif action == "REVOKE_SHARE":
        sid = meta.get("share_id")
        if sid and user_id:
            from execution.shares import revoke_share
            revoke_share(sid, user_id)
        result["detail"] = "share_revoked"
    elif action == "STOP_TUNNEL":
        tid = meta.get("tunnel_id")
        if tid:
            from execution.cloudflare_tunnel_mgr import stop_tunnel
            await stop_tunnel(tid)
        result["detail"] = "tunnel_stopped"
    elif action in ("REVOKE_PREVIEW",):
        result["detail"] = "preview_revoked_noop"
    else:
        result = {"status": "manual", "action": action, "detail": "requires manual remediation"}
    _COMPENSATED_OPS.add(op_key)
    return result


async def compensate_saga(
    saga: Saga,
    *,
    user_id: str = "",
    project_id: str = "",
    only_automatic: bool = True,
) -> dict:
    """
    Compensate completed steps in reverse order.
    AUTOMATIC always; CONDITIONAL/MANUAL marked MANUAL_REMEDIATION unless only_automatic=False
    and caller has authorized (still no direct provider mutation for MANUAL destructive ops).
    """
        # Reload durable pivot/state so in-memory object cannot erase pivot after crash-safe record
    durable = load_saga(saga.saga_id)
    if durable:
        saga.pivot_reached = durable.pivot_reached
        saga.pivot_step_id = durable.pivot_step_id
        saga.pivot_action = durable.pivot_action
        saga.pivot_at = durable.pivot_at
        if durable.steps and len(durable.steps) >= len(saga.steps):
            saga.steps = durable.steps

    if saga.status in ("COMPENSATED", "CANCELLED") and all(
        s.status in ("COMPENSATED", "SKIPPED", "PENDING", "FAILED") for s in saga.steps
    ):
        return {"status": saga.status, "idempotent": True, "results": []}

    saga.status = "COMPENSATING"
    _save_saga_row(saga)

    results = []
    completed = [s for s in saga.steps if s.status == "COMPLETED"]
    for step in reversed(completed):
        pol = step.compensation_policy or policy_for(step.action).to_dict()
        # normalize mode from formal policy (enum value or legacy uppercase)
        mode_raw = (pol.get("mode") or "manual")
        mode = str(mode_raw).lower()
        action = pol.get("action")
        resource = (step.meta or {}).get("resource") or step.meta or {}
        # reconstruct policy for evaluation
        try:
            formal = policy_for(step.action)
        except Exception:
            formal = None
        if mode in ("none",):
            step.status = "SKIPPED"
            _save_step_row(step)
            results.append({"step_id": step.step_id, "status": "SKIPPED", "outcome": CompensationOutcome.SKIPPED.value})
            continue
        if mode == "automatic" and action:
            auth = authorize_compensation(
                forward_action=step.action, resource=resource, user_id=user_id,
                context={"saga_id": saga.saga_id, "plan_id": saga.plan_id, "trace_id": saga.trace_id},
            )
            if not auth.get("allowed"):
                step.status = "MANUAL_REMEDIATION" if auth.get("outcome") != CompensationOutcome.SKIPPED.value else "SKIPPED"
                _save_step_row(step)
                results.append({"step_id": step.step_id, "status": step.status, "auth": auth})
                continue
            step.status = "COMPENSATING"
            _save_step_row(step)
            try:
                from observability.tracing import start_span
                with start_span("saga.compensation", kind="compensation", attributes={
                    "saga_id": saga.saga_id, "step_id": step.step_id, "action": action,
                }):
                    r = await _run_compensation_action(
                        action, step.meta, user_id=user_id, project_id=project_id,
                    )
                if r.get("status") in ("ok", "already_compensated"):
                    step.status = "COMPENSATED"
                    outcome = CompensationOutcome.ALREADY_COMPENSATED.value if r.get("status") == "already_compensated" else CompensationOutcome.COMPENSATED.value
                else:
                    step.status = "MANUAL_REMEDIATION"
                    outcome = CompensationOutcome.MANUAL_REMEDIATION.value
                results.append({"step_id": step.step_id, "result": r, "status": step.status, "outcome": outcome})
            except Exception as e:
                step.status = "MANUAL_REMEDIATION"
                step.error = str(e)[:300]
                results.append({"step_id": step.step_id, "error": str(e)[:200], "outcome": CompensationOutcome.FAILED.value})
            _save_step_row(step)
        elif mode == "conditional" and action and formal:
            allowed, why = evaluate_conditions(formal, resource)
            if allowed and not formal.requires_hitl:
                step.status = "COMPENSATING"
                _save_step_row(step)
                try:
                    r = await _run_compensation_action(action, step.meta, user_id=user_id, project_id=project_id)
                    step.status = "COMPENSATED" if r.get("status") in ("ok", "already_compensated") else "MANUAL_REMEDIATION"
                    results.append({"step_id": step.step_id, "result": r, "status": step.status, "outcome": step.status.lower()})
                except Exception as e:
                    step.status = "MANUAL_REMEDIATION"
                    step.error = str(e)[:300]
                    results.append({"step_id": step.step_id, "outcome": CompensationOutcome.FAILED.value})
                _save_step_row(step)
            else:
                step.status = "MANUAL_REMEDIATION"
                _save_step_row(step)
                results.append({"step_id": step.step_id, "status": "MANUAL_REMEDIATION", "outcome": CompensationOutcome.DENIED.value, "reason": why, "policy": pol})
        elif mode in ("conditional", "manual"):
            step.status = "MANUAL_REMEDIATION"
            _save_step_row(step)
            results.append({"step_id": step.step_id, "status": "MANUAL_REMEDIATION", "outcome": CompensationOutcome.MANUAL_REMEDIATION.value, "policy": pol})
        else:
            step.status = "SKIPPED"
            _save_step_row(step)
            results.append({"step_id": step.step_id, "status": "SKIPPED", "outcome": CompensationOutcome.SKIPPED.value})

    statuses = [s.status for s in saga.steps if s.status == "COMPLETED" or s.status in (
        "COMPENSATED", "MANUAL_REMEDIATION", "SKIPPED")]
    if any(s.status == "MANUAL_REMEDIATION" for s in saga.steps):
        saga.status = "PARTIALLY_COMPENSATED" if any(s.status == "COMPENSATED" for s in saga.steps) else "MANUAL_REMEDIATION"
    elif all(s.status in ("COMPENSATED", "SKIPPED", "FAILED", "PENDING") for s in saga.steps):
        saga.status = "COMPENSATED"
    else:
        saga.status = "PARTIALLY_COMPENSATED"
    saga.completed_at = time.time()
    _save_saga_row(saga)
    return {"status": saga.status, "results": results}
