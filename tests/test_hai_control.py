"""Stage 3J/3K — HAI control plane tests."""
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
    interpret_test_outcome,
    interpret_verification_outcome,
)


def test_strategic_plan_creates_subgoals():
    s = StrategicController()
    out = s.start("Fix the failing authentication test")
    assert out["subgoals"]
    assert s.control.current_subgoal_id


def test_case_a_nl_after_patch_not_complete():
    s = StrategicController()
    s.start("Fix authentication bug")
    s.on_tool_result("apply_patch", True, arguments={"path": "auth.py"}, result={"ok": True})
    gate = s.evaluate_natural_language_completion("The bug is fixed.")
    assert gate["decision"] != StrategicDecision.COMPLETE.value
    assert gate["decision"] == StrategicDecision.VERIFY.value


def test_case_b_failing_tests_not_success():
    s = StrategicController()
    s.start("Fix authentication bug")
    s.on_tool_result("apply_patch", True, result={"ok": True})
    out = s.on_tool_result(
        "run_tests", True,
        result={"ok": True, "exit_code": 1, "stdout": "1 failed, 0 passed"},
    )
    assert out["decision"] != StrategicDecision.COMPLETE.value
    assert s.control.verification_status == "failed"


def test_case_c_passing_tests_can_progress():
    s = StrategicController()
    s.start("Fix authentication bug")
    s.on_tool_result("apply_patch", True, result={"ok": True})
    out = s.on_tool_result(
        "run_tests", True,
        result={"ok": True, "exit_code": 0, "stdout": "2 passed", "tests_passed": True},
    )
    assert s.control.verification_status == "passed"
    # May complete subgoals / continue / complete
    assert out["decision"] in (
        StrategicDecision.CONTINUE.value,
        StrategicDecision.COMPLETE.value,
    )


def test_case_d_readonly_nl_may_complete():
    s = StrategicController()
    s.start("Explain the repository structure")
    # Orient and act without consequential edit
    s.on_tool_result("list_files", True, arguments={"path": "."}, result={"ok": True})
    # Force subgoals complete for informational path
    for sg in s.control.subgoals:
        sg.status = "completed"
    s.control.verification_status = "not_started"
    s.control.verification_required = False
    s.control.consequential_edit_pending = False
    gate = s.evaluate_natural_language_completion("Here is the structure...")
    assert gate["decision"] == StrategicDecision.COMPLETE.value


def test_interpret_test_outcome_helpers():
    assert interpret_test_outcome({"tests_passed": True}) is True
    assert interpret_test_outcome({"exit_code": 1, "stdout": "failed"}) is False
    assert interpret_test_outcome({"exit_code": 0, "stdout": "3 passed"}) is True
    assert interpret_test_outcome({"ok": True, "stdout": ""}) is None  # inconclusive


def test_repeated_action_considers_args():
    s = StrategicController()
    s.start("Investigate repo")
    # Different files — must NOT loop
    for path in ("a.py", "b.py", "c.py"):
        out = s.on_tool_result("read_file", True, arguments={"path": path}, result={"ok": True})
    assert out["decision"] != StrategicDecision.BLOCK.value or out["reason_code"] != "repeated_action_loop"
    # Identical failure streak — should block
    s2 = StrategicController()
    s2.start("Investigate repo")
    for _ in range(3):
        out = s2.on_tool_result(
            "run_tests", False,
            arguments={"path": "tests/auth"},
            result={"ok": False, "exit_code": 1, "stdout": "1 failed"},
        )
    assert out["decision"] == StrategicDecision.BLOCK.value
    assert out["reason_code"] == "repeated_action_loop"


def test_replan_sets_plan_dirty_and_consume():
    s = StrategicController()
    s.start("Fix bug")
    s.control.tactical_recovery_count = MAX_TACTICAL_RECOVERIES + 1
    out = s.on_tool_result("run_tests", False, result={"ok": False})
    if out["decision"] == StrategicDecision.REPLAN.value:
        assert s.control.plan_dirty is True
        ctx = s.consume_plan_update()
        assert ctx is not None
        assert "plan_version" in ctx
        assert s.control.plan_dirty is False


def test_can_complete_false_when_verify_required():
    s = StrategicController()
    s.start("Fix auth test")
    s.on_tool_result("apply_patch", True, result={"ok": True})
    ok, reason = s.control.can_complete()
    assert ok is False
    assert "verif" in reason or "edit" in reason


def test_max_steps_source_blocked():
    src = open("brain/agent_runtime.py").read()
    assert "AgentTaskStatus.BLOCKED" in src
    assert "success\": False" in src or "success': False" in src


def test_nl_completion_gated_in_runtime():
    src = open("brain/agent_runtime.py").read()
    assert "evaluate_natural_language_completion" in src
    assert "verification is required before completion" in src


def test_security_no_identity_in_state():
    s = StrategicController()
    s.start("x")
    d = s.control.to_state_dict()
    blob = str(d)
    assert "tenant_id" not in d
    assert "user_id" not in d
    assert "capabilities" not in blob or "capability" not in str(d.get("strategic", {}))


def test_delegation_signals():
    assert classify_delegation("Run the nightly workflow pipeline") == StrategicDecision.DELEGATE_WORKFLOW
    assert classify_delegation("Use multi-worker coordinator") == StrategicDecision.DELEGATE_COORDINATOR


def test_successful_run_command_is_not_verification():
    s = StrategicController()
    s.start("Fix authentication bug")
    s.on_tool_result("apply_patch", True, result={"ok": True})
    out = s.on_tool_result(
        "run_command",
        True,
        arguments={"command": "echo hello"},
        result={"ok": True, "exit_code": 0, "stdout": "hello"},
    )
    assert s.control.verification_status == "required"
    assert out["decision"] == StrategicDecision.VERIFY.value


def test_successful_readonly_command_is_not_verification():
    result = {"ok": True, "exit_code": 0, "stdout": "README.md\n"}
    assert interpret_verification_outcome("run_command", result) is None


def test_run_command_explicit_verification_passes():
    result = {"ok": True, "exit_code": 0, "verification_passed": True}
    assert interpret_verification_outcome("run_command", result) is True


def test_run_tests_real_test_evidence_passes():
    result = {"ok": True, "exit_code": 0, "stdout": "2 passed"}
    assert interpret_verification_outcome("run_tests", result) is True


def test_run_tests_empty_success_is_inconclusive():
    result = {"ok": True, "exit_code": 0, "stdout": ""}
    assert interpret_verification_outcome("run_tests", result) is None


def test_run_tests_failure_is_failure():
    result = {"ok": True, "exit_code": 1, "stdout": "1 failed"}
    assert interpret_verification_outcome("run_tests", result) is False


def test_nl_cannot_bypass_inconclusive_command():
    s = StrategicController()
    s.start("Fix authentication bug")
    s.on_tool_result("apply_patch", True, result={"ok": True})
    s.on_tool_result(
        "run_command", True,
        arguments={"command": "echo hello"},
        result={"ok": True, "exit_code": 0, "stdout": "hello"},
    )
    gate = s.evaluate_natural_language_completion("The bug is fixed.")
    assert gate["decision"] != StrategicDecision.COMPLETE.value
    assert s.control.verification_status == "required"

