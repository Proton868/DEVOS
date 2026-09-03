"""
Workflow orchestration runtime — hardened.

Immutable INPUT:  payload.workflow_snapshot  (WorkflowExecutionSnapshot schema_version=1)
Mutable RUNTIME:  payload.execution_state    (step progress; never written into the snapshot)

Recovery and retries MUST use the snapshot + execution_state from the job payload.
Never reload the live WorkflowRecord during execution.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Optional

from brain.workflow import Workflow, WorkflowStep, StepType
from brain.workflow_store import (
    SnapshotError,
    load_snapshot_for_job,
    validate_execution_snapshot,
    SNAPSHOT_SCHEMA_VERSION,
)
from governance.reliability import scrub_secrets

logger = logging.getLogger("devos.workflow_executor")

# Resource bounds (worker-local; not a durable timer service)
MAX_STEPS = 100
MAX_WAIT_S = 30.0
MAX_OUTPUT_CHARS = 4000
MAX_CONTEXT_KEYS = 200

# Step-level states
STEP_PENDING = "pending"
STEP_RUNNING = "running"
STEP_SUCCEEDED = "succeeded"
STEP_FAILED = "failed"
STEP_SKIPPED = "skipped"
STEP_BLOCKED = "blocked"
STEP_WAITING = "waiting"
STEP_DENIED = "denied"
STEP_UNKNOWN = "unknown"
STEP_PENDING_APPROVAL = "pending_approval"

# Terminal job outcomes we return to JobWorker
JOB_SUCCEEDED = "succeeded"
JOB_FAILED = "failed"

# Permanent failures must not be treated as transient by callers
PERMANENT_ERRORS = {
    "VALIDATION_ERROR",
    "GOVERNANCE_DENIED",
    "INPUT_ERROR",
    "CANCELLED",
}


@dataclass
class StepRecord:
    step_id: str
    type: str
    status: str = STEP_PENDING
    attempt: int = 0
    message: str = ""
    outputs: dict = field(default_factory=dict)
    side_effect: str = "none"  # none | local | external | unknown
    error: Optional[str] = None
    error_code: Optional[str] = None
    duration_ms: int = 0
    started_at: Optional[str] = None
    finished_at: Optional[str] = None

    def to_dict(self) -> dict:
        return scrub_secrets({
            "step_id": self.step_id,
            "type": self.type,
            "status": self.status,
            "attempt": self.attempt,
            "message": (self.message or "")[:MAX_OUTPUT_CHARS],
            "outputs": self.outputs,
            "side_effect": self.side_effect,
            "error": (self.error or "")[:1000] if self.error else None,
            "error_code": self.error_code,
            "duration_ms": self.duration_ms,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        })


@dataclass
class ExecutionState:
    """Mutable runtime progress — separate from the immutable snapshot."""
    schema_version: int = 1
    current_step_id: Optional[str] = None
    completed: list[str] = field(default_factory=list)
    records: dict[str, dict] = field(default_factory=dict)  # step_id -> StepRecord dict
    context: dict = field(default_factory=dict)
    terminal_status: Optional[str] = None
    error: Optional[str] = None
    error_code: Optional[str] = None

    def to_dict(self) -> dict:
        return scrub_secrets({
            "schema_version": self.schema_version,
            "current_step_id": self.current_step_id,
            "completed": list(self.completed),
            "records": self.records,
            "context": self.context,
            "terminal_status": self.terminal_status,
            "error": self.error,
            "error_code": self.error_code,
        })

    @classmethod
    def from_dict(cls, data: Optional[dict]) -> "ExecutionState":
        if not data or not isinstance(data, dict):
            return cls()
        return cls(
            schema_version=int(data.get("schema_version") or 1),
            current_step_id=data.get("current_step_id"),
            completed=list(data.get("completed") or []),
            records=dict(data.get("records") or {}),
            context=dict(data.get("context") or {}),
            terminal_status=data.get("terminal_status"),
            error=data.get("error"),
            error_code=data.get("error_code"),
        )


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _workflow_from_snapshot(snap: dict) -> Workflow:
    definition = dict(snap.get("definition") or {})
    definition.setdefault("workflow_id", snap["workflow_id"])
    definition.setdefault("name", snap.get("name") or definition.get("name") or "workflow")
    return Workflow.from_dict(definition)


def _step_map(wf: Workflow) -> dict[str, WorkflowStep]:
    return {s.id: s for s in wf.steps}


def _bound_outputs(outputs: dict) -> dict:
    out = scrub_secrets(outputs or {})
    # Bound size
    text = str(out)
    if len(text) > MAX_OUTPUT_CHARS:
        return {"_truncated": True, "preview": text[:MAX_OUTPUT_CHARS]}
    return out


def _eval_condition(expr: Optional[str], context: dict) -> tuple[bool, Optional[str]]:
    """Fail-closed condition evaluator. No eval/exec/imports/calls."""
    if not expr or not str(expr).strip():
        return True, None
    e = str(expr).strip()
    # Reject anything that looks like code execution
    banned = ("__", "import", "exec", "eval", "open(", "os.", "sys.", "subprocess",
              "lambda", ";", "\n", "`", "$(", "${")
    low = e.lower()
    for b in banned:
        if b in low:
            return False, "INPUT_ERROR"
    if re.search(r"[A-Za-z_][\w]*\s*\(", e):
        return False, "INPUT_ERROR"  # function calls

    if low in ("true", "1", "yes"):
        return True, None
    if low in ("false", "0", "no"):
        return False, None

    m = re.match(r"^([A-Za-z_][\w.]*)\s*(==|!=)\s*(.+)$", e)
    if m:
        key, op, raw = m.group(1), m.group(2), m.group(3).strip().strip("'\"")
        if any(x in key for x in ("__",)):
            return False, "INPUT_ERROR"
        cur: Any = context
        for part in key.split("."):
            if not isinstance(cur, dict) or part not in cur:
                cur = None
                break
            cur = cur[part]
        if op == "==":
            return str(cur) == raw, None
        return str(cur) != raw, None

    # bare key truthiness
    if re.match(r"^[A-Za-z_][\w.]*$", e):
        cur = context
        for part in e.split("."):
            if not isinstance(cur, dict) or part not in cur:
                return False, None
            cur = cur[part]
        return bool(cur), None

    return False, "INPUT_ERROR"


async def _ucip_gate(capability: str, inputs: dict, context: dict) -> tuple[str, str]:
    try:
        from governance.ucip import UCIPGateway, TrustLevel, AgentIdentity, ACTION_TO_CAP
        import json
        identity = AgentIdentity.create(
            user_id=str(context.get("owner_id") or "workflow"),
            session_id=str(context.get("correlation_id") or "wf"),
            trust_level=TrustLevel.OPERATOR,
        )
        gw = UCIPGateway(identity)
        inv = {v: k for k, v in ACTION_TO_CAP.items()}
        action = inv.get(capability, capability.replace("ucip:", "").replace(".", "_"))
        action_input = json.dumps(inputs or {}, default=str)[:4000]
        resp = gw.request(action, action_input, context={"source": "workflow_executor"})
        decision = getattr(resp, "decision", None)
        if decision is None and hasattr(resp, "approved"):
            decision = "ALLOW" if resp.approved() else (
                "ESCALATE_TO_HUMAN" if getattr(resp, "needs_human", lambda: False)() else "DENY"
            )
        reason = getattr(resp, "reason", "") or getattr(resp, "message", "") or ""
        return str(decision or "DENY"), str(reason)
    except Exception as e:
        logger.warning("UCIP gate unavailable: %s", e)
        return "DENY", f"UCIP unavailable: {e}"


def _require_authority_safe(capability: str, context: dict) -> tuple[bool, str]:
    try:
        from governance.execution_authority import require_authority
        from governance.execution_pipeline import PathClass
        require_authority(
            path_class=PathClass.DURABLE,
            actor_id=str(context.get("owner_id") or "workflow"),
            tenant_id=context.get("tenant_id"),
            capability=capability,
            reason="workflow_step",
        )
        return True, "ok"
    except Exception as e:
        msg = str(e)
        if "denied" in msg.lower() or "authority" in msg.lower():
            return False, msg
        return True, f"require_authority noted: {msg}"


async def _run_capability_step(step: WorkflowStep, context: dict, attempt: int) -> StepRecord:
    """Capability steps: UCIP + authority first. Code path only if isolation is real."""
    t0 = time.monotonic()
    rec = StepRecord(
        step_id=step.id, type=step.type.value, status=STEP_RUNNING,
        attempt=attempt, started_at=_now_iso(),
    )
    cap = step.capability or ""
    if not cap:
        rec.status = STEP_FAILED
        rec.error = "CAPABILITY step missing capability slug"
        rec.error_code = "VALIDATION_ERROR"
        rec.finished_at = _now_iso()
        rec.duration_ms = int((time.monotonic() - t0) * 1000)
        return rec

    decision, reason = await _ucip_gate(cap, step.inputs, context)
    if decision == "DENY":
        rec.status = STEP_DENIED
        rec.error = f"UCIP DENY: {reason}"
        rec.error_code = "GOVERNANCE_DENIED"
        rec.message = reason
        rec.finished_at = _now_iso()
        rec.duration_ms = int((time.monotonic() - t0) * 1000)
        return rec
    if decision == "ESCALATE_TO_HUMAN":
        rec.status = STEP_PENDING_APPROVAL
        rec.error = "Human approval required"
        rec.error_code = "GOVERNANCE_DENIED"
        rec.message = reason
        rec.finished_at = _now_iso()
        rec.duration_ms = int((time.monotonic() - t0) * 1000)
        return rec

    ok, auth_msg = _require_authority_safe(cap, context)
    if not ok:
        rec.status = STEP_DENIED
        rec.error = auth_msg
        rec.error_code = "GOVERNANCE_DENIED"
        rec.finished_at = _now_iso()
        rec.duration_ms = int((time.monotonic() - t0) * 1000)
        return rec

    code = (step.inputs or {}).get("code") or (step.inputs or {}).get("source")
    lang = (step.inputs or {}).get("language") or (
        "python" if "python" in cap else "bash" if "bash" in cap else None
    )

    # SECURITY: only execute code when a real isolation backend is available.
    # If isolation is unavailable, do NOT fall back to host execution.
    if code and lang in ("python", "bash", "node"):
        try:
            from governance.sandbox import SandboxedExecutor
            exe = SandboxedExecutor(allow_network=False)
            result = await exe.run(str(code), language=lang)
            status_s = getattr(result, "status", "") or ""
            if status_s == "sandbox_denied" or "isolation" in status_s:
                rec.status = STEP_FAILED
                rec.error = "Sandbox/isolation denied or unavailable — host execution disabled"
                rec.error_code = "CAPABILITY_ERROR"
                rec.side_effect = "none"
                rec.message = (getattr(result, "stderr", "") or "")[:500]
            elif status_s == "timeout":
                # Timeout after possible start → UNKNOWN if external, local code → failed
                rec.status = STEP_UNKNOWN
                rec.error = "Sandbox timeout; local effect may be incomplete"
                rec.error_code = "TIMEOUT"
                rec.side_effect = "unknown"
            else:
                exit_code = getattr(result, "exit_code", 1)
                rec.side_effect = "local"
                if exit_code == 0:
                    rec.status = STEP_SUCCEEDED
                    rec.message = (result.to_brain_summary() if hasattr(result, "to_brain_summary") else "")[:MAX_OUTPUT_CHARS]
                    rec.outputs = _bound_outputs({"exit_code": exit_code})
                else:
                    rec.status = STEP_FAILED
                    rec.error = f"exit_code={exit_code}"
                    rec.error_code = "CAPABILITY_ERROR"
                    rec.message = (getattr(result, "stderr", "") or "")[:500]
        except Exception as e:
            rec.status = STEP_UNKNOWN
            rec.error = "Capability execution error (side-effect uncertain)"
            rec.error_code = "UNKNOWN_SIDE_EFFECT"
            rec.side_effect = "unknown"
            logger.warning("capability step %s error: %s", step.id, e)
        rec.finished_at = _now_iso()
        rec.duration_ms = int((time.monotonic() - t0) * 1000)
        return rec

    # Authorized capability with no bound adapter — record intent only (no fake external success)
    rec.status = STEP_SUCCEEDED
    rec.message = f"Authorized '{cap}' (no external adapter invoked)"
    rec.outputs = _bound_outputs({"capability": cap, "authorized": True})
    rec.side_effect = "none"
    rec.finished_at = _now_iso()
    rec.duration_ms = int((time.monotonic() - t0) * 1000)
    return rec


async def _run_step(step: WorkflowStep, context: dict, attempt: int) -> StepRecord:
    t0 = time.monotonic()
    st = step.type
    rec = StepRecord(
        step_id=step.id, type=st.value, status=STEP_RUNNING,
        attempt=attempt, started_at=_now_iso(),
    )

    if st == StepType.NOTIFY:
        msg = (step.inputs or {}).get("message") or step.name or step.description or "notify"
        rec.status = STEP_SUCCEEDED
        rec.message = str(msg)[:500]
        rec.outputs = {"notified": True}
        rec.side_effect = "none"
    elif st == StepType.WAIT:
        seconds = float((step.inputs or {}).get("seconds") or (step.inputs or {}).get("duration") or 0)
        seconds = max(0.0, min(seconds, MAX_WAIT_S))
        rec.status = STEP_WAITING
        if seconds > 0:
            await asyncio.sleep(seconds)
        rec.status = STEP_SUCCEEDED
        rec.message = f"waited {seconds}s (bounded)"
        rec.outputs = {"waited_s": seconds}
        rec.side_effect = "none"
    elif st == StepType.CONDITION:
        ok, err = _eval_condition(step.condition, context)
        if err:
            rec.status = STEP_FAILED
            rec.error = "Malformed or unsafe condition expression"
            rec.error_code = err
        else:
            rec.status = STEP_SUCCEEDED
            rec.message = f"condition={ok}"
            rec.outputs = {"result": ok}
        rec.side_effect = "none"
    elif st == StepType.APPROVAL:
        # Fail-closed: not a resumable approval workflow yet
        rec.status = STEP_PENDING_APPROVAL
        rec.error = "Approval required — execution cannot continue"
        rec.error_code = "GOVERNANCE_DENIED"
        rec.side_effect = "none"
    elif st == StepType.SUBFLOW:
        rec.status = STEP_FAILED
        rec.error = "subflow is unsupported"
        rec.error_code = "VALIDATION_ERROR"
        rec.side_effect = "none"
    elif st == StepType.PARALLEL:
        children = (step.inputs or {}).get("children") or (step.inputs or {}).get("steps") or []
        # Deterministic sequential marker — not concurrent execution
        rec.status = STEP_SUCCEEDED
        rec.message = f"parallel group ({len(children)} children sequentialized; not concurrent)"
        rec.outputs = {"children": children, "mode": "sequentialized"}
        rec.side_effect = "none"
    elif st == StepType.CAPABILITY:
        return await _run_capability_step(step, context, attempt)
    else:
        rec.status = STEP_FAILED
        rec.error = f"Unsupported step type: {st}"
        rec.error_code = "VALIDATION_ERROR"

    rec.finished_at = _now_iso()
    rec.duration_ms = int((time.monotonic() - t0) * 1000)
    return rec


def _next_step_id(step: WorkflowStep, result: StepRecord, steps: dict[str, WorkflowStep]) -> Optional[str]:
    if result.status in (STEP_FAILED, STEP_DENIED) and step.on_error and step.on_error in steps:
        return step.on_error
    if result.status in (STEP_PENDING_APPROVAL, STEP_UNKNOWN, STEP_BLOCKED):
        return None
    if step.type == StepType.CONDITION and result.status == STEP_SUCCEEDED:
        branch_key = "true" if result.outputs.get("result") else "false"
        if branch_key in (step.branches or {}):
            return step.branches[branch_key]
        alt = "yes" if result.outputs.get("result") else "no"
        if alt in (step.branches or {}):
            return step.branches[alt]
        # Unselected branches are not auto-walked; linear next_step still applies
    if step.next_step and step.next_step in steps:
        return step.next_step
    return step.next_step if step.next_step is None else (step.next_step if step.next_step in steps else None)


def _mark_skipped_downstream(start: Optional[str], steps: dict[str, WorkflowStep], state: ExecutionState) -> None:
    """Best-effort: mark linear next chain as skipped after a hard failure (no on_error)."""
    if not start or start not in steps:
        return
    seen = set()
    cur = start
    while cur and cur not in seen:
        seen.add(cur)
        if cur in state.completed or cur in state.records:
            step = steps[cur]
            cur = step.next_step
            continue
        state.records[cur] = StepRecord(
            step_id=cur, type=steps[cur].type.value, status=STEP_SKIPPED,
            message="Skipped due to upstream failure",
        ).to_dict()
        cur = steps[cur].next_step


async def persist_execution_state(job_id: str, state: ExecutionState) -> None:
    """Write mutable execution_state into job.payload without touching workflow_snapshot."""
    try:
        from core import database as dbmod
        from core.database import ExecutionJob
        from sqlalchemy import select
        Session = dbmod.AsyncSessionLocal
        async with Session() as db:
            r = await db.execute(select(ExecutionJob).where(ExecutionJob.id == job_id))
            job = r.scalar_one_or_none()
            if not job:
                return
            if job.status == "cancelled":
                return
            payload = dict(job.payload or {})
            # Preserve immutable snapshot
            snap = payload.get("workflow_snapshot")
            payload["execution_state"] = state.to_dict()
            if snap is not None:
                payload["workflow_snapshot"] = snap
            job.payload = scrub_secrets(payload)
            await db.commit()
    except Exception as e:
        logger.warning("persist execution_state failed: %s", e)


async def _job_cancelled(job_id: Optional[str]) -> bool:
    if not job_id:
        return False
    try:
        from core import database as dbmod
        from core.database import ExecutionJob
        from sqlalchemy import select
        Session = dbmod.AsyncSessionLocal
        async with Session() as db:
            r = await db.execute(select(ExecutionJob).where(ExecutionJob.id == job_id))
            job = r.scalar_one_or_none()
            return bool(job and job.status == "cancelled")
    except Exception:
        return False


@dataclass
class OrchestrationResult:
    status: str
    workflow_id: str
    workflow_version: int
    schema_version: int = SNAPSHOT_SCHEMA_VERSION
    steps: list[dict] = field(default_factory=list)
    execution_state: dict = field(default_factory=dict)
    error: Optional[str] = None
    error_code: Optional[str] = None
    permanent: bool = False
    started_at: str = ""
    finished_at: str = ""

    def to_dict(self) -> dict:
        return scrub_secrets({
            "status": self.status,
            "workflow_id": self.workflow_id,
            "workflow_version": self.workflow_version,
            "schema_version": self.schema_version,
            "steps": self.steps,
            "execution_state": self.execution_state,
            "error": self.error,
            "error_code": self.error_code,
            "permanent": self.permanent,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        })


async def run_from_snapshot(
    snap: dict,
    *,
    execution_state: Optional[dict] = None,
    job_id: Optional[str] = None,
    max_steps: int = MAX_STEPS,
    extra_context: Optional[dict] = None,
) -> OrchestrationResult:
    started = _now_iso()
    try:
        snap = validate_execution_snapshot(snap)
    except SnapshotError as e:
        return OrchestrationResult(
            status=JOB_FAILED,
            workflow_id=str((snap or {}).get("workflow_id") or ""),
            workflow_version=int((snap or {}).get("workflow_version") or 0),
            error=f"corrupt snapshot: {e}",
            error_code="VALIDATION_ERROR",
            permanent=True,
            started_at=started,
            finished_at=_now_iso(),
        )

    wf = _workflow_from_snapshot(snap)
    steps = _step_map(wf)
    if not steps:
        return OrchestrationResult(
            status=JOB_FAILED, workflow_id=snap["workflow_id"],
            workflow_version=int(snap["workflow_version"]),
            error="workflow has no steps", error_code="VALIDATION_ERROR", permanent=True,
            started_at=started, finished_at=_now_iso(),
        )

    state = ExecutionState.from_dict(execution_state)
    if state.terminal_status in (JOB_SUCCEEDED, JOB_FAILED):
        # Already finished — idempotent return
        return OrchestrationResult(
            status=state.terminal_status,
            workflow_id=snap["workflow_id"],
            workflow_version=int(snap["workflow_version"]),
            steps=list(state.records.values()),
            execution_state=state.to_dict(),
            error=state.error,
            error_code=state.error_code,
            permanent=True,
            started_at=started,
            finished_at=_now_iso(),
        )

    # Recovery: if a step was left RUNNING with non-none side_effect → UNKNOWN, do not re-run
    if state.current_step_id and state.current_step_id in state.records:
        prev = state.records[state.current_step_id]
        if prev.get("status") == STEP_RUNNING and prev.get("side_effect") not in (None, "none"):
            prev["status"] = STEP_UNKNOWN
            prev["error"] = "Worker interrupted; side-effect uncertain"
            prev["error_code"] = "UNKNOWN_SIDE_EFFECT"
            prev["side_effect"] = "unknown"
            state.records[state.current_step_id] = prev
            state.terminal_status = JOB_FAILED
            state.error = prev["error"]
            state.error_code = "UNKNOWN_SIDE_EFFECT"
            if job_id:
                await persist_execution_state(job_id, state)
            return OrchestrationResult(
                status=JOB_FAILED,
                workflow_id=snap["workflow_id"],
                workflow_version=int(snap["workflow_version"]),
                steps=list(state.records.values()),
                execution_state=state.to_dict(),
                error=state.error,
                error_code="UNKNOWN_SIDE_EFFECT",
                permanent=True,
                started_at=started,
                finished_at=_now_iso(),
            )
        # Running with no side effect: safe to retry same step
        if prev.get("status") == STEP_RUNNING:
            state.records.pop(state.current_step_id, None)

    context: dict[str, Any] = dict(state.context or {})
    context.update({
        "owner_id": snap.get("owner_id"),
        "tenant_id": snap.get("tenant_id"),
        "correlation_id": snap.get("correlation_id"),
        "workflow_id": snap["workflow_id"],
        "workflow_version": snap["workflow_version"],
        "job_id": job_id,
    })
    if extra_context:
        context.update(extra_context)

    start_id = state.current_step_id or wf.start_step or next(iter(steps))
    if start_id not in steps and start_id not in state.completed:
        return OrchestrationResult(
            status=JOB_FAILED, workflow_id=snap["workflow_id"],
            workflow_version=int(snap["workflow_version"]),
            error=f"start_step '{start_id}' not found", error_code="VALIDATION_ERROR",
            permanent=True, started_at=started, finished_at=_now_iso(),
        )

    # Skip already completed
    current: Optional[str] = start_id
    if current in state.completed:
        # Advance along next from last completed
        last = steps.get(current)
        current = last.next_step if last else None
        while current and current in state.completed:
            last = steps.get(current)
            current = last.next_step if last else None

    visited = 0
    terminal_fail = False
    permanent = False
    error_code = None

    while current and visited < max_steps:
        if await _job_cancelled(job_id):
            state.terminal_status = "cancelled"
            state.error = "cancelled"
            state.error_code = "CANCELLED"
            if job_id:
                await persist_execution_state(job_id, state)
            return OrchestrationResult(
                status="cancelled",
                workflow_id=snap["workflow_id"],
                workflow_version=int(snap["workflow_version"]),
                steps=list(state.records.values()),
                execution_state=state.to_dict(),
                error="cancelled",
                error_code="CANCELLED",
                permanent=True,
                started_at=started,
                finished_at=_now_iso(),
            )

        if current in state.completed:
            step = steps.get(current)
            current = step.next_step if step else None
            continue

        step = steps.get(current)
        if not step:
            terminal_fail = True
            error_code = "VALIDATION_ERROR"
            state.records[str(current)] = StepRecord(
                step_id=str(current), type="unknown", status=STEP_FAILED,
                error=f"step not found", error_code=error_code, attempt=1,
            ).to_dict()
            break

        attempt = int((state.records.get(step.id) or {}).get("attempt") or 0) + 1
        state.current_step_id = step.id
        state.records[step.id] = StepRecord(
            step_id=step.id, type=step.type.value, status=STEP_RUNNING,
            attempt=attempt, started_at=_now_iso(),
        ).to_dict()
        if job_id:
            await persist_execution_state(job_id, state)

        result = await _run_step(step, context, attempt)
        state.records[step.id] = result.to_dict()
        if len(context) < MAX_CONTEXT_KEYS:
            context[step.id] = result.outputs
            state.context = {k: v for k, v in context.items() if k not in (
                "owner_id", "tenant_id", "correlation_id", "workflow_id", "workflow_version", "job_id"
            )}

        if result.status == STEP_SUCCEEDED:
            state.completed.append(step.id)
            state.current_step_id = None
            if job_id:
                await persist_execution_state(job_id, state)
            nxt = _next_step_id(step, result, steps)
            # Skip marking unselected condition branches
            if step.type == StepType.CONDITION and step.branches:
                chosen = nxt
                for bkey, bid in step.branches.items():
                    if bid != chosen and bid in steps and bid not in state.completed:
                        state.records[bid] = StepRecord(
                            step_id=bid, type=steps[bid].type.value,
                            status=STEP_SKIPPED, message=f"branch '{bkey}' not taken",
                        ).to_dict()
            current = nxt
            visited += 1
            continue

        # Failure / deny / approval / unknown
        terminal_fail = True
        error_code = result.error_code or "INTERNAL_ERROR"
        permanent = error_code in PERMANENT_ERRORS or result.status in (
            STEP_DENIED, STEP_PENDING_APPROVAL, STEP_UNKNOWN
        )
        state.error = result.error
        state.error_code = error_code
        if result.status in (STEP_FAILED, STEP_DENIED) and step.on_error and step.on_error in steps:
            current = step.on_error
            terminal_fail = False
            permanent = False
            visited += 1
            continue
        # Mark linear downstream as skipped
        if step.next_step:
            _mark_skipped_downstream(step.next_step, steps, state)
        break

    if visited >= max_steps and current:
        terminal_fail = True
        error_code = "INTERNAL_ERROR"
        permanent = True
        state.error = f"max_steps={max_steps} exceeded"

    finished = _now_iso()
    status = JOB_FAILED if terminal_fail else JOB_SUCCEEDED
    state.terminal_status = status
    state.current_step_id = None if status == JOB_SUCCEEDED else state.current_step_id
    if job_id:
        await persist_execution_state(job_id, state)

    return OrchestrationResult(
        status=status,
        workflow_id=snap["workflow_id"],
        workflow_version=int(snap["workflow_version"]),
        steps=list(state.records.values()),
        execution_state=state.to_dict(),
        error=state.error,
        error_code=error_code,
        permanent=permanent,
        started_at=started,
        finished_at=finished,
    )


async def handle_workflow_job(job) -> dict:
    """JobWorker handler — snapshot + execution_state from payload only."""
    try:
        snap = load_snapshot_for_job(job)
    except SnapshotError as e:
        logger.error("workflow job %s corrupt snapshot: %s", getattr(job, "id", "?"), e)
        return {
            "status": JOB_FAILED,
            "error": f"corrupt snapshot: {e}",
            "error_code": "VALIDATION_ERROR",
            "permanent": True,
            "isolation": "none",
        }

    payload = getattr(job, "payload", None) or {}
    prior_state = payload.get("execution_state") if isinstance(payload, dict) else None
    result = await run_from_snapshot(
        snap,
        execution_state=prior_state,
        job_id=getattr(job, "id", None),
    )
    out = result.to_dict()
    # Map cancelled to failed for job queue terminal handling if needed
    if out["status"] == "cancelled":
        out["status"] = JOB_FAILED
        out["error_code"] = "CANCELLED"
        out["permanent"] = True
    return out
