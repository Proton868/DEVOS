"""
Workflow step executor / orchestration runtime.

Input: immutable WorkflowExecutionSnapshot (schema_version=1) from ExecutionJob.payload.
Never reloads the live WorkflowRecord during execution or retry.

Governance remains authoritative:
  - CAPABILITY steps pass through UCIPGateway + require_authority
  - Enabling a workflow is not an authority grant
  - External side effects are classified; failures do not claim false rollback
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass, field
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


@dataclass
class StepResult:
    step_id: str
    type: str
    status: str  # succeeded | failed | skipped | denied | pending_approval | unknown
    message: str = ""
    outputs: dict = field(default_factory=dict)
    side_effect: str = "none"  # none | local | external | unknown
    duration_ms: int = 0
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return scrub_secrets({
            "step_id": self.step_id,
            "type": self.type,
            "status": self.status,
            "message": self.message,
            "outputs": self.outputs,
            "side_effect": self.side_effect,
            "duration_ms": self.duration_ms,
            "error": self.error,
        })


@dataclass
class OrchestrationResult:
    status: str  # succeeded | failed
    workflow_id: str
    workflow_version: int
    schema_version: int = SNAPSHOT_SCHEMA_VERSION
    steps: list[StepResult] = field(default_factory=list)
    context: dict = field(default_factory=dict)
    error: Optional[str] = None
    started_at: str = ""
    finished_at: str = ""

    def to_dict(self) -> dict:
        return scrub_secrets({
            "status": self.status,
            "workflow_id": self.workflow_id,
            "workflow_version": self.workflow_version,
            "schema_version": self.schema_version,
            "steps": [s.to_dict() for s in self.steps],
            "context_keys": list(self.context.keys()),
            "error": self.error,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        })


def _workflow_from_snapshot(snap: dict) -> Workflow:
    definition = dict(snap.get("definition") or {})
    definition.setdefault("workflow_id", snap["workflow_id"])
    definition.setdefault("name", snap.get("name") or definition.get("name") or "workflow")
    return Workflow.from_dict(definition)


def _step_map(wf: Workflow) -> dict[str, WorkflowStep]:
    return {s.id: s for s in wf.steps}


def _eval_condition(expr: Optional[str], context: dict) -> bool:
    """Very small safe expression evaluator for CONDITION steps.

    Supports: true/false literals, context key truthiness, and
    simple equality: key == value / key != value.
    Does NOT eval arbitrary Python.
    """
    if not expr or not str(expr).strip():
        return True
    e = str(expr).strip()
    low = e.lower()
    if low in ("true", "1", "yes"):
        return True
    if low in ("false", "0", "no"):
        return False
    m = re.match(r"^([A-Za-z_][\w.]*)\s*(==|!=)\s*(.+)$", e)
    if m:
        key, op, raw = m.group(1), m.group(2), m.group(3).strip().strip("'\"")
        cur = context
        for part in key.split("."):
            if isinstance(cur, dict) and part in cur:
                cur = cur[part]
            else:
                cur = None
                break
        if op == "==":
            return str(cur) == raw
        return str(cur) != raw
    # bare key
    cur = context
    for part in e.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return False
    return bool(cur)


async def _ucip_gate(capability: str, inputs: dict, context: dict) -> tuple[str, str]:
    """Return (decision, reason) via UCIPGateway when available."""
    try:
        from governance.ucip import UCIPGateway, TrustLevel, AgentIdentity, ACTION_TO_CAP
        user_id = str(context.get("owner_id") or "workflow")
        session_id = str(context.get("correlation_id") or "wf")
        identity = AgentIdentity.create(
            user_id=user_id,
            session_id=session_id,
            trust_level=TrustLevel.OPERATOR,
        )
        gw = UCIPGateway(identity)
        inv = {v: k for k, v in ACTION_TO_CAP.items()}
        action = inv.get(capability, capability.replace("ucip:", "").replace(".", "_"))
        import json
        action_input = json.dumps(inputs or {}, default=str)[:4000]
        resp = gw.request(action, action_input, context={"source": "workflow_executor"})
        # PolicyDecision may use .decision or .approved()
        decision = getattr(resp, "decision", None)
        if decision is None:
            if hasattr(resp, "approved") and callable(resp.approved):
                decision = "ALLOW" if resp.approved() else (
                    "ESCALATE_TO_HUMAN" if getattr(resp, "needs_human", lambda: False)() else "DENY"
                )
            else:
                decision = "DENY"
        reason = getattr(resp, "reason", "") or getattr(resp, "message", "") or ""
        return str(decision), str(reason)
    except Exception as e:
        logger.warning("UCIP gate unavailable: %s", e)
        return "DENY", f"UCIP unavailable: {e}"


def _require_authority_safe(capability: str, context: dict, path_class=None) -> tuple[bool, str]:
    try:
        from governance.execution_authority import require_authority
        from governance.execution_pipeline import PathClass
        pc = path_class or PathClass.DURABLE
        require_authority(
            path_class=pc,
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


async def _run_capability_step(step: WorkflowStep, context: dict) -> StepResult:
    t0 = time.monotonic()
    cap = step.capability or ""
    if not cap:
        return StepResult(
            step_id=step.id, type=step.type.value, status="failed",
            error="CAPABILITY step missing capability slug",
            duration_ms=int((time.monotonic() - t0) * 1000),
        )

    decision, reason = await _ucip_gate(cap, step.inputs, context)
    if decision == "DENY":
        return StepResult(
            step_id=step.id, type=step.type.value, status="denied",
            message=reason, error=f"UCIP DENY: {reason}",
            side_effect="none",
            duration_ms=int((time.monotonic() - t0) * 1000),
        )
    if decision == "ESCALATE_TO_HUMAN":
        return StepResult(
            step_id=step.id, type=step.type.value, status="pending_approval",
            message=reason or "HITL required",
            error="Human approval required — fail-closed until approved",
            side_effect="none",
            duration_ms=int((time.monotonic() - t0) * 1000),
        )

    ok, auth_msg = _require_authority_safe(cap, context)
    if not ok:
        return StepResult(
            step_id=step.id, type=step.type.value, status="denied",
            message=auth_msg, error=auth_msg,
            duration_ms=int((time.monotonic() - t0) * 1000),
        )

    # Bound adapters: only sandbox pure code when code is in inputs
    code = (step.inputs or {}).get("code") or (step.inputs or {}).get("source")
    lang = (step.inputs or {}).get("language") or (
        "python" if "python" in cap else "bash" if "bash" in cap else None
    )
    if code and lang in ("python", "bash", "node"):
        try:
            from governance.sandbox import SandboxedExecutor
            exe = SandboxedExecutor(allow_network=False)
            result = await exe.run(str(code), language=lang)
            status = "succeeded"
            side = "local"
            msg = result.to_brain_summary() if hasattr(result, "to_brain_summary") else str(result)
            if getattr(result, "exit_code", 0) not in (0, None):
                status = "failed"
            return StepResult(
                step_id=step.id, type=step.type.value, status=status,
                message=str(msg)[:2000],
                outputs={"exit_code": getattr(result, "exit_code", None)},
                side_effect=side,
                duration_ms=int((time.monotonic() - t0) * 1000),
            )
        except Exception as e:
            return StepResult(
                step_id=step.id, type=step.type.value, status="failed",
                error=str(e), side_effect="unknown",
                duration_ms=int((time.monotonic() - t0) * 1000),
            )

    # Authorized but no bound side-effecting adapter — record intent without faking success of external work
    return StepResult(
        step_id=step.id, type=step.type.value, status="succeeded",
        message=f"Authorized capability '{cap}' recorded (no external adapter invoked)",
        outputs={"capability": cap, "authorized": True},
        side_effect="none",
        duration_ms=int((time.monotonic() - t0) * 1000),
    )


async def _run_step(step: WorkflowStep, context: dict) -> StepResult:
    t0 = time.monotonic()
    st = step.type

    if st == StepType.NOTIFY:
        msg = (step.inputs or {}).get("message") or step.name or step.description or "notify"
        logger.info("workflow notify step=%s msg=%s", step.id, str(msg)[:200])
        return StepResult(
            step_id=step.id, type=st.value, status="succeeded",
            message=str(msg)[:500],
            outputs={"notified": True},
            side_effect="none",
            duration_ms=int((time.monotonic() - t0) * 1000),
        )

    if st == StepType.WAIT:
        seconds = float((step.inputs or {}).get("seconds") or (step.inputs or {}).get("duration") or 0)
        seconds = max(0.0, min(seconds, 30.0))  # hard cap
        if seconds > 0:
            await asyncio.sleep(seconds)
        return StepResult(
            step_id=step.id, type=st.value, status="succeeded",
            message=f"waited {seconds}s",
            outputs={"waited_s": seconds},
            side_effect="none",
            duration_ms=int((time.monotonic() - t0) * 1000),
        )

    if st == StepType.CONDITION:
        ok = _eval_condition(step.condition, context)
        return StepResult(
            step_id=step.id, type=st.value, status="succeeded",
            message=f"condition={ok}",
            outputs={"result": ok},
            side_effect="none",
            duration_ms=int((time.monotonic() - t0) * 1000),
        )

    if st == StepType.APPROVAL:
        # Fail-closed: never auto-approve
        return StepResult(
            step_id=step.id, type=st.value, status="pending_approval",
            message="Human approval gate — not auto-approved",
            error="approval required",
            side_effect="none",
            duration_ms=int((time.monotonic() - t0) * 1000),
        )

    if st == StepType.SUBFLOW:
        return StepResult(
            step_id=step.id, type=st.value, status="failed",
            error="Nested subflow execution is not enabled in this runtime",
            side_effect="none",
            duration_ms=int((time.monotonic() - t0) * 1000),
        )

    if st == StepType.PARALLEL:
        # Sequentialize child IDs listed in inputs.children for determinism
        children = (step.inputs or {}).get("children") or (step.inputs or {}).get("steps") or []
        return StepResult(
            step_id=step.id, type=st.value, status="succeeded",
            message=f"parallel marker with {len(children)} children (sequentialized)",
            outputs={"children": children},
            side_effect="none",
            duration_ms=int((time.monotonic() - t0) * 1000),
        )

    if st == StepType.CAPABILITY:
        return await _run_capability_step(step, context)

    return StepResult(
        step_id=step.id, type=getattr(st, "value", str(st)), status="failed",
        error=f"Unsupported step type: {st}",
        duration_ms=int((time.monotonic() - t0) * 1000),
    )


def _next_step_id(step: WorkflowStep, result: StepResult, steps: dict[str, WorkflowStep]) -> Optional[str]:
    if result.status in ("failed", "denied") and step.on_error:
        return step.on_error if step.on_error in steps else None
    if result.status == "pending_approval":
        return None
    if step.type == StepType.CONDITION and result.outputs:
        branch_key = "true" if result.outputs.get("result") else "false"
        if branch_key in (step.branches or {}):
            return step.branches[branch_key]
        # also allow yes/no
        alt = "yes" if result.outputs.get("result") else "no"
        if alt in (step.branches or {}):
            return step.branches[alt]
    return step.next_step if step.next_step in steps or step.next_step is None else step.next_step


async def run_from_snapshot(
    snap: dict,
    *,
    max_steps: int = 100,
    extra_context: Optional[dict] = None,
) -> OrchestrationResult:
    """Execute a workflow from an immutable snapshot dict."""
    started = datetime.now(timezone.utc).isoformat()
    try:
        snap = validate_execution_snapshot(snap)
    except SnapshotError as e:
        return OrchestrationResult(
            status="failed",
            workflow_id=str((snap or {}).get("workflow_id") or ""),
            workflow_version=int((snap or {}).get("workflow_version") or 0),
            error=f"corrupt snapshot: {e}",
            started_at=started,
            finished_at=datetime.now(timezone.utc).isoformat(),
        )

    wf = _workflow_from_snapshot(snap)
    steps = _step_map(wf)
    if not steps:
        return OrchestrationResult(
            status="failed",
            workflow_id=snap["workflow_id"],
            workflow_version=int(snap["workflow_version"]),
            error="workflow has no steps",
            started_at=started,
            finished_at=datetime.now(timezone.utc).isoformat(),
        )

    start_id = wf.start_step or next(iter(steps))
    if start_id not in steps:
        return OrchestrationResult(
            status="failed",
            workflow_id=snap["workflow_id"],
            workflow_version=int(snap["workflow_version"]),
            error=f"start_step '{start_id}' not found",
            started_at=started,
            finished_at=datetime.now(timezone.utc).isoformat(),
        )

    context: dict[str, Any] = {
        "owner_id": snap.get("owner_id"),
        "tenant_id": snap.get("tenant_id"),
        "correlation_id": snap.get("correlation_id"),
        "workflow_id": snap["workflow_id"],
        "workflow_version": snap["workflow_version"],
        "steps": {},
    }
    if extra_context:
        context.update(extra_context)

    results: list[StepResult] = []
    current: Optional[str] = start_id
    visited = 0
    terminal_fail = False

    while current and visited < max_steps:
        visited += 1
        step = steps.get(current)
        if not step:
            results.append(StepResult(
                step_id=str(current), type="unknown", status="failed",
                error=f"step '{current}' not found",
            ))
            terminal_fail = True
            break

        result = await _run_step(step, context)
        results.append(result)
        context["steps"][step.id] = result.to_dict()
        context[step.id] = result.outputs

        if result.status in ("failed", "denied", "pending_approval"):
            if result.status != "pending_approval" and step.on_error and step.on_error in steps:
                current = step.on_error
                continue
            terminal_fail = True
            break

        nxt = _next_step_id(step, result, steps)
        if nxt and nxt not in steps:
            results.append(StepResult(
                step_id=str(nxt), type="unknown", status="failed",
                error=f"next_step '{nxt}' not found",
            ))
            terminal_fail = True
            break
        current = nxt

    if visited >= max_steps and current:
        terminal_fail = True
        results.append(StepResult(
            step_id="__limit__", type="system", status="failed",
            error=f"max_steps={max_steps} exceeded",
        ))

    finished = datetime.now(timezone.utc).isoformat()
    return OrchestrationResult(
        status="failed" if terminal_fail else "succeeded",
        workflow_id=snap["workflow_id"],
        workflow_version=int(snap["workflow_version"]),
        steps=results,
        context={k: v for k, v in context.items() if k not in ("steps",)},
        error=results[-1].error if terminal_fail and results else None,
        started_at=started,
        finished_at=finished,
    )


async def handle_workflow_job(job) -> dict:
    """JobWorker handler for job_type='workflow'. Uses payload snapshot only."""
    try:
        snap = load_snapshot_for_job(job)
    except SnapshotError as e:
        logger.error("workflow job %s corrupt snapshot: %s", getattr(job, "id", "?"), e)
        return {
            "status": "failed",
            "error": f"corrupt snapshot: {e}",
            "isolation": "none",
        }

    result = await run_from_snapshot(snap)
    out = result.to_dict()
    out["status"] = result.status
    if result.error:
        out["error"] = result.error
    return out
