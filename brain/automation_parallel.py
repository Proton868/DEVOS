"""Bounded parallel automation scheduling on the existing execution spine.

Architecture
------------
Parallelism is an *orchestration scheduling* concern only.

  select_eligible_steps()  →  [B, C]
        ↓
  each step → step_operation_idempotency_key → ExecutionOperation + ExecutionJob
        ↓
  workers claim jobs independently
        ↓
  join gates D via ALL_SUCCESS / ANY_SUCCESS

No second executor. SCRIPT/HTTP/DATABASE still go through workflow_executor
+ UCIP + isolation.

Concurrency limit: per automation *run* (default 4). Conservative; not a
distributed fairness system.
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Optional

from brain.automation_orchestration import (
    select_eligible_steps,
    has_unresolved_unknown,
    JoinMode,
)
from brain.automation_runtime import step_operation_idempotency_key
from brain.workflow_executor import (
    ExecutionState,
    run_from_snapshot,
    STEP_SUCCEEDED,
    STEP_FAILED,
    STEP_DENIED,
    STEP_UNKNOWN,
    STEP_RUNNING,
    JOB_SUCCEEDED,
    JOB_FAILED,
)

logger = logging.getLogger("devos.automation_parallel")

# Per-run concurrent step cap (not global, not per-tenant).
DEFAULT_MAX_CONCURRENT_STEPS_PER_RUN = 4


def max_concurrent_steps_per_run() -> int:
    raw = os.environ.get("DEVOS_AUTOMATION_MAX_PARALLEL", str(DEFAULT_MAX_CONCURRENT_STEPS_PER_RUN))
    try:
        n = int(raw)
    except Exception:
        n = DEFAULT_MAX_CONCURRENT_STEPS_PER_RUN
    return max(1, min(n, 32))  # hard ceiling


def concurrency_policy() -> dict:
    return {
        "scope": "per_automation_run",
        "default": DEFAULT_MAX_CONCURRENT_STEPS_PER_RUN,
        "env": "DEVOS_AUTOMATION_MAX_PARALLEL",
        "ceiling": 32,
        "active": max_concurrent_steps_per_run(),
        "rationale": (
            "Bound fan-out within a single run to avoid unbounded job storms; "
            "not a cross-run fairness scheduler."
        ),
    }


def _status(rec: Optional[dict]) -> Optional[str]:
    if not isinstance(rec, dict):
        return None
    return rec.get("status")


def step_already_terminal(state: ExecutionState, step_id: str) -> bool:
    st = _status(state.records.get(step_id))
    return st in (STEP_SUCCEEDED, STEP_FAILED, STEP_DENIED, STEP_UNKNOWN, "skipped")


def active_running_count(state: ExecutionState) -> int:
    return sum(
        1
        for r in (state.records or {}).values()
        if isinstance(r, dict) and r.get("status") == STEP_RUNNING
    )


def select_schedulable_steps(
    snap: dict,
    state: ExecutionState,
    *,
    limit: Optional[int] = None,
) -> list[str]:
    """Eligible steps not yet terminal, capped by remaining concurrency budget."""
    if has_unresolved_unknown(state):
        return []
    elig = select_eligible_steps(snap, state)
    # Drop already terminal (idempotent)
    elig = [s for s in elig if not step_already_terminal(state, s)]
    cap = limit if limit is not None else max_concurrent_steps_per_run()
    running = active_running_count(state)
    remaining = max(0, cap - running)
    return elig[:remaining]


def merge_step_state(
    base: ExecutionState,
    partial: ExecutionState,
    step_id: str,
) -> ExecutionState:
    """Merge one step's outcome into the parent run state (branch independence)."""
    out = ExecutionState.from_dict(base.to_dict())
    if step_id in (partial.records or {}):
        out.records[step_id] = dict(partial.records[step_id])
    # Merge context for this step only
    pctx = partial.context or {}
    if step_id in pctx:
        out.context[step_id] = pctx[step_id]
    # Preserve branch decisions
    if "_branch_decisions" in pctx:
        bd = dict(out.context.get("_branch_decisions") or {})
        bd.update(pctx["_branch_decisions"] or {})
        out.context["_branch_decisions"] = bd
    # Track parallel metadata
    meta = dict(out.context.get("_parallel") or {})
    meta.setdefault("scheduled", [])
    if step_id not in meta["scheduled"]:
        meta["scheduled"] = list(meta["scheduled"]) + [step_id]
    meta["concurrency_limit"] = max_concurrent_steps_per_run()
    out.context["_parallel"] = meta
    # Clear false terminal if more work exists
    out.terminal_status = None
    out.error = None
    out.error_code = None
    return out


