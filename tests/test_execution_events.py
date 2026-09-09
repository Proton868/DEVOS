"""Sequenced plan events and mission_truth remain authoritative."""
from brain.orchestration import OrchestrationPlan
from brain.nuha_bridge import mission_truth


def test_emit_has_sequence():
    p = OrchestrationPlan(id="p1", user_id="u1", goal="test")
    p.emit("execution.created", {"x": 1})
    p.emit("node.started", {"node_id": "n1"})
    assert len(p.events) == 2
    assert p.events[0]["sequence"] == 1
    assert p.events[1]["sequence"] == 2
    assert p.events[0]["event_id"] == "p1:1"
    assert p.events[1]["type"] == "node.started"


def test_replay_after_filters():
    p = OrchestrationPlan(id="p2", user_id="u1", goal="test")
    for i in range(5):
        p.emit(f"tick.{i}", {})
    after = 2
    out = [e for e in p.events if e["sequence"] > after]
    assert [e["sequence"] for e in out] == [3, 4, 5]


def test_mission_truth_unknown_not_success():
    assert mission_truth("running")["ok"] is False
    assert mission_truth("unknown")["ok"] is False
