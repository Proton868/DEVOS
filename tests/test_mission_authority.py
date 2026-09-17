"""Mission state authority — transitions, sticky terminals, projections."""
from __future__ import annotations

import pytest

from brain.mission_checkpoint import (
    MissionCheckpoint,
    MissionLifecycle,
    create_checkpoint,
    can_transition,
    TERMINAL,
)
from brain.mission_authority import (
    ownership_matrix,
    transition,
    declare_mission_outcome,
    project_for_sse,
    child_cannot_complete_mission,
    MissionAuthorityError,
    is_sticky_terminal,
)


def _cp(status=MissionLifecycle.QUEUED, **kw) -> MissionCheckpoint:
    cp = create_checkpoint(
        mission_id=kw.get("mission_id", "ma-1"),
        user_id=kw.get("user_id", "u1"),
        goal=kw.get("goal", "test"),
    )
    cp.status = status
    return cp


def test_ownership_matrix_covers_core_concepts():
    rows = ownership_matrix()
    concepts = {r["concept"] for r in rows}
    for c in (
        "mission_lifecycle",
        "coding_loop_acceptance",
        "agent_task",
        "provider_attempts",
        "evidence_acceptance",
        "sse_frontend",
        "execution_job",
    ):
        assert c in concepts
    life = next(r for r in rows if r["concept"] == "mission_lifecycle")
    assert life["independent_terminal"] is True
    loop = next(r for r in rows if r["concept"] == "coding_loop_acceptance")
    assert loop["independent_terminal"] is False


def test_completed_and_cancelled_are_sticky():
    assert is_sticky_terminal(MissionLifecycle.COMPLETED)
    assert is_sticky_terminal(MissionLifecycle.CANCELLED)
    assert not is_sticky_terminal(MissionLifecycle.FAILED)
    assert can_transition(MissionLifecycle.COMPLETED, MissionLifecycle.EXECUTING) is False
    assert can_transition(MissionLifecycle.CANCELLED, MissionLifecycle.COMPLETED) is False


def test_failed_cannot_silently_become_completed():
    assert can_transition(MissionLifecycle.FAILED, MissionLifecycle.COMPLETED) is False
    cp = _cp(MissionLifecycle.FAILED)
    d = declare_mission_outcome(cp, desired=MissionLifecycle.COMPLETED, acceptance={"ok": True})
    assert d.rejected or d.status == MissionLifecycle.FAILED.value
    assert cp.status == MissionLifecycle.FAILED


def test_cancelled_cannot_later_become_completed():
    cp = _cp(MissionLifecycle.EXECUTING)
    d = declare_mission_outcome(cp, desired=MissionLifecycle.CANCELLED, cancelled=True)
    assert d.status == MissionLifecycle.CANCELLED.value
    d2 = declare_mission_outcome(cp, desired=MissionLifecycle.COMPLETED, acceptance={"ok": True})
    assert d2.rejected or d2.status == MissionLifecycle.CANCELLED.value
    assert cp.status == MissionLifecycle.CANCELLED


def test_provider_exhaustion_cannot_become_success():
    cp = _cp(MissionLifecycle.EXECUTING)
    d = declare_mission_outcome(
        cp,
        desired=MissionLifecycle.COMPLETED,
        acceptance={"ok": True},
        provider_exhausted=True,
    )
    assert d.ok is False
    assert d.code == "provider_exhausted"
    assert cp.status == MissionLifecycle.FAILED


def test_completed_requires_acceptance():
    cp = _cp(MissionLifecycle.EXECUTING)
    d = declare_mission_outcome(
        cp,
        desired=MissionLifecycle.COMPLETED,
        acceptance={"ok": False, "reason": "no_evidence"},
        execution_ok=True,
        files_changed=[{"path": "a.py"}],
    )
    assert d.rejected is True
    assert d.code == "acceptance_denied"
    assert cp.status != MissionLifecycle.COMPLETED


def test_acceptance_ok_reaches_completed():
    cp = _cp(MissionLifecycle.EXECUTING)
    d = declare_mission_outcome(
        cp,
        desired=MissionLifecycle.COMPLETED,
        acceptance={"ok": True, "reason": "all_checks_passed"},
        execution_ok=True,
    )
    assert d.ok is True
    assert cp.status == MissionLifecycle.COMPLETED


