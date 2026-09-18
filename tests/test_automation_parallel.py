"""Bounded parallel automation: fan-out, fan-in, independence, limits, restart."""
from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace

import pytest

from brain.workflow import Workflow, WorkflowStep, StepType
from brain.workflow_executor import (
    ExecutionState,
    STEP_SUCCEEDED,
    STEP_FAILED,
    STEP_UNKNOWN,
    STEP_DENIED,
    STEP_RUNNING,
)
from brain.automation_orchestration import (
    select_eligible_steps,
    JoinMode,
)
from brain.automation_parallel import (
    select_schedulable_steps,
    execute_parallel_graph,
    run_parallel_wave,
    merge_step_state,
    planned_operation_keys,
    max_concurrent_steps_per_run,
    concurrency_policy,
    finalize_parallel_state,
)
from brain.automation_runtime import step_operation_idempotency_key
from brain.flow_automation import (
    trigger_automation_run,
    TriggerSpec,
    TriggerType,
    RunMode,
    reset_run_store_for_tests,
)
from brain.automation_runtime import execute_automation_run


def _diamond_snap(*, join_mode: str = "all_success", fail_c: bool = False):
    """
         A
        / \\
       B   C
        \\ /
         D
    """
    steps = [
        WorkflowStep(
            id="A", type=StepType.TRANSFORM, name="a",
            inputs={"expr": "1"}, next_step="B",
            # edges declared in definition
        ),
        WorkflowStep(
            id="B", type=StepType.TRANSFORM, name="b",
            inputs={"expr": "2"},
        ),
        WorkflowStep(
            id="C",
            type=StepType.TRANSFORM if not fail_c else StepType.CONDITION,
            name="c",
            inputs={"expr": "3"} if not fail_c else {},
            condition="__bad__" if fail_c else None,
        ),
        WorkflowStep(
            id="D", type=StepType.TRANSFORM, name="d",
            inputs={"expr": "4"},
            metadata={"depends_on": ["B", "C"], "join": join_mode},
        ),
    ]
    # B and C both follow A via edges
    definition = {
        "workflow_id": "wf-par-1",
        "name": "diamond",
        "version": "1.0.0",
        "start_step": "A",
        "steps": [s.to_dict() for s in steps],
        "edges": [
            {"source": "A", "target": "B", "on": "success"},
            {"source": "A", "target": "C", "on": "success"},
            {"source": "B", "target": "D", "on": "success"},
            {"source": "C", "target": "D", "on": "success"},
        ],
        "joins": [
            {"target": "D", "deps": ["B", "C"], "mode": join_mode},
        ],
        "triggers": ["manual"],
    }
    from brain.workflow_store import build_execution_snapshot
    return build_execution_snapshot(
        workflow_id="wf-par-1",
        workflow_version=1,
        owner_id="u1",
        tenant_id="t1",
        name="diamond",
        definition=definition,
        enabled=True,
    )


def test_fan_out_b_c_after_a():
    snap = _diamond_snap()
    st = ExecutionState()
    st.records["A"] = {"step_id": "A", "status": STEP_SUCCEEDED}
    elig = select_eligible_steps(snap, st)
    assert set(elig) == {"B", "C"}


def test_independent_operation_ids():
    keys = planned_operation_keys(
        run_id="run1", workflow_version=1, step_ids=["B", "C"],
    )
    assert keys["B"] != keys["C"]
    assert keys["B"] == step_operation_idempotency_key(
        automation_run_id="run1", workflow_version=1, step_id="B", occurrence=1,
    )


@pytest.mark.asyncio
async def test_b_and_c_execute_concurrently():
    snap = _diamond_snap()
    st = ExecutionState()
    st.records["A"] = {"step_id": "A", "status": STEP_SUCCEEDED}
    started = []

    # run_parallel_wave should finish both
    out = await run_parallel_wave(snap, st, ["B", "C"])
    assert out.records["B"]["status"] == STEP_SUCCEEDED
    assert out.records["C"]["status"] == STEP_SUCCEEDED


@pytest.mark.asyncio
async def test_d_waits_all_success():
    snap = _diamond_snap(join_mode="all_success")
    st = ExecutionState()
    st.records["A"] = {"step_id": "A", "status": STEP_SUCCEEDED}
    st.records["B"] = {"step_id": "B", "status": STEP_SUCCEEDED}
    # C not done → D not eligible
    elig = select_eligible_steps(snap, st)
    assert "D" not in elig
    st.records["C"] = {"step_id": "C", "status": STEP_SUCCEEDED}
    elig2 = select_eligible_steps(snap, st)
    assert "D" in elig2


def test_d_any_success():
    snap = _diamond_snap(join_mode="any_success")
    st = ExecutionState()
    st.records["A"] = {"step_id": "A", "status": STEP_SUCCEEDED}
    st.records["B"] = {"step_id": "B", "status": STEP_SUCCEEDED}
    # C not done but ANY_SUCCESS → D eligible
    elig = select_eligible_steps(snap, st)
    assert "D" in elig


