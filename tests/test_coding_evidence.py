"""Coding evidence completeness — fabricated/incomplete cannot accept."""
from __future__ import annotations

from brain.coding_evidence import (
    build_coding_evidence,
    failure_evidence,
    validate_coding_evidence,
)
from brain.mission_acceptance import evaluate_mission_acceptance


def _full_evidence(**over):
    base = dict(
        mission_id="m1",
        project_id="p1",
        user_id="u1",
        agent_id="web-agent",
        persona_id="full_stack_engineer",
        provider="omniroute",
        model="free",
        files_changed=["src/app.py"],
        commands=[{
            "command": "python -m pytest -q",
            "exit_code": 0,
            "ok": True,
            "stdout": "1 passed",
            "stderr": "",
            "kind": "test",
        }],
        tests_executed=[{"command": "python -m pytest -q", "ok": True, "exit_code": 0}],
        validation={"ok": True, "structure_ok": True, "works": True},
        artifacts=["file:src/app.py"],
        success=True,
    )
    base.update(over)
    return build_coding_evidence(**base)


def test_build_includes_required_fields():
    ev = _full_evidence()
    d = ev.to_dict()
    assert d["mission_id"] == "m1"
    assert d["project_id"] == "p1"
    assert d["agent_id"] == "web-agent"
    assert d["provider"] == "omniroute"
    assert d["model"] == "free"
    assert "src/app.py" in d["files_changed"]
    assert d["commands"][0]["exit_code"] == 0
    assert d["commands"][0]["stdout_tail"]
    assert d["tests_executed"]
    assert d["validation"]
    assert d["artifacts"]
    assert d["timestamps"]["created_at"]
    assert d["fabricated"] is False


def test_incomplete_missing_commands_rejected():
    # Without files+validation, missing commands still fail closed
    ev = _full_evidence(commands=[], files_changed=[], artifacts=[], validation=None, success=False)
    res = validate_coding_evidence(ev, require_commands=True, require_files_if_success=False)
    assert res["ok"] is False
    assert res["reason"] == "commands_missing"


def test_fabricated_flag_rejected():
    ev = _full_evidence()
    d = ev.to_dict()
    d["fabricated"] = True
    res = validate_coding_evidence(d)
    assert res["ok"] is False
    assert res["reason"] == "fabricated_evidence"


def test_success_without_files_rejected():
    ev = _full_evidence(files_changed=[], artifacts=[], success=True)
    res = validate_coding_evidence(ev, require_files_if_success=True)
    assert res["ok"] is False
    assert res["reason"] == "success_without_files"


def test_success_without_validation_rejected():
    ev = _full_evidence(validation=None, success=True)
    res = validate_coding_evidence(ev)
    assert res["ok"] is False
    assert res["reason"] == "success_without_validation"


def test_mission_mismatch_rejected():
    ev = _full_evidence()
    res = validate_coding_evidence(ev, expected_mission_id="other")
    assert res["ok"] is False
    assert res["reason"] == "mission_mismatch"


def test_failure_evidence_retained():
    ev = failure_evidence(
        mission_id="m2",
        project_id="p2",
        error="pytest failed",
        agent_id="coder",
        commands=[{
            "command": "python -m pytest -q",
            "exit_code": 1,
            "ok": False,
            "stdout": "FAILED",
            "stderr": "",
            "kind": "test",
        }],
        files_changed=["src/app.py"],
    )
    assert ev.success is False
    assert ev.error == "pytest failed"
    res = validate_coding_evidence(ev, require_files_if_success=True)
    # failure with commands is complete enough for storage
    assert res["ok"] is True


def test_acceptance_rejects_incomplete_coding_evidence():
    acc = evaluate_mission_acceptance(
        execution_ok=True,
        status="succeeded",
        files_changed=["a.py"],
        ponytail={"passed": True, "applicable": True},
        evidence_refs=["fake-ref"],
        mission_id="m1",
        user_id="u1",
        require_coding_evidence=True,
        coding_evidence={
            "evidence_id": "x",
            "mission_id": "m1",
            "project_id": "p1",
            # missing agent, commands, timestamps
            "fabricated": False,
        },
    )
    assert acc["ok"] is False
    assert "coding_evidence" in acc["reason"]


def test_acceptance_rejects_fabricated():
    acc = evaluate_mission_acceptance(
        execution_ok=True,
        status="succeeded",
        files_changed=["a.py"],
        ponytail={"passed": True},
        evidence_refs=["e1"],
        require_coding_evidence=True,
        coding_evidence={
            "evidence_id": "e1",
            "mission_id": "m1",
            "project_id": "p1",
            "agent_id": "a",
            "commands": [{"command": "true", "exit_code": 0, "ok": True}],
            "files_changed": ["a.py"],
            "validation": {"ok": True},
            "timestamps": {"created_at": "t"},
            "success": True,
            "fabricated": True,
        },
    )
    assert acc["ok"] is False
    assert "fabricated" in acc["reason"]


def test_acceptance_with_complete_coding_evidence():
    ev = _full_evidence()
    d = ev.to_dict()
    acc = evaluate_mission_acceptance(
        execution_ok=True,
        status="succeeded",
        files_changed=d["files_changed"],
        ponytail={"passed": True, "applicable": True, "evidence_id": d["evidence_id"]},
        evidence_refs=[d["evidence_id"]],
        mission_id="m1",
        user_id="u1",
        expected_mission_id="m1",
        expected_user_id="u1",
        require_coding_evidence=True,
        coding_evidence=d,
    )
    assert acc["ok"] is True
    assert acc["checks"].get("coding_evidence_ok") is True


def test_empty_evidence_refs_alone_not_enough_when_coding_required():
    acc = evaluate_mission_acceptance(
        execution_ok=True,
        status="succeeded",
        files_changed=["a.py"],
        ponytail={"passed": True},
        evidence_refs=[],
        require_coding_evidence=True,
        coding_evidence=None,
    )
    assert acc["ok"] is False
