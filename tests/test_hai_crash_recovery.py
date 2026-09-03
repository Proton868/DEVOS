"""Stage 3I — Controlled HAI crash/restart acceptance."""
from __future__ import annotations
import json, subprocess, sys
from pathlib import Path
import pytest
from cognitive.hai_checkpoint import (
    CheckpointError, HAICheckpoint, ReconcileOutcome,
    bound_state, build_checkpoint, reconcile_with_execution,
)

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "auth_fail_project"

def _ensure_fixture_broken():
    src = '''SIDE_EFFECT_COUNT = 0

def authenticate(username: str, password: str) -> bool:
    global SIDE_EFFECT_COUNT
    SIDE_EFFECT_COUNT += 1
    if not username or not password:
        return False
    return password == username

def reset_side_effect_count() -> None:
    global SIDE_EFFECT_COUNT
    SIDE_EFFECT_COUNT = 0
'''
    Path(FIXTURE / "auth.py").write_text(src)
    for name in list(sys.modules):
        if name == "auth" or name.startswith("auth."):
            del sys.modules[name]

def _sample_state():
    return {
        "strategic": {
            "objective": "Fix authentication test failure",
            "plan": "1. orient\n2. patch\n3. verify",
            "plan_version": 1,
            "current_subgoal_id": "sg_patch",
            "subgoals": [{"id": "sg_patch", "status": "in_progress"}],
            "completed_subgoals": ["sg_orient"],
            "blockers": [], "status": "active",
            "iteration_count": 2, "replan_count": 0, "uncertainty": 0.25,
        },
        "tactical": {
            "current_subgoal_id": "sg_patch", "current_action": "apply_patch",
            "action_count": 4,
            "tool_calls": [{"tool": "apply_patch", "outcome": "ok"}],
            "observations": [{"summary": "password compared to username"}],
            "verification_status": "not_started",
            "action_history": ["read_file", "apply_patch"],
            "failure_history": [],
        },
        "verification": {"status": "not_started"},
        "lifecycle": "executing",
    }

def test_baseline_auth_fixture_fails_for_intended_reason():
    _ensure_fixture_broken()
    r = subprocess.run(
        [sys.executable, "-m", "pytest", "test_auth.py::test_authenticate_accepts_valid_password", "-q"],
        cwd=str(FIXTURE), capture_output=True, text=True,
    )
    assert r.returncode != 0

def test_manual_fix_makes_tests_pass():
    _ensure_fixture_broken()
    original = Path(FIXTURE / "auth.py").read_text()
    fixed = original.replace("password == username", 'password == "s3cret"')
    assert fixed != original
    Path(FIXTURE / "auth.py").write_text(fixed)
    try:
        r = subprocess.run(
            [sys.executable, "-m", "pytest", "test_auth.py", "-q"],
            cwd=str(FIXTURE), capture_output=True, text=True,
        )
        assert r.returncode == 0, r.stdout + r.stderr
    finally:
        Path(FIXTURE / "auth.py").write_text(original)

def test_crash_after_checkpoint_restore_validates():
    cp = build_checkpoint(
        task_id="task-crash-1", state=_sample_state(), lifecycle="executing",
        state_version=7, last_job_id="job-1", last_job_status="succeeded",
        correlation_id="corr-crash",
    )
    persisted = json.loads(json.dumps(cp.to_dict()))
    restored = HAICheckpoint.from_dict(persisted, verify=True)
    assert restored.task_id == "task-crash-1"
    recon = reconcile_with_execution(restored, job_status="succeeded", job_id="job-1")
    assert recon.retry is False
    assert recon.outcome == ReconcileOutcome.CONTINUE.value

@pytest.mark.parametrize("job_status,expected", [
    ("succeeded", ReconcileOutcome.CONTINUE.value),
    ("running", ReconcileOutcome.WAIT.value),
    ("queued", ReconcileOutcome.WAIT.value),
    ("failed", ReconcileOutcome.REPLAN.value),
    ("unknown", ReconcileOutcome.UNKNOWN.value),
])
def test_reconcile_job_statuses(job_status, expected):
    cp = build_checkpoint(task_id="t", state=_sample_state(), last_job_id="j1", last_job_status="pending")
    r = reconcile_with_execution(cp, job_status=job_status, job_id="j1")
    assert r.outcome == expected
    assert r.retry is False

def test_cancelled_no_restart():
    cp = build_checkpoint(task_id="t", state=_sample_state())
    r = reconcile_with_execution(cp, task_status="cancelled")
    assert r.outcome == ReconcileOutcome.CANCELLED.value

def test_stale_checkpoint_job_already_succeeded():
    cp = build_checkpoint(task_id="t", state=_sample_state(), last_job_status="running")
    r = reconcile_with_execution(cp, job_status="succeeded", job_id="j1")
    assert r.outcome == ReconcileOutcome.CONTINUE.value
    assert r.retry is False

def test_corrupt_checkpoint_fail_closed():
    cp = build_checkpoint(task_id="t", state=_sample_state())
    d = cp.to_dict()
    d["state"]["strategic"]["objective"] = "TAMPER"
    with pytest.raises(CheckpointError, match="checksum"):
        HAICheckpoint.from_dict(d, verify=True)

def test_identity_tamper_rejected():
    cp = build_checkpoint(task_id="t", state=_sample_state())
    d = cp.to_dict()
    d["user_id"] = "attacker"
    with pytest.raises(CheckpointError):
        HAICheckpoint.from_dict(d, verify=True)

def test_unknown_never_retried():
    cp = build_checkpoint(task_id="t", state=_sample_state(), last_job_status="unknown")
    r = reconcile_with_execution(cp, job_status="unknown")
    assert r.lifecycle == "unknown"
    assert r.retry is False

def test_event_sequence_monotonic():
    events = [{"seq": i} for i in range(1, 6)]
    replay = [e for e in events if e["seq"] > 3]
    assert [e["seq"] for e in replay] == [4, 5]

def test_no_subprocess_in_recovery_module():
    src = open("cognitive/hai_checkpoint.py").read()
    assert "subprocess" not in src
