"""
Governed economic domain (Jev).

Answers: «Is this operation economically authorized under the world's entitlement?»
Does NOT answer commercial billing.

Lifecycle:
  ESTIMATE → RESERVE → EXECUTE → RECONCILE

Invariant:
  world-scoped · idempotent · no double-spend · no double-count · fail-closed
"""
from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class EconomicDecision(str, Enum):
    ALLOW = "ALLOW"
    DENY = "DENY"
    REQUIRE_APPROVAL = "REQUIRE_APPROVAL"
    REQUIRE_BYOK = "REQUIRE_BYOK"
    DOWNGRADE_MODEL = "DOWNGRADE_MODEL"
    REDUCE_SCOPE = "REDUCE_SCOPE"
    PAUSE = "PAUSE"


class ReservationStatus(str, Enum):
    ESTIMATED = "ESTIMATED"
    RESERVED = "RESERVED"
    CONSUMED = "CONSUMED"
    RELEASED = "RELEASED"
    EXPIRED = "EXPIRED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class PlanProfile(str, Enum):
    RECRUIT = "RECRUIT"
    OUTER_SECT = "OUTER_SECT"
    INNER_SECT = "INNER_SECT"
    CORE = "CORE"
    CONCLAVE = "CONCLAVE"


class EconomicError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


# Default entitlement profiles (capability-based, not plan-name branching in callers)
_DEFAULT_ENTITLEMENTS: dict[str, dict[str, Any]] = {
    PlanProfile.RECRUIT.value: {
        "monthly_allowance": 500,
        "max_mission_cost": 50,
        "max_concurrent_agents": 2,
        "premium_allowance": 0,
        "byok_enabled": True,
        "model_access": ["free", "local"],
        "runtime_limit_s": 600,
    },
    PlanProfile.OUTER_SECT.value: {
        "monthly_allowance": 5000,
        "max_mission_cost": 500,
        "max_concurrent_agents": 5,
        "premium_allowance": 200,
        "byok_enabled": True,
        "model_access": ["free", "local", "standard"],
        "runtime_limit_s": 3600,
    },
    PlanProfile.INNER_SECT.value: {
        "monthly_allowance": 20000,
        "max_mission_cost": 2000,
        "max_concurrent_agents": 15,
        "premium_allowance": 2000,
        "byok_enabled": True,
        "model_access": ["free", "local", "standard", "premium"],
        "runtime_limit_s": 14400,
    },
    PlanProfile.CORE.value: {
        "monthly_allowance": 100000,
        "max_mission_cost": 10000,
        "max_concurrent_agents": 50,
        "premium_allowance": 20000,
        "byok_enabled": True,
        "model_access": ["free", "local", "standard", "premium"],
        "runtime_limit_s": 86400,
    },
    PlanProfile.CONCLAVE.value: {
        "monthly_allowance": 1_000_000,
        "max_mission_cost": 100_000,
        "max_concurrent_agents": 200,
        "premium_allowance": 200_000,
        "byok_enabled": True,
        "model_access": ["free", "local", "standard", "premium", "enterprise"],
        "runtime_limit_s": 604800,
        "organization_features": True,
    },
}


@dataclass
class Entitlement:
    world_id: str
    profile: str = PlanProfile.RECRUIT.value
    monthly_allowance: float = 500
    consumed_credits: float = 0.0
    reserved_credits: float = 0.0
    max_mission_cost: float = 50
    max_concurrent_agents: int = 2
    premium_allowance: float = 0
    byok_enabled: bool = True
    model_access: list[str] = field(default_factory=lambda: ["free", "local"])
    runtime_limit_s: int = 600
    beta_override: bool = False
    organization_features: bool = False

    @property
    def available(self) -> float:
        return max(0.0, float(self.monthly_allowance) - float(self.consumed_credits) - float(self.reserved_credits))

    def to_dict(self) -> dict:
        return {
            "world_id": self.world_id,
            "profile": self.profile,
            "monthly_allowance": self.monthly_allowance,
            "consumed_credits": self.consumed_credits,
            "reserved_credits": self.reserved_credits,
            "available": self.available,
            "max_mission_cost": self.max_mission_cost,
            "max_concurrent_agents": self.max_concurrent_agents,
            "premium_allowance": self.premium_allowance,
            "byok_enabled": self.byok_enabled,
            "model_access": list(self.model_access),
            "runtime_limit_s": self.runtime_limit_s,
            "beta_override": self.beta_override,
            "organization_features": self.organization_features,
        }


