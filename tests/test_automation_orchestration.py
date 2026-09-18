"""Durable automation control-flow orchestration proofs."""
from __future__ import annotations

import pytest

from brain.workflow import Workflow, WorkflowStep, StepType
from brain.workflow_executor import (
    ExecutionState,
    STEP_SUCCEEDED,
    STEP_FAILED,
    STEP_UNKNOWN,
    STEP_RUNNING,
)
from brain.automation_orchestration import (
    select_eligible_steps,
    select_next_step,
    has_unresolved_unknown,
    evaluate_condition_expr,
    condition_language_spec,
    WorkflowEdge,
    EdgeOn,
)
from brain.flow_automation import (
    trigger_automation_run,
    reset_run_store_for_tests,
    set_workflow_enabled,
    RunStatus,
)
from brain.automation_runtime import execute_automation_run


@pytest.fixture
async def db(tmp_path, monkeypatch):
    url = f"sqlite+aiosqlite:///{(tmp_path / 'orch.db').as_posix()}"
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("REQUIRE_POSTGRES", "false")
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from core import database as dbmod
    dbmod.engine = create_async_engine(url, echo=False)
    dbmod.AsyncSessionLocal = async_sessionmaker(dbmod.engine, expire_on_commit=False)
    await dbmod.init_db()
    reset_run_store_for_tests()
    yield dbmod
    await dbmod.engine.dispose()


def _snap(wf: Workflow, **extra) -> dict:
    definition = wf.to_dict()
    definition.update(extra)
    return {
        "workflow_id": wf.workflow_id,
        "workflow_version": int(getattr(wf, "revision", 1) or 1),
        "schema_version": 1,
        "definition": definition,
        "owner_id": wf.owner_id,
        "name": wf.name,
    }


def test_success_route_a_to_b():
    wf = Workflow(
        workflow_id="w1", name="s", owner_id="u",
        steps=[
            WorkflowStep(id="A", type=StepType.TRANSFORM, name="a", next_step="B"),
            WorkflowStep(id="B", type=StepType.TRANSFORM, name="b"),
        ],
        start_step="A",
    )
    state = ExecutionState()
    assert select_next_step(_snap(wf), state) == "A"
    state.completed = ["A"]
    state.records = {"A": {"step_id": "A", "status": STEP_SUCCEEDED}}
    assert select_next_step(_snap(wf), state) == "B"


def test_failure_route_to_recovery():
    wf = Workflow(
        workflow_id="w1", name="s", owner_id="u",
        steps=[
            WorkflowStep(id="A", type=StepType.TRANSFORM, name="a", next_step="B", on_error="R"),
            WorkflowStep(id="B", type=StepType.TRANSFORM, name="b"),
            WorkflowStep(id="R", type=StepType.TRANSFORM, name="recovery"),
        ],
        start_step="A",
    )
    state = ExecutionState()
    state.records = {"A": {"step_id": "A", "status": STEP_FAILED}}
    elig = select_eligible_steps(_snap(wf), state)
    assert "R" in elig
    assert "B" not in elig


def test_condition_true_false_routes():
    wf = Workflow(
        workflow_id="w1", name="s", owner_id="u",
        steps=[
            WorkflowStep(
                id="C", type=StepType.CONDITION, name="cond",
                condition="flag",
                branches={"true": "B", "false": "D"},
            ),
            WorkflowStep(id="B", type=StepType.TRANSFORM, name="b"),
            WorkflowStep(id="D", type=StepType.TRANSFORM, name="d"),
        ],
        start_step="C",
    )
    snap = _snap(wf)
    state = ExecutionState()
    state.completed = ["C"]
    state.records = {"C": {"step_id": "C", "status": STEP_SUCCEEDED}}
    state.context = {"C": {"result": True}}
    assert select_next_step(snap, state) == "B"
    state.context = {"C": {"result": False}}
    assert select_next_step(snap, state) == "D"


def test_unknown_pauses_orchestration():
    state = ExecutionState()
    state.records = {"A": {"step_id": "A", "status": STEP_UNKNOWN, "side_effect": "external"}}
    assert has_unresolved_unknown(state)
    wf = Workflow(
        workflow_id="w1", name="s", owner_id="u",
        steps=[
            WorkflowStep(id="A", type=StepType.TRANSFORM, name="a", next_step="B"),
            WorkflowStep(id="B", type=StepType.TRANSFORM, name="b"),
        ],
        start_step="A",
    )
    assert select_eligible_steps(_snap(wf), state) == []


