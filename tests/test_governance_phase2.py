"""Phase 2: fail-closed mission acceptance, Ponytail, evidence."""
from __future__ import annotations

from brain.mission_acceptance import evaluate_mission_acceptance
from brain.nuha_bridge import mission_truth


def test_explicit_ok_cannot_bypass_missing_acceptance():
    t = mission_truth("running", explicit_ok=True)
    assert t["ok"] is False
    assert t.get("reason") == "explicit_ok_insufficient" or t["synthesis_mode"] == "incomplete"


def test_explicit_ok_false_fails():
    t = mission_truth("accepted", explicit_ok=False)
    assert t["ok"] is False


def test_missing_ponytail_not_success():
    acc = evaluate_mission_acceptance(
        execution_ok=True,
        status="succeeded",
        files_changed=[{"path": "index.html"}],
        ponytail=None,
        evidence_refs=["e1"],
    )
    assert acc["ok"] is False
    assert acc["reason"] == "ponytail_missing"


def test_rejected_ponytail_not_success():
    acc = evaluate_mission_acceptance(
        execution_ok=True,
        status="succeeded",
        files_changed=[{"path": "index.html"}],
        ponytail={"passed": False, "applicable": True},
        evidence_refs=["e1"],
    )
    assert acc["ok"] is False
    assert acc["reason"] == "ponytail_rejected"


def test_missing_evidence_not_success():
    acc = evaluate_mission_acceptance(
        execution_ok=True,
        status="accepted",
        files_changed=[{"path": "index.html"}],
        ponytail={"passed": True, "applicable": True, "evidence_id": "ev1"},
        evidence_refs=[],
    )
    assert acc["ok"] is False
    assert acc["reason"] == "evidence_missing"


def test_wrong_owner_not_success():
    acc = evaluate_mission_acceptance(
        execution_ok=True,
        status="accepted",
        files_changed=[{"path": "index.html"}],
        ponytail={"passed": True, "applicable": True, "evidence_id": "ev1"},
        evidence_refs=["ev1"],
        user_id="user-a",
        expected_user_id="user-b",
        mission_id="m1",
        expected_mission_id="m1",
    )
    assert acc["ok"] is False
    assert acc["reason"] == "owner_mismatch"


def test_wrong_mission_not_success():
    acc = evaluate_mission_acceptance(
        execution_ok=True,
        status="accepted",
        files_changed=[{"path": "index.html"}],
        ponytail={"passed": True, "applicable": True, "evidence_id": "ev1"},
        evidence_refs=["ev1"],
        user_id="u1",
        expected_user_id="u1",
        mission_id="m-a",
        expected_mission_id="m-b",
    )
    assert acc["ok"] is False
    assert acc["reason"] == "mission_mismatch"


def test_execution_not_ok_fails():
    acc = evaluate_mission_acceptance(
        execution_ok=False,
        status="failed",
        files_changed=[{"path": "index.html"}],
        ponytail={"passed": True, "applicable": True},
        evidence_refs=["e1"],
    )
    assert acc["ok"] is False


def test_governed_success():
    acc = evaluate_mission_acceptance(
        execution_ok=True,
        status="accepted",
        files_changed=[{"path": "index.html"}],
        ponytail={"passed": True, "applicable": True, "evidence_id": "ev1"},
        evidence_refs=["ev1"],
        user_id="u1",
        expected_user_id="u1",
        mission_id="m1",
        expected_mission_id="m1",
    )
    assert acc["ok"] is True
    t = mission_truth("accepted", acceptance=acc)
    assert t["ok"] is True


def test_acceptance_overrides_explicit_ok_true():
    acc = evaluate_mission_acceptance(
        execution_ok=True,
        files_changed=[{"path": "x.py"}],
        ponytail=None,
        evidence_refs=[],
    )
    t = mission_truth("succeeded", explicit_ok=True, acceptance=acc)
    assert t["ok"] is False
