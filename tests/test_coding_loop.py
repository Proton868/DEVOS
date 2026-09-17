"""Bounded coding loop: success, repair, exhaustion, cancel, no false success."""
from __future__ import annotations

from brain.coding_loop import (
    CodingLoopConfig,
    CodingLoopHooks,
    CodingLoopState,
    CodingStage,
    run_coding_loop,
)


def _hooks(**overrides):
    base = dict(
        inspect=lambda: {"ecosystem": "python", "kind": "PYTHON_APP"},
        plan=lambda insp: {"command": "pytest -q", "validate_as": "tests"},
        edit=lambda plan, diag: [{"path": "app.py"}],
        execute=lambda plan: {"command": "pytest -q", "exit_code": 0, "ok": True, "stdout": "ok"},
        diagnose=lambda cmd: "fix_it",
        evidence=lambda st: "ev-1",
        accept=lambda st: {"ok": True, "reason": "validated"},
    )
    base.update(overrides)
    return CodingLoopHooks(**base)


def test_successful_first_attempt():
    events = []
    h = _hooks(on_event=events.append)
    st = run_coding_loop(h, CodingLoopConfig(max_attempts=3, require_evidence=True))
    assert st.stage == CodingStage.DONE.value
    assert st.attempt == 1
    stages = [e["stage"] for e in st.events]
    assert CodingStage.INSPECT.value in stages
    assert CodingStage.ACCEPT.value in stages
    assert st.evidence_id == "ev-1"


def test_failure_then_repair_success():
    calls = {"n": 0}

    def execute(plan):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"command": "pytest", "exit_code": 1, "ok": False, "stderr": "boom"}
        return {"command": "pytest", "exit_code": 0, "ok": True, "stdout": "pass"}

    st = run_coding_loop(
        _hooks(execute=execute),
        CodingLoopConfig(max_attempts=3, require_evidence=True),
    )
    assert st.stage == CodingStage.DONE.value
    assert st.attempt == 2
    assert any(e["stage"] == CodingStage.DIAGNOSE.value for e in st.events)


def test_repeated_failure_exhausts():
    st = run_coding_loop(
        _hooks(
            execute=lambda plan: {"command": "x", "exit_code": 1, "ok": False, "stderr": "fail"},
            evidence=lambda s: None,
        ),
        CodingLoopConfig(max_attempts=3, require_evidence=True),
    )
    assert st.stage == CodingStage.EXHAUSTED.value
    assert st.attempt == 3
    assert st.error == "retry_budget_exhausted"


def test_retry_exhaustion_never_infinite():
    st = run_coding_loop(
        _hooks(execute=lambda plan: {"exit_code": 1, "ok": False}),
        CodingLoopConfig(max_attempts=2),
    )
    assert st.attempt <= 2
    assert st.stage == CodingStage.EXHAUSTED.value
    assert st.finished_at is not None


def test_interruption_recovery_resume():
    """Resume from durable state continues attempts without losing inspection/plan."""
    partial = CodingLoopState(
        stage=CodingStage.EXECUTE.value,
        attempt=1,
        max_attempts=3,
        inspection={"ecosystem": "python"},
        plan={"command": "pytest"},
        diagnosis="prior_fail",
        files_changed=[{"path": "a.py"}],
    )
    calls = {"n": 0}

    def execute(plan):
        calls["n"] += 1
        return {"exit_code": 0, "ok": True}

    st = run_coding_loop(
        _hooks(execute=execute, edit=lambda p, d: []),
        CodingLoopConfig(max_attempts=3),
        resume=partial,
    )
    assert st.inspection is not None
    assert st.plan is not None
    assert st.stage == CodingStage.DONE.value
    # resumed: one more successful attempt after prior attempt=1
    assert st.attempt == 2


def test_false_success_prevented_without_evidence():
    st = run_coding_loop(
        _hooks(evidence=lambda s: None),
        CodingLoopConfig(max_attempts=2, require_evidence=True),
    )
    assert st.stage == CodingStage.FAILED.value
    assert st.error == "evidence_required_missing"
    assert st.stage != CodingStage.DONE.value


def test_false_success_prevented_when_accept_denies():
    st = run_coding_loop(
        _hooks(accept=lambda s: {"ok": False, "reason": "ponytail_failed"}),
        CodingLoopConfig(max_attempts=1, require_evidence=True),
    )
    assert st.stage == CodingStage.EXHAUSTED.value
    assert st.stage != CodingStage.DONE.value


def test_cancel_stops_loop():
    flag = {"c": False}

    def inspect():
        flag["c"] = True
        return {"ecosystem": "python"}

    st = run_coding_loop(
        _hooks(inspect=inspect, is_cancelled=lambda: True),
        CodingLoopConfig(max_attempts=5),
    )
    assert st.stage == CodingStage.CANCELLED.value


def test_state_to_dict_durable():
    st = CodingLoopState(attempt=1, inspection={"k": 1})
    d = st.to_dict()
    assert d["attempt"] == 1
    assert "events" in d
    assert "elapsed_s" in d