def test_version_conflict_rejects_stale_writer():
    cp = _cp(MissionLifecycle.EXECUTING)
    v = cp.version
    transition(cp, MissionLifecycle.VALIDATING)
    with pytest.raises(MissionAuthorityError) as ei:
        transition(cp, MissionLifecycle.COMPLETED, expected_version=v)
    assert ei.value.code == "version_conflict"


def test_sse_projection_never_success_from_provisional_or_agent():
    cp = _cp(MissionLifecycle.EXECUTING)
    proj = project_for_sse(
        cp,
        provisional_acceptance={"ok": True},
        agent_success=True,
    )
    assert proj["final_success"] is False
    assert proj["projection"] is True

    cp2 = _cp(MissionLifecycle.COMPLETED)
    proj2 = project_for_sse(cp2, provisional_acceptance={"ok": True})
    assert proj2["final_success"] is True


def test_child_sources_cannot_complete():
    for s in (
        "coding_loop_accept",
        "agent.completed",
        "provider_response",
        "sse_progress",
        "frontend_infer",
    ):
        assert child_cannot_complete_mission(s) is True


def test_recovery_does_not_reopen_completed():
    from brain.mission_checkpoint import begin_recovery
    cp = _cp(MissionLifecycle.COMPLETED)
    begin_recovery(cp, note="crash")
    assert cp.status == MissionLifecycle.COMPLETED


def test_coding_progress_final_success_gate():
    from brain.coding_progress import build_coding_progress
    snap = build_coding_progress(
        mission_id="m1",
        status="executing",
        acceptance={"ok": True, "reason": "provisional"},
    )
    assert snap.get("final_success") is False
    assert snap.get("success_implied") is False

    snap2 = build_coding_progress(
        mission_id="m1",
        status="completed",
        acceptance={"ok": True, "reason": "accepted"},
    )
    assert snap2.get("final_success") is True


def test_duplicate_destructive_skip():
    from brain.mission_checkpoint import should_skip_destructive, mark_step_completed, MissionStepRecord
    cp = _cp(MissionLifecycle.EXECUTING)
    cp.steps = [
        MissionStepRecord(step_id="s1", name="write", status="pending", destructive=True, idempotency_key="ik-1")
    ]
    assert should_skip_destructive(cp, "ik-1") is False
    mark_step_completed(cp, "s1", artifact_ref="f:a.py")
    assert should_skip_destructive(cp, "ik-1") is True


def test_apply_plan_terminal_uses_checkpoint_when_present():
    from brain.mission_authority import apply_plan_terminal
    from types import SimpleNamespace
    cp = _cp(MissionLifecycle.EXECUTING, mission_id="plan-auth-1")
    plan = SimpleNamespace(id="plan-auth-1", mission_id="plan-auth-1", status="running", user_id="u1")
    d = apply_plan_terminal(
        plan,
        desired="completed",
        mission_id="plan-auth-1",
        acceptance={"ok": True, "reason": "ok"},
        execution_ok=True,
    )
    assert d.get("authority") == "mission_checkpoint"
    assert plan.status == MissionLifecycle.COMPLETED.value
    assert cp.status == MissionLifecycle.COMPLETED


def test_apply_plan_terminal_plan_only_without_checkpoint():
    from brain.mission_authority import apply_plan_terminal
    from brain.mission_checkpoint import reset_checkpoints_for_tests
    from types import SimpleNamespace
    reset_checkpoints_for_tests()
    plan = SimpleNamespace(id="no-cp-1", mission_id="no-cp-1", status="running", user_id="u1")
    d = apply_plan_terminal(plan, desired="completed", mission_id="no-cp-1", execution_ok=True)
    assert d.get("authority") == "plan_only"
    assert plan.status == "completed"


def test_apply_plan_terminal_cancel_wins():
    from brain.mission_authority import apply_plan_terminal
    from types import SimpleNamespace
    cp = _cp(MissionLifecycle.EXECUTING, mission_id="plan-cancel-1")
    plan = SimpleNamespace(id="plan-cancel-1", mission_id="plan-cancel-1", status="running")
    d = apply_plan_terminal(plan, desired="completed", mission_id="plan-cancel-1",
                            acceptance={"ok": True}, cancelled=True)
    assert plan.status == MissionLifecycle.CANCELLED.value
    assert cp.status == MissionLifecycle.CANCELLED


def test_child_sources_documented():
    assert child_cannot_complete_mission("coding_loop_accept")
    assert child_cannot_complete_mission("agent.completed")