@dataclass
class EconomicReservation:
    reservation_id: str
    world_id: str
    user_id: str
    mission_id: Optional[str]
    policy_version: str
    estimated_credits: float
    reserved_credits: float
    consumed_credits: float = 0.0
    released_credits: float = 0.0
    status: ReservationStatus = ReservationStatus.RESERVED
    created_at: float = field(default_factory=time.time)
    expires_at: Optional[float] = None
    idempotency_key: Optional[str] = None
    org_id: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "reservation_id": self.reservation_id,
            "world_id": self.world_id,
            "user_id": self.user_id,
            "mission_id": self.mission_id,
            "policy_version": self.policy_version,
            "estimated_credits": self.estimated_credits,
            "reserved_credits": self.reserved_credits,
            "consumed_credits": self.consumed_credits,
            "released_credits": self.released_credits,
            "status": self.status.value,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "idempotency_key": self.idempotency_key,
            "org_id": self.org_id,
        }


@dataclass
class UsageEvent:
    usage_event_id: str
    reservation_id: str
    world_id: str
    user_id: str
    mission_id: Optional[str]
    provider: Optional[str]
    model: Optional[str]
    operation: str
    input_units: float
    output_units: float
    tool_calls: int
    runtime_ms: int
    credits_consumed: float
    actual_cost: float
    started_at: float
    completed_at: float
    evidence_id: Optional[str]
    outcome: str
    agent_id: Optional[str] = None
    org_id: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "usage_event_id": self.usage_event_id,
            "reservation_id": self.reservation_id,
            "world_id": self.world_id,
            "user_id": self.user_id,
            "mission_id": self.mission_id,
            "agent_id": self.agent_id,
            "org_id": self.org_id,
            "provider": self.provider,
            "model": self.model,
            "operation": self.operation,
            "input_units": self.input_units,
            "output_units": self.output_units,
            "tool_calls": self.tool_calls,
            "runtime_ms": self.runtime_ms,
            "credits_consumed": self.credits_consumed,
            "actual_cost": self.actual_cost,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "evidence_id": self.evidence_id,
            "outcome": self.outcome,
        }


