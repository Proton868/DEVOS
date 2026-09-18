"""Durable automation runtime — sequential step orchestrator on the execution spine.

Authority boundary
------------------
Automation Runtime = orchestrator of immutable workflow snapshots.
CapabilitySubstrate / UCIP = authorization for consequential steps.
ExecutionOperation / ExecutionJob = durable consequential identity.
Evidence = proof of outcome.
AutomationRunRecord = automation-level projection (not a second ledger).

Never executes a mutable draft when the run references a frozen snapshot.
"""
from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Optional

from brain.flow_automation import (
    AutomationRun,
    RunStatus,
    load_run_durable,
    persist_run_durable,
    get_run_store,
)
from brain.workflow_executor import (
    run_from_snapshot,
    ExecutionState,
    JOB_SUCCEEDED,
    JOB_FAILED,
    STEP_SUCCEEDED,
    STEP_FAILED,
    STEP_UNKNOWN,
    STEP_DENIED,
)
from governance.reliability import scrub_secrets
from brain.automation_orchestration import (
    select_next_step,
    select_eligible_steps,
    has_unresolved_unknown,
)
from brain.workflow_executor import ExecutionState as _ExecState

logger = logging.getLogger("devos.automation_runtime")


def step_operation_idempotency_key(
    *,
    automation_run_id: str,
    workflow_version: int,
    step_id: str,
    occurrence: int = 1,
) -> str:
    """Logical identity for a consequential step within a run.

    Matches the operation idempotency contract: key identifies the *logical*
    consequential action (this step in this run version), not a transport attempt.
    """
    raw = json.dumps(
        {
            "run_id": automation_run_id,
            "version": int(workflow_version),
            "step_id": step_id,
            "occurrence": int(occurrence),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(raw.encode()).hexdigest()


def _map_result_status(result_status: str, steps: list) -> RunStatus:
    st = (result_status or "").lower()
    if st == JOB_SUCCEEDED or st == "succeeded":
        return RunStatus.SUCCEEDED
    if st == "cancelled":
        return RunStatus.CANCELLED
    # UNKNOWN side effect → failed run status but expose unknown in summary
    for rec in steps or []:
        if isinstance(rec, dict) and rec.get("status") == STEP_UNKNOWN:
            return RunStatus.FAILED
    return RunStatus.FAILED


async def execute_automation_run(
    run_id: str,
    *,
    job_id: Optional[str] = None,
    operation_id: Optional[str] = None,
    execution_state: Optional[dict] = None,
) -> AutomationRun:
    """Execute an automation run from its immutable definition_snapshot.

    Uses `run_from_snapshot` (existing sequential executor). Does not reload
    the live WorkflowRecord draft — only the run's frozen snapshot.
    """
    run = await load_run_durable(run_id)
    store = get_run_store()
    if run is None:
        # Fall back to process store (tests)
        run = store.get(run_id)
    if run is None:
        raise KeyError(f"automation run not found: {run_id}")

    if run.status == RunStatus.SUCCEEDED:
        return run  # idempotent terminal success

    snap = getattr(run, "definition_snapshot", None) or {}
    if not snap or not isinstance(snap, dict):
        # Try execution_state embedding from older paths
        snap = (run.execution_state or {}).get("snapshot") or {}
    if not snap.get("workflow_id"):
        run.status = RunStatus.FAILED
        run.error = "missing_immutable_snapshot"
        await persist_run_durable(run)
        store.put(run)
        return run

    # Force frozen identity from run record (never live draft version)
    snap = dict(snap)
    snap["workflow_id"] = run.workflow_id
    snap["workflow_version"] = int(run.workflow_version or snap.get("workflow_version") or 1)
    snap.setdefault("owner_id", run.owner_id)
    snap.setdefault("tenant_id", run.tenant_id)
    snap.setdefault("correlation_id", run.correlation_id)

    state_in = execution_state or dict(run.execution_state or {})
    # Strip non-executor keys
    for k in ("job_id", "operation_id", "snapshot"):
        state_in.pop(k, None)

    run.status = RunStatus.RUNNING
    if job_id:
        setattr(run, "execution_job_id", job_id)
    if operation_id:
        setattr(run, "operation_id", operation_id)
    await persist_run_durable(run)
    store.put(run)

    extra = {
        "owner_id": run.owner_id,
        "tenant_id": run.tenant_id,
        "correlation_id": run.correlation_id,
        "automation_run_id": run.run_id,
        "run_mode": run.mode.value if hasattr(run.mode, "value") else str(run.mode),
    }
    definition = dict(snap.get("definition") or {})
    use_orch = bool(
        definition.get("edges")
        or definition.get("joins")
        or (definition.get("metadata") or {}).get("edges")
        or (definition.get("metadata") or {}).get("joins")
    )
    jid = job_id or getattr(run, "execution_job_id", None)
    if use_orch:
        state = _ExecState.from_dict(state_in if state_in else None)
        result = None
        for _ in range(100):
            if has_unresolved_unknown(state):
                break
            # Clear false "job complete" when graph still has eligible work
            if state.terminal_status in (JOB_SUCCEEDED, "succeeded"):
                if select_eligible_steps(snap, state):
                    state.terminal_status = None
                    state.error = None
                    state.error_code = None
                else:
                    break
            if state.terminal_status in ("failed", "cancelled", JOB_FAILED):
                break
            nxt = select_next_step(snap, state)
            if not nxt:
                break
            state.current_step_id = nxt
            # Ensure executor does not skip via stale terminal
            state.terminal_status = None
            result = await run_from_snapshot(
                snap,
                execution_state=state.to_dict(),
                job_id=jid,
                max_steps=1,
                extra_context=extra,
            )
            state = _ExecState.from_dict(result.execution_state)
            if result.error_code == "UNKNOWN_SIDE_EFFECT" or any(
                isinstance(s, dict) and s.get("status") == STEP_UNKNOWN
                for s in (result.steps or [])
            ):
                break
            if result.status not in (JOB_SUCCEEDED, "succeeded") and result.permanent:
                break
        # Final result from accumulated state
        from brain.workflow_executor import OrchestrationResult
        if has_unresolved_unknown(state):
            result = OrchestrationResult(
                status=JOB_FAILED,
                workflow_id=snap["workflow_id"],
                workflow_version=int(snap["workflow_version"]),
                steps=list(state.records.values()),
                execution_state=state.to_dict(),
                error=state.error or "UNKNOWN_SIDE_EFFECT",
                error_code="UNKNOWN_SIDE_EFFECT",
                permanent=True,
            )
        else:
            failed = any(
                isinstance(r, dict) and r.get("status") in (STEP_FAILED, STEP_DENIED)
                for r in state.records.values()
            )
            result = OrchestrationResult(
                status=JOB_FAILED if failed else JOB_SUCCEEDED,
                workflow_id=snap["workflow_id"],
                workflow_version=int(snap["workflow_version"]),
                steps=list(state.records.values()),
                execution_state=state.to_dict(),
                error=state.error,
                error_code=state.error_code,
                permanent=failed,
            )
    else:
        result = await run_from_snapshot(

            snap,
            execution_state=state_in if state_in else None,
            job_id=jid,
            extra_context=extra,
        )

    steps = list(result.steps or [])
    has_unknown = any(
        isinstance(s, dict) and s.get("status") == STEP_UNKNOWN for s in steps
    )
    run.execution_state = scrub_secrets(dict(result.execution_state or {}))
    run.execution_state["job_id"] = getattr(run, "execution_job_id", None)
    run.execution_state["operation_id"] = getattr(run, "operation_id", None)
    run.result_summary = scrub_secrets({
        "status": result.status,
        "steps": steps[:50],
        "error_code": result.error_code,
        "has_unknown": has_unknown,
        "workflow_version": run.workflow_version,
        "completed_step_ids": [
            s.get("step_id") for s in steps if isinstance(s, dict) and s.get("status") == STEP_SUCCEEDED
        ],
        "failed_step_ids": [
            s.get("step_id") for s in steps if isinstance(s, dict) and s.get("status") in (STEP_FAILED, STEP_DENIED, STEP_UNKNOWN)
        ],
    })
    run.error = result.error
    if has_unknown:
        run.status = RunStatus.FAILED
        run.error = run.error or "UNKNOWN_SIDE_EFFECT"
        run.result_summary["pending_review"] = True
    else:
        run.status = _map_result_status(result.status, steps)
    run.updated_at = run.updated_at  # store handles timestamps
    await persist_run_durable(run)
    store.put(run)
    return run


async def handle_workflow_job(job) -> dict:
    """JobWorker handler for job_type='workflow'.

    Payload must include run_id and/or snapshot. Uses job ownership from claim.
    """
    payload = job.payload if isinstance(job.payload, dict) else {}
    run_id = payload.get("run_id")
    op_id = getattr(job, "operation_id", None) or payload.get("operation_id")
    job_id = getattr(job, "id", None)

    if not run_id:
        # Legacy path: snapshot-only job without AutomationRunRecord
        snap = payload.get("snapshot") or payload.get("workflow_snapshot")
        if not snap:
            return {"status": "failed", "error": "missing_run_id_or_snapshot", "permanent": True}
        result = await run_from_snapshot(
            snap,
            execution_state=payload.get("execution_state"),
            job_id=job_id,
            extra_context={
                "owner_id": getattr(job, "owner_id", None),
                "tenant_id": getattr(job, "tenant_id", None),
            },
        )
        return {
            "status": "succeeded" if result.status == JOB_SUCCEEDED else "failed",
            "error": result.error,
            "permanent": bool(result.permanent or result.error_code == "UNKNOWN_SIDE_EFFECT"),
            "result": result.to_dict() if hasattr(result, "to_dict") else {},
        }

    run = await execute_automation_run(
        run_id,
        job_id=job_id,
        operation_id=op_id,
        execution_state=payload.get("execution_state"),
    )
    permanent = False
    if run.result_summary and run.result_summary.get("has_unknown"):
        permanent = True
    if run.status == RunStatus.SUCCEEDED:
        return {
            "status": "succeeded",
            "run_id": run.run_id,
            "result": run.result_summary,
            "operation_id": getattr(run, "operation_id", None),
        }
    return {
        "status": "failed",
        "error": run.error or "automation_failed",
        "permanent": permanent,
        "run_id": run.run_id,
        "result": run.result_summary,
        "operation_id": getattr(run, "operation_id", None),
    }


def register_automation_handlers(worker) -> None:
    """Attach workflow handler to a JobWorker instance."""
    worker.register("workflow", handle_workflow_job)