def test_all_success_join():
    wf = Workflow(
        workflow_id="w1", name="s", owner_id="u",
        steps=[
            WorkflowStep(id="A", type=StepType.TRANSFORM, name="a"),
            WorkflowStep(id="B", type=StepType.TRANSFORM, name="b"),
            WorkflowStep(id="C", type=StepType.TRANSFORM, name="c"),
            WorkflowStep(id="D", type=StepType.TRANSFORM, name="d"),
        ],
        start_step="A",
    )
    snap = _snap(
        wf,
        edges=[
            {"source": "A", "target": "B", "on": "success"},
            {"source": "A", "target": "C", "on": "success"},
            {"source": "B", "target": "D", "on": "success"},
            {"source": "C", "target": "D", "on": "success"},
        ],
        joins=[{"target": "D", "deps": ["B", "C"], "mode": "all_success"}],
    )
    state = ExecutionState()
    state.completed = ["A"]
    state.records = {"A": {"status": STEP_SUCCEEDED}}
    elig = select_eligible_steps(snap, state)
    assert "B" in elig and "C" in elig
    assert "D" not in elig
    state.completed = ["A", "B"]
    state.records["B"] = {"status": STEP_SUCCEEDED}
    elig = select_eligible_steps(snap, state)
    assert "D" not in elig
    state.completed = ["A", "B", "C"]
    state.records["C"] = {"status": STEP_SUCCEEDED}
    elig = select_eligible_steps(snap, state)
    assert "D" in elig


def test_any_success_join():
    wf = Workflow(
        workflow_id="w1", name="s", owner_id="u",
        steps=[
            WorkflowStep(id="A", type=StepType.TRANSFORM, name="a"),
            WorkflowStep(id="B", type=StepType.TRANSFORM, name="b"),
            WorkflowStep(id="C", type=StepType.TRANSFORM, name="c"),
            WorkflowStep(id="D", type=StepType.TRANSFORM, name="d"),
        ],
        start_step="A",
    )
    snap = _snap(
        wf,
        edges=[
            {"source": "A", "target": "B", "on": "success"},
            {"source": "A", "target": "C", "on": "success"},
        ],
        joins=[{"target": "D", "deps": ["B", "C"], "mode": "any_success"}],
    )
    state = ExecutionState()
    state.completed = ["A", "B"]
    state.records = {
        "A": {"status": STEP_SUCCEEDED},
        "B": {"status": STEP_SUCCEEDED},
    }
    assert "D" in select_eligible_steps(snap, state)


def test_deterministic_reload_same_eligible():
    wf = Workflow(
        workflow_id="w1", name="s", owner_id="u",
        steps=[
            WorkflowStep(id="A", type=StepType.TRANSFORM, name="a", next_step="B"),
            WorkflowStep(id="B", type=StepType.TRANSFORM, name="b", next_step="C"),
            WorkflowStep(id="C", type=StepType.TRANSFORM, name="c"),
        ],
        start_step="A",
    )
    snap = _snap(wf)
    state = ExecutionState()
    state.completed = ["A"]
    state.records = {"A": {"status": STEP_SUCCEEDED}}
    a = select_eligible_steps(snap, state)
    # Simulate reload from dict
    state2 = ExecutionState.from_dict(state.to_dict())
    b = select_eligible_steps(snap, state2)
    assert a == b == ["B"]


def test_condition_cannot_eval_host_code():
    ok, err = evaluate_condition_expr("__import__('os').system('x')", {})
    assert ok is False
    ok2, err2 = evaluate_condition_expr("eval('1')", {})
    assert ok2 is False
    spec = condition_language_spec()
    assert "eval" in str(spec["forbidden"])


def test_failed_without_failure_edge_stops():
    wf = Workflow(
        workflow_id="w1", name="s", owner_id="u",
        steps=[
            WorkflowStep(id="A", type=StepType.TRANSFORM, name="a", next_step="B"),
            WorkflowStep(id="B", type=StepType.TRANSFORM, name="b"),
        ],
        start_step="A",
    )
    state = ExecutionState()
    state.records = {"A": {"status": STEP_FAILED}}
    # No on_error → B not eligible via failure
    assert "B" not in select_eligible_steps(_snap(wf), state)