def test_two_workers_stale_version():
    """Second worker with stale expected_version cannot complete."""
    cp = _cp(MissionLifecycle.EXECUTING, mission_id="race-v1")
    v0 = cp.version
    d1 = declare_mission_outcome(
        cp, desired=MissionLifecycle.COMPLETED,
        acceptance={"ok": True, "reason": "worker1"},
        execution_ok=True, expected_version=v0,
    )
    assert d1.ok is True
    # Stale writer attempts complete again
    d2 = declare_mission_outcome(
        cp, desired=MissionLifecycle.FAILED,
        execution_ok=False, expected_version=v0,
    )
    assert d2.ok is False or d2.code == "version_conflict" or cp.status == MissionLifecycle.COMPLETED
    # Terminal sticky: cannot leave COMPLETED
    try:
        transition(cp, MissionLifecycle.EXECUTING)
        assert False, "should not leave completed"
    except MissionAuthorityError as e:
        assert e.code in ("terminal_sticky", "illegal_transition")


def test_cancel_wins_over_completion_race():
    cp = _cp(MissionLifecycle.EXECUTING, mission_id="race-cancel")
    d = declare_mission_outcome(
        cp, desired=MissionLifecycle.COMPLETED,
        acceptance={"ok": True}, execution_ok=True, cancelled=True,
    )
    assert d.status == MissionLifecycle.CANCELLED.value
    assert cp.status == MissionLifecycle.CANCELLED


def test_completion_vs_failure_first_writer_wins_sticky():
    cp = _cp(MissionLifecycle.EXECUTING, mission_id="race-fail-first")
    d1 = declare_mission_outcome(cp, desired=MissionLifecycle.FAILED, execution_ok=False)
    assert d1.status == MissionLifecycle.FAILED.value
    d2 = declare_mission_outcome(
        cp, desired=MissionLifecycle.COMPLETED,
        acceptance={"ok": True}, execution_ok=True,
    )
    # FAILED is not sticky the same way as COMPLETED/CANCELLED — recovery may allow
    # But COMPLETED from FAILED without recovery should be rejected
    assert d2.ok is False or cp.status != MissionLifecycle.COMPLETED or d2.rejected


def test_provider_exhaustion_vs_completion():
    cp = _cp(MissionLifecycle.EXECUTING, mission_id="race-prov")
    d = declare_mission_outcome(
        cp, desired=MissionLifecycle.COMPLETED,
        acceptance={"ok": True}, execution_ok=True, provider_exhausted=True,
    )
    assert d.ok is False
    assert d.status == MissionLifecycle.FAILED.value
    assert cp.status == MissionLifecycle.FAILED


def test_missing_evidence_blocks_completed_when_required():
    cp = _cp(MissionLifecycle.EXECUTING, mission_id="race-ev")
    d = declare_mission_outcome(
        cp, desired=MissionLifecycle.COMPLETED,
        execution_ok=True,
        files_changed=[{"path": "a.py"}],
        coding_evidence=None,
        acceptance=None,  # will call evaluate with require_coding_evidence
    )
    # evaluate_mission_acceptance with files and no evidence should deny
    assert d.ok is False or d.rejected


def test_plan_completion_without_checkpoint_is_plan_only():
    from brain.mission_authority import apply_plan_terminal
    from brain.mission_checkpoint import reset_checkpoints_for_tests
    from types import SimpleNamespace
    reset_checkpoints_for_tests()
    plan = SimpleNamespace(id="p-only", mission_id="p-only", status="running", user_id="u")
    d = apply_plan_terminal(plan, desired="completed", execution_ok=True)
    assert d["authority"] == "plan_only"
    assert plan.status == "completed"


def test_child_agent_completed_cannot_alone_complete():
    assert child_cannot_complete_mission("agent.completed") is True
    assert child_cannot_complete_mission("coding_loop_accept") is True
    assert child_cannot_complete_mission("plan.status") is True


def test_duplicate_terminal_idempotent_cancel():
    cp = _cp(MissionLifecycle.EXECUTING, mission_id="race-dup-c")
    d1 = declare_mission_outcome(cp, desired=MissionLifecycle.CANCELLED, cancelled=True)
    v1 = cp.version
    d2 = declare_mission_outcome(cp, desired=MissionLifecycle.CANCELLED, cancelled=True)
    assert d1.status == MissionLifecycle.CANCELLED.value
    assert d2.status == MissionLifecycle.CANCELLED.value
    # sticky cancel still cancelled
    assert cp.status == MissionLifecycle.CANCELLED
