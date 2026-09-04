"""
Saga lifecycle coordination — not orchestration authority.
Mission Engine decides what/when; Saga records what succeeded and compensation.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Callable, Optional

from execution.saga_compensation import CompensationPolicy, policy_for

_LOCK = threading.Lock()
_DB = Path("data/saga.sqlite3")

SAGA_STATUSES = (
    "PENDING", "RUNNING", "COMPLETED", "COMPENSATING", "COMPENSATED",
    "PARTIALLY_COMPENSATED", "FAILED", "CANCELLED", "MANUAL_REMEDIATION",
)
STEP_STATUSES = (
    "PENDING", "RUNNING", "COMPLETED", "FAILED", "COMPENSATING",
    "COMPENSATED", "SKIPPED", "MANUAL_REMEDIATION",
)


def _conn() -> sqlite3.Connection:
    _DB.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(_DB), check_same_thread=False)
    c.row_factory = sqlite3.Row
    return c


def init_saga_db() -> None:
    with _LOCK:
        c = _conn()
        try:
            c.executescript(
                """
                CREATE TABLE IF NOT EXISTS sagas (
                  saga_id TEXT PRIMARY KEY,
                  plan_id TEXT,
                  mission_id TEXT,
                  status TEXT,
                  created_at REAL,
                  updated_at REAL,
                  started_at REAL,
                  completed_at REAL,
                  failure TEXT,
                  trace_id TEXT
                );
                CREATE TABLE IF NOT EXISTS saga_steps (
                  step_id TEXT PRIMARY KEY,
                  saga_id TEXT NOT NULL,
                  node_id TEXT,
                  action TEXT,
                  status TEXT,
                  compensation_policy TEXT,
                  evidence_id TEXT,
                  trace_id TEXT,
                  span_id TEXT,
                  created_at REAL,
                  updated_at REAL,
                  started_at REAL,
                  completed_at REAL,
                  error TEXT,
                  meta_json TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_steps_saga ON saga_steps(saga_id);
                """
            )
            c.commit()
        finally:
            c.close()


init_saga_db()


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
            "steps": [s.to_dict() for s in self.steps],
        }


def _save_saga_row(s: Saga) -> None:
    with _LOCK:
        c = _conn()
        try:
            c.execute(
                """INSERT OR REPLACE INTO sagas
                   (saga_id,plan_id,mission_id,status,created_at,updated_at,started_at,completed_at,failure,trace_id)
                   VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (s.saga_id, s.plan_id, s.mission_id, s.status, s.created_at, time.time(),
                 s.started_at, s.completed_at, s.failure, s.trace_id),
            )
            c.commit()
        finally:
            c.close()


def _save_step_row(st: SagaStep) -> None:
    with _LOCK:
        c = _conn()
        try:
            c.execute(
                """INSERT OR REPLACE INTO saga_steps
                   (step_id,saga_id,node_id,action,status,compensation_policy,evidence_id,trace_id,span_id,
                    created_at,updated_at,started_at,completed_at,error,meta_json)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    st.step_id, st.saga_id, st.node_id, st.action, st.status,
                    json.dumps(st.compensation_policy or {}), st.evidence_id, st.trace_id, st.span_id,
                    st.created_at, time.time(), st.started_at, st.completed_at, st.error,
                    json.dumps(st.meta or {}),
                ),
            )
            c.commit()
        finally:
            c.close()


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
        meta=meta or {},
    )
    saga.steps.append(step)
    _save_step_row(step)
    return step


def complete_step(step: SagaStep, *, evidence_id: Optional[str] = None, meta: Optional[dict] = None) -> None:
    step.status = "COMPLETED"
    step.completed_at = time.time()
    step.updated_at = time.time()
    if evidence_id:
        step.evidence_id = evidence_id
    if meta:
        step.meta.update(meta)
    _save_step_row(step)


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
        mode = pol.get("mode", "MANUAL")
        action = pol.get("action")
        if mode == "NONE":
            step.status = "SKIPPED"
            _save_step_row(step)
            results.append({"step_id": step.step_id, "status": "SKIPPED"})
            continue
        if mode == "AUTOMATIC" and action:
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
                step.status = "COMPENSATED" if r.get("status") in ("ok", "already_compensated") else "MANUAL_REMEDIATION"
                results.append({"step_id": step.step_id, "result": r, "status": step.status})
            except Exception as e:
                step.status = "MANUAL_REMEDIATION"
                step.error = str(e)[:300]
                results.append({"step_id": step.step_id, "error": str(e)[:200]})
            _save_step_row(step)
        elif mode in ("CONDITIONAL", "MANUAL"):
            step.status = "MANUAL_REMEDIATION"
            _save_step_row(step)
            results.append({"step_id": step.step_id, "status": "MANUAL_REMEDIATION", "policy": pol})
        else:
            step.status = "SKIPPED"
            _save_step_row(step)
            results.append({"step_id": step.step_id, "status": "SKIPPED"})

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
