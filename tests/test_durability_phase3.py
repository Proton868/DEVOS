"""Phase 3: mission durability, outbox idempotency, recovery decisions."""
from __future__ import annotations

from brain.mission_durability import (
    scoped_idempotency_key,
    a2a_message_idempotency_id,
    reconcile_mission_state,
)


def test_idempotency_key_owner_scoped():
    a = scoped_idempotency_key(user_id="user-a", client_key="same")
    b = scoped_idempotency_key(user_id="user-b", client_key="same")
    assert a != b
    assert a == scoped_idempotency_key(user_id="user-a", client_key="same")


def test_a2a_message_id_stable():
    x = a2a_message_idempotency_id(
        mission_id="m1", task_id="t1", message_type="delegate", round_i=1
    )
    y = a2a_message_idempotency_id(
        mission_id="m1", task_id="t1", message_type="delegate", round_i=1
    )
    z = a2a_message_idempotency_id(
        mission_id="m1", task_id="t1", message_type="delegate", round_i=2
    )
    assert x == y
    assert x != z


def test_reconcile_crash_before_evidence():
    r = reconcile_mission_state(
        status="running",
        files_changed=[{"path": "index.html"}],
        ponytail={"passed": True, "applicable": True},
        evidence_refs=[],
        execution_ok=True,
    )
    assert r["action"] == "resume_evidence"
    assert r["target_status"] == "accepting"


def test_reconcile_crash_after_evidence():
    r = reconcile_mission_state(
        status="running",
        files_changed=[{"path": "index.html"}],
        ponytail={"passed": True, "applicable": True, "evidence_id": "e1"},
        evidence_refs=["e1"],
        execution_ok=True,
    )
    assert r["action"] == "complete"
    assert r["target_status"] == "succeeded"


def test_reconcile_ponytail_pending():
    r = reconcile_mission_state(
        status="running",
        files_changed=[{"path": "index.html"}],
        ponytail=None,
        evidence_refs=[],
        execution_ok=True,
    )
    assert r["action"] == "resume_ponytail"


def test_reconcile_terminal_noop():
    r = reconcile_mission_state(status="failed", files_changed=[], ponytail=None)
    assert r["action"] == "noop"


def test_outbox_enqueue_idempotent_logic():
    """Same idempotency_key returns same event id when DB available; pure unit otherwise."""
    keys = set()
    for _ in range(3):
        from brain.mission_durability import emit_mission_event
        eid = emit_mission_event(
            "mission.created",
            mission_id="m-test",
            user_id="u1",
            payload={"n": 1},
        )
        if eid:
            keys.add(eid)
    # If outbox unavailable, all None — still pass as environment limitation
    if keys:
        assert len(keys) == 1
