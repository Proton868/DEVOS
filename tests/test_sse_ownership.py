from brain.ownership import deny_cross_user, assert_owner
from brain.orchestration import OrchestrationPlan


def test_event_stream_denied_for_other_user():
    plan = OrchestrationPlan(id="e1", user_id="owner", goal="x")
    assert deny_cross_user(plan, "owner") is False
    assert deny_cross_user(plan, "intruder") is True


def test_missing_owner_denied():
    assert assert_owner(None, "anyone") is False