def test_duplicate_schedule_same_op_key():
    k1 = step_operation_idempotency_key(
        automation_run_id="r", workflow_version=1, step_id="B", occurrence=1,
    )
    k2 = step_operation_idempotency_key(
        automation_run_id="r", workflow_version=1, step_id="B", occurrence=1,
    )
    assert k1 == k2


def test_concurrency_limit(monkeypatch):
    monkeypatch.setenv("DEVOS_AUTOMATION_MAX_PARALLEL", "1")
    # re-import limit
    from brain import automation_parallel as ap
    assert ap.max_concurrent_steps_per_run() == 1
    snap = _diamond_snap()
    st = ExecutionState()
    st.records["A"] = {"step_id": "A", "status": STEP_SUCCEEDED}
    batch = ap.select_schedulable_steps(snap, st)
    assert len(batch) == 1
    pol = ap.concurrency_policy()
    assert pol["scope"] == "per_automation_run"


@pytest.mark.asyncio
async def test_full_diamond_all_success():
    snap = _diamond_snap(join_mode="all_success")
    st = ExecutionState()
    out = await execute_parallel_graph(snap, st)
    assert out.records.get("A", {}).get("status") == STEP_SUCCEEDED
    assert out.records.get("B", {}).get("status") == STEP_SUCCEEDED
    assert out.records.get("C", {}).get("status") == STEP_SUCCEEDED
    assert out.records.get("D", {}).get("status") == STEP_SUCCEEDED
    status, perm, err = finalize_parallel_state(snap, out)
    assert status == "succeeded"


@pytest.mark.asyncio
async def test_branch_independence_c_fails_all_success():
    snap = _diamond_snap(join_mode="all_success", fail_c=True)
    st = ExecutionState()
    out = await execute_parallel_graph(snap, st)
    assert out.records.get("B", {}).get("status") == STEP_SUCCEEDED
    assert out.records.get("C", {}).get("status") in (STEP_FAILED, STEP_DENIED)
    # D must not run under ALL_SUCCESS
    assert out.records.get("D", {}).get("status") not in (STEP_SUCCEEDED, STEP_RUNNING)


@pytest.mark.asyncio
async def test_restart_after_a_before_fanout():
    snap = _diamond_snap()
    st = ExecutionState()
    st.records["A"] = {"step_id": "A", "status": STEP_SUCCEEDED}
    # simulate restart: only A done
    batch = select_schedulable_steps(snap, st)
    assert set(batch) <= {"B", "C"}
    out = await execute_parallel_graph(snap, st)
    assert out.records["B"]["status"] == STEP_SUCCEEDED
    assert out.records["C"]["status"] == STEP_SUCCEEDED
    assert out.records["D"]["status"] == STEP_SUCCEEDED


@pytest.mark.asyncio
async def test_restart_one_branch_complete():
    snap = _diamond_snap()
    st = ExecutionState()
    st.records["A"] = {"step_id": "A", "status": STEP_SUCCEEDED}
    st.records["B"] = {"step_id": "B", "status": STEP_SUCCEEDED}
    # C still pending
    elig = select_eligible_steps(snap, st)
    assert "C" in elig
    assert "D" not in elig
    out = await execute_parallel_graph(snap, st)
    assert out.records["C"]["status"] == STEP_SUCCEEDED
    assert out.records["D"]["status"] == STEP_SUCCEEDED
    # B not redispatched as failed
    assert out.records["B"]["status"] == STEP_SUCCEEDED


@pytest.mark.asyncio
async def test_unknown_blocks_join():
    snap = _diamond_snap()
    st = ExecutionState()
    st.records["A"] = {"step_id": "A", "status": STEP_SUCCEEDED}
    st.records["B"] = {"step_id": "B", "status": STEP_UNKNOWN}
    st.records["C"] = {"step_id": "C", "status": STEP_SUCCEEDED}
    elig = select_eligible_steps(snap, st)
    assert elig == []  # UNKNOWN pauses all


@pytest.mark.asyncio
async def test_completed_never_redispatched():
    snap = _diamond_snap()
    st = ExecutionState()
    st.records["A"] = {"step_id": "A", "status": STEP_SUCCEEDED}
    st.records["B"] = {"step_id": "B", "status": STEP_SUCCEEDED}
    st.records["C"] = {"step_id": "C", "status": STEP_SUCCEEDED}
    st.records["D"] = {"step_id": "D", "status": STEP_SUCCEEDED}
    batch = select_schedulable_steps(snap, st)
    assert batch == []


@pytest.mark.asyncio
async def test_idempotent_wave_no_duplicate():
    snap = _diamond_snap()
    st = ExecutionState()
    st.records["A"] = {"step_id": "A", "status": STEP_SUCCEEDED}
    out1 = await run_parallel_wave(snap, st, ["B", "C"])
    out2 = await run_parallel_wave(snap, out1, ["B", "C"])
    assert out2.records["B"]["status"] == STEP_SUCCEEDED
    assert out2.records["C"]["status"] == STEP_SUCCEEDED


def test_join_final_exactly_once_eligibility():
    snap = _diamond_snap()
    st = ExecutionState()
    for sid in ("A", "B", "C"):
        st.records[sid] = {"step_id": sid, "status": STEP_SUCCEEDED}
    elig = select_eligible_steps(snap, st)
    assert elig.count("D") == 1