async def execute_one_step(
    snap: dict,
    state: ExecutionState,
    step_id: str,
    *,
    job_id: Optional[str] = None,
    extra_context: Optional[dict] = None,
) -> ExecutionState:
    """Execute a single step via the sole consequential executor (run_from_snapshot)."""
    if step_already_terminal(state, step_id):
        return state
    st = ExecutionState.from_dict(state.to_dict())
    st.current_step_id = step_id
    st.terminal_status = None
    # Mark running for concurrency accounting before await
    st.records[step_id] = {
        **(st.records.get(step_id) or {}),
        "step_id": step_id,
        "status": STEP_RUNNING,
    }
    result = await run_from_snapshot(
        snap,
        execution_state=st.to_dict(),
        job_id=job_id,
        max_steps=1,
        extra_context=extra_context or {},
    )
    return ExecutionState.from_dict(result.execution_state)


async def run_parallel_wave(
    snap: dict,
    state: ExecutionState,
    step_ids: list[str],
    *,
    job_id: Optional[str] = None,
    extra_context: Optional[dict] = None,
) -> ExecutionState:
    """Execute independent steps concurrently; merge durable step records."""
    if not step_ids:
        return state
    if len(step_ids) == 1:
        partial = await execute_one_step(
            snap, state, step_ids[0], job_id=job_id, extra_context=extra_context,
        )
        return merge_step_state(state, partial, step_ids[0])

    async def _one(sid: str) -> tuple[str, ExecutionState]:
        # Each branch gets an isolated view of parent state (no cross-write during run)
        local = ExecutionState.from_dict(state.to_dict())
        partial = await execute_one_step(
            snap, local, sid, job_id=job_id, extra_context=extra_context,
        )
        return sid, partial

    pairs = await asyncio.gather(*[_one(s) for s in step_ids], return_exceptions=True)
    merged = ExecutionState.from_dict(state.to_dict())
    for item in pairs:
        if isinstance(item, Exception):
            logger.error("parallel step exception: %s", item)
            continue
        sid, partial = item
        merged = merge_step_state(merged, partial, sid)
    return merged


def finalize_parallel_state(snap: dict, state: ExecutionState) -> tuple[str, bool, Optional[str]]:
    """Return (job_status, permanent, error) after waves complete."""
    if has_unresolved_unknown(state):
        return JOB_FAILED, True, state.error or "UNKNOWN_SIDE_EFFECT"
    elig = select_eligible_steps(snap, state)
    if elig:
        # Still work remaining (e.g. blocked by concurrency) — not terminal
        return "running", False, None
    # Check failures
    failed = any(
        isinstance(r, dict) and r.get("status") in (STEP_FAILED, STEP_DENIED)
        for r in (state.records or {}).values()
    )
    unknown = any(
        isinstance(r, dict) and r.get("status") == STEP_UNKNOWN
        for r in (state.records or {}).values()
    )
    if unknown:
        return JOB_FAILED, True, "UNKNOWN_SIDE_EFFECT"
    if failed:
        return JOB_FAILED, True, state.error or "step_failed"
    return JOB_SUCCEEDED, False, None


async def execute_parallel_graph(
    snap: dict,
    state: ExecutionState,
    *,
    job_id: Optional[str] = None,
    extra_context: Optional[dict] = None,
    max_waves: int = 100,
) -> ExecutionState:
    """Drive the graph with fan-out waves until no eligible work or UNKNOWN."""
    st = ExecutionState.from_dict(state.to_dict() if state else None)
    for _ in range(max_waves):
        if has_unresolved_unknown(st):
            break
        batch = select_schedulable_steps(snap, st)
        if not batch:
            break
        st = await run_parallel_wave(
            snap, st, batch, job_id=job_id, extra_context=extra_context,
        )
        # Safety: if a step is still RUNNING without progress, stop
        if active_running_count(st) and not any(
            _status(st.records.get(s)) in (STEP_SUCCEEDED, STEP_FAILED, STEP_DENIED, STEP_UNKNOWN)
            for s in batch
        ):
            break
    return st


def planned_operation_keys(
    *,
    run_id: str,
    workflow_version: int,
    step_ids: list[str],
    occurrence: int = 1,
) -> dict[str, str]:
    """Map step_id → logical operation idempotency key (duplicate schedule protection)."""
    return {
        sid: step_operation_idempotency_key(
            automation_run_id=run_id,
            workflow_version=workflow_version,
            step_id=sid,
            occurrence=occurrence,
        )
        for sid in step_ids
    }