class EconomicGateway:
    """In-process durable economic gateway (world-scoped). Thread-safe."""

    def __init__(self):
        self._lock = threading.RLock()
        self._entitlements: dict[str, Entitlement] = {}
        self._reservations: dict[str, EconomicReservation] = {}
        self._usage: dict[str, UsageEvent] = {}
        self._idempotency: dict[str, str] = {}  # key → reservation_id
        self._policy_version = "jev-v1"

    def reset_for_tests(self) -> None:
        with self._lock:
            self._entitlements.clear()
            self._reservations.clear()
            self._usage.clear()
            self._idempotency.clear()

    def ensure_entitlement(
        self,
        world_id: str,
        *,
        profile: str = PlanProfile.RECRUIT.value,
        beta_override: bool = False,
    ) -> Entitlement:
        with self._lock:
            if world_id in self._entitlements:
                ent = self._entitlements[world_id]
                if beta_override:
                    ent.beta_override = True
                    # modest boost for beta without separate code path
                    ent.monthly_allowance = max(ent.monthly_allowance, 1000)
                return ent
            base = dict(_DEFAULT_ENTITLEMENTS.get(profile, _DEFAULT_ENTITLEMENTS[PlanProfile.RECRUIT.value]))
            ent = Entitlement(
                world_id=world_id,
                profile=profile,
                monthly_allowance=float(base.get("monthly_allowance", 500)),
                max_mission_cost=float(base.get("max_mission_cost", 50)),
                max_concurrent_agents=int(base.get("max_concurrent_agents", 2)),
                premium_allowance=float(base.get("premium_allowance", 0)),
                byok_enabled=bool(base.get("byok_enabled", True)),
                model_access=list(base.get("model_access") or ["free"]),
                runtime_limit_s=int(base.get("runtime_limit_s", 600)),
                beta_override=beta_override,
                organization_features=bool(base.get("organization_features", False)),
            )
            if beta_override:
                ent.monthly_allowance = max(ent.monthly_allowance, 1000)
            self._entitlements[world_id] = ent
            return ent

    def get_entitlement(self, world_id: str) -> Optional[Entitlement]:
        with self._lock:
            return self._entitlements.get(world_id)

    def estimate(
        self,
        *,
        world_id: str,
        user_id: str,
        operation: str,
        estimated_credits: float,
        mission_id: Optional[str] = None,
        require_premium: bool = False,
    ) -> dict:
        if not world_id or not user_id:
            raise EconomicError("WORLD_REQUIRED", "world_id and user_id required")
        if estimated_credits < 0:
            raise EconomicError("INVALID_ESTIMATE", "estimated_credits must be >= 0")
        ent = self.ensure_entitlement(world_id)
        if estimated_credits > ent.max_mission_cost:
            return {
                "decision": EconomicDecision.REDUCE_SCOPE.value,
                "reason": "exceeds_max_mission_cost",
                "estimated_credits": estimated_credits,
                "max_mission_cost": ent.max_mission_cost,
            }
        if require_premium and "premium" not in ent.model_access and not ent.byok_enabled:
            return {
                "decision": EconomicDecision.REQUIRE_BYOK.value,
                "reason": "premium_requires_byok_or_entitlement",
                "estimated_credits": estimated_credits,
            }
        if require_premium and "premium" not in ent.model_access and ent.byok_enabled:
            return {
                "decision": EconomicDecision.REQUIRE_BYOK.value,
                "reason": "premium_not_in_allowance",
                "estimated_credits": estimated_credits,
            }
        if estimated_credits > ent.available:
            return {
                "decision": EconomicDecision.DENY.value,
                "reason": "insufficient_allowance",
                "estimated_credits": estimated_credits,
                "available": ent.available,
            }
        return {
            "decision": EconomicDecision.ALLOW.value,
            "reason": "within_allowance",
            "estimated_credits": estimated_credits,
            "available": ent.available,
            "policy_version": self._policy_version,
        }

    def reserve(
        self,
        *,
        world_id: str,
        user_id: str,
        estimated_credits: float,
        mission_id: Optional[str] = None,
        idempotency_key: Optional[str] = None,
        ttl_s: float = 3600,
        org_id: Optional[str] = None,
    ) -> EconomicReservation:
        if not world_id or not user_id:
            raise EconomicError("WORLD_REQUIRED", "world_id and user_id required")
        if estimated_credits < 0:
            raise EconomicError("INVALID_RESERVE", "estimated_credits must be >= 0")

        with self._lock:
            if idempotency_key:
                ik = f"{world_id}:{idempotency_key}"
                existing_id = self._idempotency.get(ik)
                if existing_id and existing_id in self._reservations:
                    return self._reservations[existing_id]

            decision = self.estimate(
                world_id=world_id,
                user_id=user_id,
                operation="reserve",
                estimated_credits=estimated_credits,
                mission_id=mission_id,
            )
            if decision["decision"] != EconomicDecision.ALLOW.value:
                raise EconomicError(decision["decision"], decision.get("reason", "denied"))

            ent = self.ensure_entitlement(world_id)
            # Atomic hold
            if estimated_credits > ent.available:
                raise EconomicError("DENY", "insufficient_allowance")
            ent.reserved_credits += estimated_credits

            rid = "rsv_" + uuid.uuid4().hex[:16]
            rec = EconomicReservation(
                reservation_id=rid,
                world_id=world_id,
                user_id=user_id,
                mission_id=mission_id,
                policy_version=self._policy_version,
                estimated_credits=estimated_credits,
                reserved_credits=estimated_credits,
                status=ReservationStatus.RESERVED,
                expires_at=time.time() + ttl_s,
                idempotency_key=idempotency_key,
                org_id=org_id,
            )
            self._reservations[rid] = rec
            if idempotency_key:
                self._idempotency[f"{world_id}:{idempotency_key}"] = rid
            return rec

    def reconcile(
        self,
        *,
        reservation_id: str,
        world_id: str,
        actual_credits: float,
        outcome: str = "success",
        provider: Optional[str] = None,
        model: Optional[str] = None,
        operation: str = "execute",
        input_units: float = 0,
        output_units: float = 0,
        tool_calls: int = 0,
        runtime_ms: int = 0,
        evidence_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        usage_idempotency_key: Optional[str] = None,
    ) -> tuple[EconomicReservation, UsageEvent]:
        if actual_credits < 0:
            raise EconomicError("INVALID_USAGE", "actual_credits must be >= 0")

        with self._lock:
            rec = self._reservations.get(reservation_id)
            if not rec:
                raise EconomicError("NOT_FOUND", "reservation not found")
            if rec.world_id != world_id:
                raise EconomicError("CROSS_WORLD_DENIED", "reservation world mismatch")
            if rec.status in (ReservationStatus.CONSUMED, ReservationStatus.RELEASED):
                # Idempotent: return existing usage if any
                for u in self._usage.values():
                    if u.reservation_id == reservation_id and u.world_id == world_id:
                        return rec, u
                raise EconomicError("ALREADY_FINAL", f"reservation already {rec.status.value}")
            if rec.status == ReservationStatus.CANCELLED:
                raise EconomicError("CANCELLED", "reservation cancelled")
            if rec.expires_at and time.time() > rec.expires_at and rec.status == ReservationStatus.RESERVED:
                # expire hold
                ent = self.ensure_entitlement(world_id)
                ent.reserved_credits = max(0.0, ent.reserved_credits - rec.reserved_credits)
                rec.status = ReservationStatus.EXPIRED
                raise EconomicError("EXPIRED", "reservation expired")

            ent = self.ensure_entitlement(world_id)
            # Release reserved hold, then consume actual
            ent.reserved_credits = max(0.0, ent.reserved_credits - rec.reserved_credits)
            consume = min(actual_credits, rec.reserved_credits) if outcome != "success" else actual_credits
            # Allow over-consumption accounting but never negative available via clamp
            ent.consumed_credits += consume
            released = max(0.0, rec.reserved_credits - consume)
            rec.consumed_credits = consume
            rec.released_credits = released
            rec.status = ReservationStatus.CONSUMED if consume > 0 else ReservationStatus.RELEASED

            ueid = "use_" + uuid.uuid4().hex[:16]
            if usage_idempotency_key:
                for u in self._usage.values():
                    if u.reservation_id == reservation_id and u.outcome == outcome:
                        return rec, u
            event = UsageEvent(
                usage_event_id=ueid,
                reservation_id=reservation_id,
                world_id=world_id,
                user_id=rec.user_id,
                mission_id=rec.mission_id,
                provider=provider,
                model=model,
                operation=operation,
                input_units=input_units,
                output_units=output_units,
                tool_calls=tool_calls,
                runtime_ms=runtime_ms,
                credits_consumed=consume,
                actual_cost=actual_credits,
                started_at=rec.created_at,
                completed_at=time.time(),
                evidence_id=evidence_id,
                outcome=outcome,
                agent_id=agent_id,
                org_id=rec.org_id,
            )
            self._usage[ueid] = event
            return rec, event

    def cancel_reservation(self, *, reservation_id: str, world_id: str) -> EconomicReservation:
        with self._lock:
            rec = self._reservations.get(reservation_id)
            if not rec:
                raise EconomicError("NOT_FOUND", "reservation not found")
            if rec.world_id != world_id:
                raise EconomicError("CROSS_WORLD_DENIED", "reservation world mismatch")
            if rec.status in (ReservationStatus.CONSUMED, ReservationStatus.RELEASED, ReservationStatus.CANCELLED):
                return rec
            ent = self.ensure_entitlement(world_id)
            ent.reserved_credits = max(0.0, ent.reserved_credits - rec.reserved_credits)
            rec.released_credits = rec.reserved_credits
            rec.reserved_credits = 0.0
            rec.status = ReservationStatus.CANCELLED
            return rec

    def list_usage(self, world_id: str) -> list[UsageEvent]:
        with self._lock:
            return [u for u in self._usage.values() if u.world_id == world_id]

    def list_reservations(self, world_id: str) -> list[EconomicReservation]:
        with self._lock:
            return [r for r in self._reservations.values() if r.world_id == world_id]


_GATEWAY: Optional[EconomicGateway] = None
_G_LOCK = threading.Lock()


def get_economic_gateway() -> EconomicGateway:
    global _GATEWAY
    with _G_LOCK:
        if _GATEWAY is None:
            _GATEWAY = EconomicGateway()
        return _GATEWAY


def reset_economic_gateway_for_tests() -> None:
    get_economic_gateway().reset_for_tests()
