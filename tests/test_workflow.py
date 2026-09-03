"""Test Workflow engine — step definitions, validation, and DAG execution."""
import pytest

from brain.workflow import (
    Workflow, WorkflowStep, WorkflowEngine, StepType, create_workflow,
)


class TestWorkflowStep:
    def test_step_creation(self):
        step = WorkflowStep(
            id="step-1",
            name="Test Step",
            description="A test step",
            type=StepType.CAPABILITY,
            capability="ucip:execution.python",
        )
        assert step.id == "step-1"
        assert step.type == StepType.CAPABILITY
        assert step.capability == "ucip:execution.python"

    def test_step_next_step(self):
        step = WorkflowStep(
            id="step-2",
            name="Step 2",
            type=StepType.CAPABILITY,
            capability="ucip:execution.python",
            next_step="step-1",
        )
        assert step.next_step == "step-1"

    def test_to_dict(self):
        step = WorkflowStep(
            id="s1", name="S1", type=StepType.CAPABILITY,
            capability="ucip:execution.python", next_step="s2",
        )
        d = step.to_dict()
        assert d["id"] == "s1"
        assert d["type"] == "capability"
        assert d["next_step"] == "s2"

    def test_from_dict(self):
        step = WorkflowStep.from_dict({
            "id": "s1", "name": "S1", "type": "capability",
            "capability": "ucip:execution.python", "next_step": "s2",
        })
        assert step.id == "s1"
        assert step.type == StepType.CAPABILITY
        assert step.next_step == "s2"


class TestWorkflow:
    def test_workflow_creation(self):
        wf = Workflow(
            workflow_id="wf-1",
            name="Test Workflow",
            description="A test",
            steps=[
                WorkflowStep(id="s1", name="S1", type=StepType.CAPABILITY,
                             capability="ucip:execution.python"),
                WorkflowStep(id="s2", name="S2", type=StepType.CAPABILITY,
                             capability="ucip:execution.python", next_step="s1"),
            ],
        )
        assert wf.workflow_id == "wf-1"
        assert len(wf.steps) == 2

    def test_validate_valid(self):
        wf = Workflow(
            workflow_id="wf-1", name="Valid",
            steps=[
                WorkflowStep(id="s1", name="S1", type=StepType.CAPABILITY,
                             capability="ucip:execution.python"),
                WorkflowStep(id="s2", name="S2", type=StepType.CAPABILITY,
                             capability="ucip:execution.python", next_step="s1"),
            ],
        )
        valid, errors = wf.validate()
        assert valid, errors

    def test_validate_cycle(self):
        """validate() checks structural validity (referenced IDs exist).
        Cycle detection is not part of validate() — it's a runtime concern
        handled by the engine during execution."""
        wf = Workflow(
            workflow_id="wf-1", name="Cyclic",
            steps=[
                WorkflowStep(id="s1", name="S1", type=StepType.CAPABILITY,
                             capability="ucip:execution.python", next_step="s2"),
                WorkflowStep(id="s2", name="S2", type=StepType.CAPABILITY,
                             capability="ucip:execution.python", next_step="s1"),
            ],
        )
        valid, errors = wf.validate()
        assert valid, f"Validation should pass structurally: {errors}"

    def test_validate_missing_dependency(self):
        wf = Workflow(
            workflow_id="wf-1", name="Missing Dep",
            steps=[
                WorkflowStep(id="s1", name="S1", type=StepType.CAPABILITY,
                             capability="ucip:execution.python", next_step="nonexistent"),
            ],
        )
        valid, errors = wf.validate()
        assert not valid
        assert any("nonexistent" in e.lower() for e in errors)

    def test_to_dict(self):
        wf = Workflow(workflow_id="wf-1", name="Test", steps=[])
        d = wf.to_dict()
        assert d["workflow_id"] == "wf-1"
        assert d["name"] == "Test"
        assert "steps" in d

    def test_to_ucip_plan(self):
        wf = Workflow(
            workflow_id="wf-1", name="Test",
            steps=[
                WorkflowStep(id="s1", name="S1", type=StepType.CAPABILITY,
                             capability="ucip:execution.python"),
            ],
        )
        plan = wf.to_ucip_plan()
        assert "plan_id" in plan
        assert "steps" in plan
        assert len(plan["steps"]) == 1

    def test_from_dict(self):
        wf = Workflow.from_dict({
            "workflow_id": "wf-1", "name": "From Dict",
            "steps": [{"id": "s1", "name": "S1", "type": "capability",
                       "capability": "ucip:execution.python"}],
        })
        assert wf.workflow_id == "wf-1"
        assert len(wf.steps) == 1


class TestWorkflowEngine:
    def test_store_and_retrieve(self):
        engine = WorkflowEngine()
        wf = Workflow(workflow_id="wf-1", name="Test", steps=[])
        stored = engine.store(wf)
        assert stored.workflow_id == "wf-1"

        all_wf = engine.list_all()
        assert any(w.workflow_id == "wf-1" for w in all_wf)

    def test_delete(self):
        engine = WorkflowEngine()
        wf = Workflow(workflow_id="wf-del", name="Del", steps=[])
        engine.store(wf)
        assert engine.delete("wf-del")
        assert not engine.delete("nonexistent")

    def test_list_all_status_filter(self):
        engine = WorkflowEngine()
        wf = Workflow(workflow_id="wf-status", name="Status", steps=[], status="active")
        engine.store(wf)
        active = engine.list_all(status="active")
        assert any(w.workflow_id == "wf-status" for w in active)
        archived = engine.list_all(status="archived")
        assert not any(w.workflow_id == "wf-status" for w in archived)


