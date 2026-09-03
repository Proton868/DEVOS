"""Stage 3J — HAI control plane + AgentRuntime unification tests."""
from __future__ import annotations

import pytest
from cognitive.hai_control import (
    MAX_SAME_ACTION_STREAK,
    MAX_STRATEGIC_REPLANS,
    MAX_TACTICAL_RECOVERIES,
    StrategicController,
    StrategicDecision,
    classify_delegation,
    derive_subgoals,
)


def test_strategic_plan_creates_subgoals():
    s = StrategicController()
    out = s.start("Fix the failing authentication test")
    assert out["decision"] in (StrategicDecision.CONTINUE.value, StrategicDecision.DELEGATE_WORKFLOW.value)
    assert out["subgoals"]
    assert s.control.current_subgoal_id


def test_max_steps_not_success_in_source():
    src = open("brain/agent_runtime.py").read()
    assert "max_steps_reached" in src
    assert "AgentTaskStatus.BLOCKED" in src
    # Must not set SUCCEEDED solely for max steps
    assert "Reached maximum tool steps. Partial progress may exist." not in src or "BLOCKED" in src
    assert "success\": False" in src or "success': False" in src or "success\": False" in src


def test_repeated_action_loop_blocks():
    s = StrategicController()
    s.start("Investigate repo")
    # Force same action streak
    for _ in range(MAX_SAME_ACTION_STREAK):
        out = s.on_tool_result("list_files", True)
    assert out["decision"] == StrategicDecision.BLOCK.value
    assert out["reason_code"] == "repeated_action_loop"


def test_tactical_recovery_then_replan():
    s = StrategicController()
    s.start("Fix bug")
    for i in range(MAX_TACTICAL_RECOVERIES + 1):
        out = s.on_tool_result("run_tests", False, summary=f"fail {i}")
    assert out["decision"] in (StrategicDecision.REPLAN.value, StrategicDecision.BLOCK.value)


def test_replan_budget_bounded():
    s = StrategicController()
    s.start("Fix bug")
    for i in range(MAX_STRATEGIC_REPLANS + 2):
        s.control.tactical_recovery_count = MAX_TACTICAL_RECOVERIES + 1
        out = s.on_tool_result("run_tests", False, summary=f"x{i}")
        if out["decision"] == StrategicDecision.BLOCK.value and out["reason_code"] == "max_replans":
            break
    assert out["decision"] == StrategicDecision.BLOCK.value


def test_edit_requires_verify_decision():
    s = StrategicController()
    s.start("Implement feature X")
    out = s.on_tool_result("apply_patch", True, summary="patched")
    assert out["decision"] == StrategicDecision.VERIFY.value


def test_delegation_workflow_signal():
    assert classify_delegation("Run the nightly workflow pipeline") == StrategicDecision.DELEGATE_WORKFLOW


def test_delegation_coordinator_signal():
    assert classify_delegation("Use multi-worker coordinator for parallel analysis") == StrategicDecision.DELEGATE_COORDINATOR


def test_checkpoint_roundtrip():
    s = StrategicController()
    s.start("Fix auth test")
    cp = s.control.checkpoint("task-1", correlation_id="c1", state_version=2)
    d = cp.to_dict()
    from cognitive.hai_checkpoint import HAICheckpoint
    restored = HAICheckpoint.from_dict(d, verify=True)
    assert restored.task_id == "task-1"
    assert "user_id" not in d


def test_derive_subgoals_dag_shape():
    subs = derive_subgoals("Fix login test failure")
    assert subs[0].depends_on == []
    assert all(isinstance(s.depends_on, list) for s in subs)


def test_agent_runtime_imports_hai():
    src = open("brain/agent_runtime.py").read()
    assert "StrategicController" in src
    assert "strategic_plan_created" in src
    assert "persist_hai_checkpoint" in src


def test_coordinator_not_duplicated():
    """Coordinator remains the multi-worker path — not reimplemented in hai_control."""
    src = open("cognitive/hai_control.py").read()
    assert "WorkerRuntime" not in src
    assert "subprocess" not in src
    assert "GoalDecomposer" not in src or "compatible" in src.lower()  # may mention
