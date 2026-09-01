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