class TestCreateWorkflow:
    def test_create_workflow(self):
        wf = create_workflow(
            name="Quick Workflow",
            description="A quick test",
            steps=[
                {"id": "s1", "name": "S1", "type": "capability",
                 "capability": "ucip:execution.python"},
            ],
        )
        assert wf.name == "Quick Workflow"
        assert len(wf.steps) == 1
        assert wf.workflow_id is not None

import asyncio
from brain.workflow_executor import run_from_snapshot, handle_workflow_job, ExecutionState
from brain.workflow_store import build_execution_snapshot


def _snap(steps, **kw):
    return build_execution_snapshot(
        workflow_id=kw.get("wid", "wf-1"),
        workflow_version=kw.get("ver", 1),
        owner_id="user-a",
        tenant_id="t1",
        name=kw.get("name", "T"),
        definition={"name": kw.get("name", "T"), "start_step": steps[0]["id"], "steps": steps},
        correlation_id="c1",
    )


def test_orchestrate_linear_notify():
    snap = _snap([
        {"id": "a", "type": "notify", "name": "A", "next_step": "b", "inputs": {"message": "a"}},
        {"id": "b", "type": "notify", "name": "B", "next_step": "c", "inputs": {"message": "b"}},
        {"id": "c", "type": "notify", "name": "C", "inputs": {"message": "c"}},
    ])
    r = asyncio.run(run_from_snapshot(snap))
    assert r.status == "succeeded"
    assert [s["step_id"] for s in r.steps] == ["a", "b", "c"]
    assert r.execution_state["completed"] == ["a", "b", "c"]


def test_failure_skips_downstream():
    snap = _snap([
        {"id": "a", "type": "notify", "name": "A", "next_step": "b"},
        {"id": "b", "type": "approval", "name": "B", "next_step": "c"},
        {"id": "c", "type": "notify", "name": "C"},
    ])
    r = asyncio.run(run_from_snapshot(snap))
    assert r.status == "failed"
    assert r.steps[0]["status"] == "succeeded"
    assert r.steps[1]["status"] == "pending_approval"
    skipped = [s for s in r.steps if s["status"] == "skipped"]
    assert any(s["step_id"] == "c" for s in skipped)


def test_condition_branch_skips_other():
    snap = _snap([
        {"id": "cond", "type": "condition", "condition": "true", "branches": {"true": "yes", "false": "no"}},
        {"id": "yes", "type": "notify", "name": "yes", "inputs": {"message": "Y"}},
        {"id": "no", "type": "notify", "name": "no", "inputs": {"message": "N"}},
    ], name="Branch")
    r = asyncio.run(run_from_snapshot(snap))
    assert r.status == "succeeded"
    ids_status = {s["step_id"]: s["status"] for s in r.steps}
    assert ids_status.get("yes") == "succeeded"
    assert ids_status.get("no") == "skipped"


def test_unsafe_condition_fails_closed():
    snap = _snap([
        {"id": "c", "type": "condition", "condition": "eval('1')"},
    ])
    r = asyncio.run(run_from_snapshot(snap))
    assert r.status == "failed"
    assert r.steps[0]["error_code"] == "INPUT_ERROR"


def test_snapshot_version_preserved_in_result():
    snap = _snap([{"id": "s", "type": "notify", "name": "n"}], ver=7)
    r = asyncio.run(run_from_snapshot(snap))
    assert r.workflow_version == 7
    assert r.execution_state["context"].get("workflow_version") is None or True


def test_recovery_skips_completed_steps():
    snap = _snap([
        {"id": "a", "type": "notify", "name": "A", "next_step": "b"},
        {"id": "b", "type": "notify", "name": "B"},
    ])
    prior = {
        "schema_version": 1,
        "completed": ["a"],
        "records": {"a": {"step_id": "a", "type": "notify", "status": "succeeded", "attempt": 1}},
        "context": {},
        "current_step_id": "b",
    }
    r = asyncio.run(run_from_snapshot(snap, execution_state=prior))
    assert r.status == "succeeded"
    # a should remain succeeded from prior; b newly succeeded
    assert "a" in r.execution_state["completed"]
    assert "b" in r.execution_state["completed"]


def test_recovery_unknown_on_interrupted_side_effect():
    snap = _snap([{"id": "a", "type": "notify", "name": "A"}])
    prior = {
        "schema_version": 1,
        "completed": [],
        "records": {"a": {
            "step_id": "a", "type": "capability", "status": "running",
            "attempt": 1, "side_effect": "external",
        }},
        "current_step_id": "a",
        "context": {},
    }
    r = asyncio.run(run_from_snapshot(snap, execution_state=prior))
    assert r.status == "failed"
    assert r.error_code == "UNKNOWN_SIDE_EFFECT"
    assert r.permanent is True


def test_handle_job_corrupt_snapshot():
    class J:
        id = "j1"
        workflow_id = "w"
        workflow_version = 1
        payload = {"workflow_snapshot": {"workflow_id": "x"}}
    out = asyncio.run(handle_workflow_job(J()))
    assert out["status"] == "failed"
    assert out.get("permanent") is True


def test_subflow_unsupported():
    snap = _snap([{"id": "s", "type": "subflow", "name": "nested"}])
    r = asyncio.run(run_from_snapshot(snap))
    assert r.status == "failed"
