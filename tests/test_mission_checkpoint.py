"""Long-running coding mission durability, recovery, and idempotency."""
from __future__ import annotations

import pytest

from brain.mission_checkpoint import (
    MissionLifecycle,
    begin_recovery,
    can_transition,
    complete_mission,
    create_checkpoint,
    get_checkpoint,
    mark_step_completed,
    mark_step_failed,
    mark_step_running,
    record_provider_failure,
    reset_checkpoints_for_tests,
    resume_plan,
    should_skip_destructive,
    step_idempotency_key,
    transition_status,
)


@pytest.fixture(autouse=True)
def _clean():
    reset_checkpoints_for_tests()
    yield
    reset_checkpoints_for_tests()


def test_lifecycle_transitions_truthful():
    assert can_transition(MissionLifecycle.QUEUED, MissionLifecycle.PLANNING)
    assert can_transition(MissionLifecycle.EXECUTING, MissionLifecycle.RETRYING)
    assert can_transition(MissionLifecycle.VALIDATING, MissionLifecycle.COMPLETED)
    assert not can_transition(MissionLifecycle.COMPLETED, MissionLifecycle.EXECUTING)
    assert not can_transition(MissionLifecycle.CANCELLED, MissionLifecycle.EXECUTING)


def test_persist_steps_and_resume_from_checkpoint():
    cp = create_checkpoint(
        mission_id="m1",
        goal="fix auth tests",
        user_id="u1",
        steps=[
            {"step_id": "s1", "name": "inspect"},
            {"step_id": "s2", "name": "edit", "destructive": True},
            {"step_id": "s3", "name": "validate"},
        ],
    )
    transition_status(cp, MissionLifecycle.PLANNING)
    transition_status(cp, MissionLifecycle.EXECUTING)
    mark_step_running(cp, "s1", provider="openrouter", model="free")
    mark_step_completed(cp, "s1", summary="found failing test")
    assert len(cp.completed_steps()) == 1
    assert len(cp.pending_steps()) == 2

    # Simulate process death — only durable checkpoint remains
    raw = cp.to_dict()
    reset_checkpoints_for_tests()
    from brain.mission_checkpoint import MissionCheckpoint, _CHECKPOINTS
    restored = MissionCheckpoint.from_dict(raw)
    _CHECKPOINTS["m1"] = restored

    plan = resume_plan(restored)
    assert plan["action"] in ("execute_step", "retry_step")
    assert plan["next_step_id"] == "s2"
    assert plan["status"] in ("recovery", "executing", "retrying")


def test_provider_429_marks_retrying_not_completed():
    cp = create_checkpoint(
        mission_id="m2",
        goal="code",
        steps=[{"step_id": "s1", "name": "llm"}],
    )
    transition_status(cp, MissionLifecycle.PLANNING)
    transition_status(cp, MissionLifecycle.EXECUTING)
    mark_step_running(cp, "s1", provider="openrouter")
    record_provider_failure(
        cp,
        provider="openrouter",
        model="free",
        status_code=429,
        error="HTTP 429 Too Many Requests",
        category="rate_limited",
    )
    assert cp.status == MissionLifecycle.RETRYING
    assert cp.global_retry_count == 1
    assert cp.status != MissionLifecycle.COMPLETED
    assert cp.provider_errors[-1]["retryable"] is True


def test_provider_exhausted_fails_truthfully():
    cp = create_checkpoint(
        mission_id="m3",
        goal="code",
        steps=[{"step_id": "s1", "name": "llm"}],
    )
    cp.max_global_retries = 1
    transition_status(cp, MissionLifecycle.PLANNING)
    transition_status(cp, MissionLifecycle.EXECUTING)
    record_provider_failure(cp, provider="openrouter", status_code=429, error="429")
    record_provider_failure(cp, provider="omniroute", status_code=503, error="503")
    assert cp.status == MissionLifecycle.FAILED
    assert cp.status != MissionLifecycle.COMPLETED


def test_destructive_idempotency_prevents_duplicate_after_recovery():
    cp = create_checkpoint(
        mission_id="m4",
        goal="edit",
        steps=[{"step_id": "s1", "name": "apply_patch", "destructive": True}],
    )
    transition_status(cp, MissionLifecycle.PLANNING)
    transition_status(cp, MissionLifecycle.EXECUTING)
    s1 = cp.steps[0]
    assert s1.idempotency_key
    mark_step_running(cp, "s1")
    mark_step_completed(cp, "s1", summary="patched", artifact_ref="file:a.py")
    assert should_skip_destructive(cp, s1.idempotency_key) is True

    begin_recovery(cp)  # from failed would work; from completed skip
    # After completed steps, resume should validate not re-run destructive
    plan = resume_plan(cp)
    assert s1.idempotency_key in plan["skip_destructive_keys"]


def test_cannot_complete_without_validation_path():
    cp = create_checkpoint(
        mission_id="m5",
        goal="x",
        steps=[{"step_id": "s1", "name": "work"}],
    )
    transition_status(cp, MissionLifecycle.PLANNING)
    transition_status(cp, MissionLifecycle.EXECUTING)
    mark_step_completed(cp, "s1")
    plan = resume_plan(cp)
    assert plan["action"] == "validate"
    complete_mission(cp, verified=True)
    assert cp.status == MissionLifecycle.COMPLETED


def test_failed_verification_does_not_complete():
    cp = create_checkpoint(
        mission_id="m6",
        goal="x",
        steps=[{"step_id": "s1", "name": "work"}],
    )
    transition_status(cp, MissionLifecycle.PLANNING)
    transition_status(cp, MissionLifecycle.EXECUTING)
    mark_step_completed(cp, "s1")
    resume_plan(cp)
    complete_mission(cp, verified=False)
    assert cp.status == MissionLifecycle.FAILED


def test_step_retry_count_persists():
    cp = create_checkpoint(
        mission_id="m7",
        goal="x",
        steps=[{"step_id": "s1", "name": "work", "max_attempts": 2}],
    )
    transition_status(cp, MissionLifecycle.PLANNING)
    transition_status(cp, MissionLifecycle.EXECUTING)
    mark_step_running(cp, "s1")
    mark_step_failed(cp, "s1", error="transient")
    assert cp.steps[0].attempt == 1
    assert cp.steps[0].status == "pending"
    mark_step_running(cp, "s1")
    mark_step_failed(cp, "s1", error="again")
    assert cp.steps[0].status == "failed"
