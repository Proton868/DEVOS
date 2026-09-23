"""World boundary proof suite — World A cannot reach World B."""
from __future__ import annotations

import pytest

from governance.world_context import (
    WorldContext,
    WorldBoundaryError,
    resolve_world_from_request,
    require_world_context,
)
from governance.economics import (
    get_economic_gateway,
    reset_economic_gateway_for_tests,
    EconomicError,
    EconomicDecision,
    PlanProfile,
)


@pytest.fixture(autouse=True)
def _reset():
    reset_economic_gateway_for_tests()
    yield
    reset_economic_gateway_for_tests()


def test_world_context_required_fields():
    with pytest.raises(WorldBoundaryError):
        WorldContext(world_id="", principal_id="u1")
    with pytest.raises(WorldBoundaryError):
        WorldContext(world_id="w1", principal_id="")
    ctx = WorldContext(world_id="world-a", principal_id="user-a")
    assert ctx.tenant_id == "world-a"


def test_client_supplied_world_not_in_membership_denied():
    with pytest.raises(WorldBoundaryError) as ei:
        resolve_world_from_request(
            authenticated_user_id="user-a",
            membership_tenant_ids={"world-a"},
            client_supplied_world_id="world-b",
        )
    assert ei.value.code == "CROSS_WORLD_DENIED"


def test_client_supplied_world_in_membership_ok():
    ctx = resolve_world_from_request(
        authenticated_user_id="user-a",
        membership_tenant_ids={"world-a", "world-c"},
        client_supplied_world_id="world-a",
    )
    assert ctx.world_id == "world-a"


def test_no_membership_fail_closed():
    with pytest.raises(WorldBoundaryError) as ei:
        resolve_world_from_request(
            authenticated_user_id="user-a",
            membership_tenant_ids=set(),
        )
    assert ei.value.code == "WORLD_REQUIRED"


def test_assert_same_world():
    ctx = WorldContext(world_id="world-a", principal_id="u1")
    ctx.assert_same_world("world-a", resource="mission")
    with pytest.raises(WorldBoundaryError) as ei:
        ctx.assert_same_world("world-b", resource="mission")
    assert ei.value.code == "CROSS_WORLD_DENIED"


def test_economic_cross_world_reservation_denied():
    gw = get_economic_gateway()
    gw.ensure_entitlement("world-a", profile=PlanProfile.OUTER_SECT.value)
    gw.ensure_entitlement("world-b", profile=PlanProfile.OUTER_SECT.value)
    rsv = gw.reserve(world_id="world-a", user_id="ua", estimated_credits=10, idempotency_key="k1")
    with pytest.raises(EconomicError) as ei:
        gw.reconcile(reservation_id=rsv.reservation_id, world_id="world-b", actual_credits=5)
    assert ei.value.code == "CROSS_WORLD_DENIED"


def test_world_a_cannot_spend_world_b_credits():
    gw = get_economic_gateway()
    a = gw.ensure_entitlement("world-a", profile=PlanProfile.RECRUIT.value)
    b = gw.ensure_entitlement("world-b", profile=PlanProfile.CORE.value)
    assert a.available < b.available
    # World A only sees its own balance
    rsv = gw.reserve(world_id="world-a", user_id="ua", estimated_credits=10)
    assert gw.get_entitlement("world-a").reserved_credits == 10
    assert gw.get_entitlement("world-b").reserved_credits == 0
    gw.reconcile(reservation_id=rsv.reservation_id, world_id="world-a", actual_credits=10)
    assert gw.get_entitlement("world-b").consumed_credits == 0


def test_usage_list_is_world_scoped():
    gw = get_economic_gateway()
    gw.ensure_entitlement("world-a")
    gw.ensure_entitlement("world-b")
    ra = gw.reserve(world_id="world-a", user_id="ua", estimated_credits=5, idempotency_key="a1")
    rb = gw.reserve(world_id="world-b", user_id="ub", estimated_credits=5, idempotency_key="b1")
    gw.reconcile(reservation_id=ra.reservation_id, world_id="world-a", actual_credits=5)
    gw.reconcile(reservation_id=rb.reservation_id, world_id="world-b", actual_credits=5)
    assert len(gw.list_usage("world-a")) == 1
    assert len(gw.list_usage("world-b")) == 1
    assert gw.list_usage("world-a")[0].world_id == "world-a"
