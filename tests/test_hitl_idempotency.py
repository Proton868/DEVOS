"""HITL persistence and plan idempotency."""
import asyncio
from brain.hitl_store import create_approval, get_approval, decide_approval, list_pending_for_user
from brain.orchestration import create_plan, _PLANS
from brain.nuha_bridge import mission_truth


def test_hitl_approval_lifecycle():
    rec = create_approval(
        execution_id="ex1",
        mission_id="ex1",
        node_id="n1",
        user_id="u1",
        requested_action="deploy",
        risk="critical",
        required_capability="deploy",
    )
    assert rec["status"] == "AWAITING_APPROVAL"
    assert get_approval(rec["approval_id"])["status"] == "AWAITING_APPROVAL"
    pending = list_pending_for_user("u1")
    assert any(p["approval_id"] == rec["approval_id"] for p in pending)
    out = decide_approval(rec["approval_id"], decision="APPROVED", decided_by="u1")
    assert out["status"] == "APPROVED"
    # idempotent second decide
    out2 = decide_approval(rec["approval_id"], decision="DENIED", decided_by="u1")
    assert out2["status"] == "APPROVED"  # stays approved


def test_hitl_deny():
    rec = create_approval(
        execution_id="ex2", mission_id="ex2", node_id="n2",
        user_id="u2", requested_action="shell",
    )
    out = decide_approval(rec["approval_id"], decision="DENIED", decided_by="u2")
    assert out["status"] == "DENIED"
    assert mission_truth("waiting_for_user")["ok"] is False


def test_create_plan_idempotent():
    async def _run():
        p1 = await create_plan(user_id="u9", goal="build site", idempotency_key="k-site-1")
        p2 = await create_plan(user_id="u9", goal="build site", idempotency_key="k-site-1")
        assert p1.id == p2.id
        # different key => new plan
        p3 = await create_plan(user_id="u9", goal="build site", idempotency_key="k-site-2")
        assert p3.id != p1.id
        return True
    assert asyncio.get_event_loop().run_until_complete(_run())
