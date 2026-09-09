"""P0: mission truth must never claim success on failed/cancelled plans."""
from brain.nuha_bridge import mission_truth, synthesize_orchestration_reply


def test_failed_status_not_ok():
    t = mission_truth("failed")
    assert t["ok"] is False
    assert t["synthesis_mode"] == "failure"


def test_cancelled_not_ok():
    t = mission_truth("cancelled")
    assert t["ok"] is False


def test_completed_ok():
    t = mission_truth("completed")
    assert t["ok"] is True
    assert t["synthesis_mode"] == "success"


def test_waiting_mode():
    t = mission_truth("waiting_for_user")
    assert t["ok"] is False
    assert t["synthesis_mode"] == "waiting"


def test_explicit_ok_false_overrides():
    t = mission_truth("completed", explicit_ok=False)
    assert t["ok"] is False


def test_synthesize_failure_no_done_claim():
    text = synthesize_orchestration_reply({
        "ok": False,
        "plan_id": "p1",
        "status": "failed",
        "error": "provider down",
    })
    assert "provider down" in text or "failed" in text.lower()
    assert "completed successfully" not in text.lower()


def test_synthesize_uses_status_when_ok_true_but_failed():
    # Defensive: ok flag wrong but status failed
    text = synthesize_orchestration_reply({
        "ok": True,
        "plan_id": "p2",
        "status": "failed",
        "plan": {},
        "steps": [],
    })
    assert "failed" in text.lower() or "not treated as completed" in text.lower() or "hit:" in text.lower()
