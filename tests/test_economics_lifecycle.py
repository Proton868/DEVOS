"""Economic reserve → execute → reconcile lifecycle + recovery."""
from __future__ import annotations

import pytest

from governance.economics import (
    get_economic_gateway,
    reset_economic_gateway_for_tests,
    EconomicError,
    EconomicDecision,
    ReservationStatus,
    PlanProfile,
)


@pytest.fixture(autouse=True)
def _reset():
    reset_economic_gateway_for_tests()
    yield
    reset_economic_gateway_for_tests()


def test_estimate_allow_and_deny():
    gw = get_economic_gateway()
    gw.ensure_entitlement("w1", profile=PlanProfile.RECRUIT.value)
    ok = gw.estimate(world_id="w1", user_id="u1", operation="chat", estimated_credits=10)
    assert ok["decision"] == EconomicDecision.ALLOW.value
    deny = gw.estimate(world_id="w1", user_id="u1", operation="chat", estimated_credits=10_000)
    assert deny["decision"] in (
        EconomicDecision.DENY.value,
        EconomicDecision.REDUCE_SCOPE.value,
    )


def test_reserve_execute_reconcile_release():
    gw = get_economic_gateway()
    gw.ensure_entitlement("w1", profile=PlanProfile.OUTER_SECT.value)
    rsv = gw.reserve(world_id="w1", user_id="u1", estimated_credits=100, mission_id="m1")
    assert rsv.status == ReservationStatus.RESERVED
    ent = gw.get_entitlement("w1")
    assert ent.reserved_credits == 100
    rec, usage = gw.reconcile(
        reservation_id=rsv.reservation_id,
        world_id="w1",
        actual_credits=40,
        outcome="success",
        evidence_id="ev1",
    )
    assert rec.consumed_credits == 40
    assert rec.released_credits == 60
    assert usage.credits_consumed == 40
    assert usage.evidence_id == "ev1"
    ent2 = gw.get_entitlement("w1")
    assert ent2.consumed_credits == 40
    assert ent2.reserved_credits == 0


def test_idempotent_reserve():
    gw = get_economic_gateway()
    gw.ensure_entitlement("w1", profile=PlanProfile.OUTER_SECT.value)
    r1 = gw.reserve(world_id="w1", user_id="u1", estimated_credits=20, idempotency_key="same")
    r2 = gw.reserve(world_id="w1", user_id="u1", estimated_credits=20, idempotency_key="same")
    assert r1.reservation_id == r2.reservation_id
    assert gw.get_entitlement("w1").reserved_credits == 20  # not double-held


def test_failed_execution_releases_hold():
    gw = get_economic_gateway()
    gw.ensure_entitlement("w1", profile=PlanProfile.OUTER_SECT.value)
    rsv = gw.reserve(world_id="w1", user_id="u1", estimated_credits=50)
    rec, usage = gw.reconcile(
        reservation_id=rsv.reservation_id,
        world_id="w1",
        actual_credits=0,
        outcome="failed",
    )
    assert usage.outcome == "failed"
    assert gw.get_entitlement("w1").reserved_credits == 0


def test_cancel_reservation():
    gw = get_economic_gateway()
    gw.ensure_entitlement("w1", profile=PlanProfile.OUTER_SECT.value)
    rsv = gw.reserve(world_id="w1", user_id="u1", estimated_credits=30)
    cancelled = gw.cancel_reservation(reservation_id=rsv.reservation_id, world_id="w1")
    assert cancelled.status == ReservationStatus.CANCELLED
    assert gw.get_entitlement("w1").reserved_credits == 0


def test_double_reconcile_idempotent():
    gw = get_economic_gateway()
    gw.ensure_entitlement("w1", profile=PlanProfile.OUTER_SECT.value)
    rsv = gw.reserve(world_id="w1", user_id="u1", estimated_credits=25)
    r1, u1 = gw.reconcile(reservation_id=rsv.reservation_id, world_id="w1", actual_credits=25)
    r2, u2 = gw.reconcile(reservation_id=rsv.reservation_id, world_id="w1", actual_credits=25)
    assert u1.usage_event_id == u2.usage_event_id
    assert gw.get_entitlement("w1").consumed_credits == 25


def test_insufficient_allowance_blocks_execution():
    gw = get_economic_gateway()
    gw.ensure_entitlement("w1", profile=PlanProfile.RECRUIT.value)
    with pytest.raises(EconomicError):
        gw.reserve(world_id="w1", user_id="u1", estimated_credits=10_000)


def test_premium_requires_byok_or_entitlement():
    gw = get_economic_gateway()
    gw.ensure_entitlement("w1", profile=PlanProfile.RECRUIT.value)
    d = gw.estimate(
        world_id="w1", user_id="u1", operation="llm",
        estimated_credits=5, require_premium=True,
    )
    assert d["decision"] == EconomicDecision.REQUIRE_BYOK.value


def test_recruit_beta_override():
    gw = get_economic_gateway()
    ent = gw.ensure_entitlement("w1", profile=PlanProfile.RECRUIT.value, beta_override=True)
    assert ent.beta_override is True
    assert ent.monthly_allowance >= 1000


def test_plan_profiles_exist():
    gw = get_economic_gateway()
    for p in PlanProfile:
        ent = gw.ensure_entitlement(f"w-{p.value}", profile=p.value)
        assert ent.profile == p.value
        assert ent.monthly_allowance > 0
