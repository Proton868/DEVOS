"""Cross-user isolation and spine label invariants."""
from brain.ownership import assert_owner, deny_cross_user
from brain.orchestration import OrchestrationPlan
from brain.nuha_bridge import mission_truth


def test_owner_match():
    assert assert_owner("u1", "u1") is True
    assert assert_owner("u1", "u2") is False
    assert assert_owner(None, "u1") is False


def test_deny_cross_user_plan():
    p = OrchestrationPlan(id="p1", user_id="alice", goal="x")
    assert deny_cross_user(p, "alice") is False
    assert deny_cross_user(p, "bob") is True
    assert deny_cross_user({"user_id": "alice"}, "bob") is True


def test_scaffold_vs_mission_labels_in_truth_path():
    # mission_truth does not care about path label; ok only from status
    assert mission_truth("completed")["ok"] is True
    assert mission_truth("failed")["ok"] is False
    # scaffold success is not a mission status
    assert mission_truth("scaffold_created")["ok"] is False
