"""Cancellation is terminal for mission_truth; sequenced events survive cancel emit."""
from brain.orchestration import OrchestrationPlan, request_cancel, _PLANS
from brain.nuha_bridge import mission_truth


def test_cancel_status_is_not_success():
    assert mission_truth("cancelled")["ok"] is False
    assert mission_truth("cancellation_requested")["ok"] is False
    assert mission_truth("cancelling")["ok"] is False


def test_request_cancel_emits_and_marks():
    p = OrchestrationPlan(id="cancel-test-1", user_id="u1", goal="long job")
    p.status = "running"
    _PLANS[p.id] = p
    out = request_cancel(p.id)
    assert out is not None
    st = (out.status or "").lower()
    assert st in ("cancellation_requested", "cancelling", "cancelled")
    # Must not be success
    assert mission_truth(out.status)["ok"] is False
    types = [e.get("type") for e in out.events]
    assert any("cancel" in (t or "") for t in types)


def test_cancelled_cannot_be_reported_ok_via_truth():
    # Even if someone passes explicit_ok=True, failure statuses win
    assert mission_truth("cancelled", explicit_ok=True)["ok"] is False