@pytest.mark.asyncio
async def test_runtime_success_route_integration(db):
    wf = Workflow(
        workflow_id="w-int", name="s", owner_id="user-1", version="1.0.0",
        steps=[
            WorkflowStep(id="A", type=StepType.TRANSFORM, name="a", inputs={"x": 1}, next_step="B"),
            WorkflowStep(id="B", type=StepType.TRANSFORM, name="b", inputs={"x": 2}),
        ],
        start_step="A",
        triggers=["manual"],
    )
    setattr(wf, "enabled", True)
    setattr(wf, "revision", 1)
    setattr(wf, "status", "published")
    setattr(wf, "tenant_id", "t1")
    run = await trigger_automation_run(
        wf, owner_id="user-1", tenant_id="t1", idempotency_key="orch-int-1",
    )
    done = await execute_automation_run(run.run_id, job_id=getattr(run, "execution_job_id"))
    assert done.status == RunStatus.SUCCEEDED
    assert set(done.result_summary.get("completed_step_ids") or []) >= {"A", "B"}


@pytest.mark.asyncio
async def test_join_orchestration_end_to_end(db):
    wf = Workflow(
        workflow_id="w-join", name="join", owner_id="user-1", version="1.0.0",
        steps=[
            WorkflowStep(id="A", type=StepType.TRANSFORM, name="a", inputs={}),
            WorkflowStep(id="B", type=StepType.TRANSFORM, name="b", inputs={}),
            WorkflowStep(id="C", type=StepType.TRANSFORM, name="c", inputs={}),
            WorkflowStep(id="D", type=StepType.TRANSFORM, name="d", inputs={}),
        ],
        start_step="A",
        triggers=["manual"],
    )
    setattr(wf, "enabled", True)
    setattr(wf, "revision", 1)
    setattr(wf, "status", "published")
    setattr(wf, "tenant_id", "t1")
    # Put edges/joins on definition via snapshot injection after trigger
    run = await trigger_automation_run(
        wf, owner_id="user-1", tenant_id="t1", idempotency_key="orch-join-1",
    )
    snap = getattr(run, "definition_snapshot", {}) or {}
    definition = dict(snap.get("definition") or {})
    definition["edges"] = [
        {"source": "A", "target": "B", "on": "success"},
        {"source": "A", "target": "C", "on": "success"},
        {"source": "B", "target": "D", "on": "success"},
        {"source": "C", "target": "D", "on": "success"},
    ]
    definition["joins"] = [{"target": "D", "deps": ["B", "C"], "mode": "all_success"}]
    snap = dict(snap)
    snap["definition"] = definition
    setattr(run, "definition_snapshot", snap)
    from brain.flow_automation import persist_run_durable
    await persist_run_durable(run)

    done = await execute_automation_run(run.run_id, job_id=getattr(run, "execution_job_id"))
    assert done.status == RunStatus.SUCCEEDED
    completed = set(done.result_summary.get("completed_step_ids") or [])
    assert completed >= {"A", "B", "C", "D"}


@pytest.mark.asyncio
async def test_disabled_cannot_orchestrate(db):
    wf = Workflow(
        workflow_id="w-d", name="d", owner_id="user-1",
        steps=[WorkflowStep(id="A", type=StepType.TRANSFORM, name="a")],
        start_step="A",
    )
    setattr(wf, "enabled", False)
    setattr(wf, "revision", 1)
    with pytest.raises(PermissionError, match="automation_disabled"):
        await trigger_automation_run(
            wf, owner_id="user-1", tenant_id="t1", idempotency_key="orch-dis",
        )


def test_retry_identity_helper_distinct_attempts():
    from brain.automation_runtime import step_operation_idempotency_key
    k1 = step_operation_idempotency_key(
        automation_run_id="r", workflow_version=1, step_id="A", occurrence=1,
    )
    k2 = step_operation_idempotency_key(
        automation_run_id="r", workflow_version=1, step_id="A", occurrence=2,
    )
    assert k1 != k2
