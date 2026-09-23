"""
UCIP integration helper for economic governance.

Insert into:
  Intent → Plan → Policy → Estimate → Authorization → Reservation
  → Capability → Execution → Evidence → Usage → Reconciliation → Outcome
"""
from __future__ import annotations

from typing import Any, Optional

from governance.economics import (
    EconomicDecision,
    EconomicError,
    EconomicReservation,
    UsageEvent,
    get_economic_gateway,
)
from governance.world_context import WorldContext, WorldBoundaryError, require_world_context


def authorize_and_reserve(
    world: WorldContext,
    *,
    estimated_credits: float,
    operation: str = "capability",
    mission_id: Optional[str] = None,
    idempotency_key: Optional[str] = None,
    require_premium: bool = False,
) -> EconomicReservation:
    """Estimate + reserve before metered execution. Fail closed."""
    world = require_world_context(world)
    gw = get_economic_gateway()
    gw.ensure_entitlement(world.world_id)
    est = gw.estimate(
        world_id=world.world_id,
        user_id=world.principal_id,
        operation=operation,
        estimated_credits=estimated_credits,
        mission_id=mission_id or world.mission_id,
        require_premium=require_premium,
    )
    if est["decision"] != EconomicDecision.ALLOW.value:
        raise EconomicError(est["decision"], est.get("reason", "denied"))
    return gw.reserve(
        world_id=world.world_id,
        user_id=world.principal_id,
        estimated_credits=estimated_credits,
        mission_id=mission_id or world.mission_id,
        idempotency_key=idempotency_key,
    )


def reconcile_execution(
    world: WorldContext,
    reservation: EconomicReservation,
    *,
    actual_credits: float,
    outcome: str = "success",
    evidence_id: Optional[str] = None,
    provider: Optional[str] = None,
    model: Optional[str] = None,
    operation: str = "capability",
) -> tuple[EconomicReservation, UsageEvent]:
    world = require_world_context(world)
    if reservation.world_id != world.world_id:
        raise WorldBoundaryError("CROSS_WORLD_DENIED", "reservation outside world")
    gw = get_economic_gateway()
    return gw.reconcile(
        reservation_id=reservation.reservation_id,
        world_id=world.world_id,
        actual_credits=actual_credits,
        outcome=outcome,
        evidence_id=evidence_id,
        provider=provider,
        model=model,
        operation=operation,
    )
