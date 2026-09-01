"""
Enterprise — RBAC Engine (Agency OS Master Plan §7).

Role-Based Access Control keyed off the UCIP trust_tier. Each tier maps to
a set of capabilities. The RBAC engine evaluates whether a given IdentityContext
is permitted to perform a given action.

Tier mapping:
  SYSTEM       — all capabilities, no restrictions
  AGENCY_OP    — all except system-level (secret.write, system.shell, vcs.push)
  TENANT_ADMIN — manage tenant users, filesystem, execution, api.call
  TENANT_USER  — filesystem.read, execution, api.call, memory.read/write
  PUBLIC       — public capabilities only, no secrets, no filesystem.delete
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from governance.identity_context import TenantTier, IdentityContext


class RBACDecision(str, Enum):
    ALLOW = "allow"
    DENY = "deny"
    ESCALATE = "escalate"


# Tier → capabilities mapping
TIER_CAPABILITIES: dict[TenantTier, set[str]] = {
    TenantTier.SYSTEM: {
        "ucip:execution.python", "ucip:execution.bash", "ucip:execution.node",
        "ucip:memory.read", "ucip:memory.write",
        "ucip:search.web",
        "ucip:filesystem.read", "ucip:filesystem.write", "ucip:filesystem.delete",
        "ucip:api.call",
        "ucip:agent.spawn",
        "ucip:secret.read", "ucip:system.shell",
        "ucip:vcs.write", "ucip:vcs.push",
    },
    TenantTier.AGENCY_OP: {
        "ucip:execution.python", "ucip:execution.bash", "ucip:execution.node",
        "ucip:memory.read", "ucip:memory.write",
        "ucip:search.web",
        "ucip:filesystem.read", "ucip:filesystem.write", "ucip:filesystem.delete",
        "ucip:api.call",
        "ucip:agent.spawn",
        "ucip:secret.read",
        "ucip:vcs.write",
    },
    TenantTier.TENANT_ADMIN: {
        "ucip:execution.python", "ucip:execution.bash", "ucip:execution.node",
        "ucip:memory.read", "ucip:memory.write",
        "ucip:search.web",
        "ucip:filesystem.read", "ucip:filesystem.write", "ucip:filesystem.delete",
        "ucip:api.call",
        "ucip:agent.spawn",
        "ucip:secret.read",
    },
    TenantTier.TENANT_USER: {
        "ucip:execution.python", "ucip:execution.bash",
        "ucip:memory.read", "ucip:memory.write",
        "ucip:search.web",
        "ucip:filesystem.read", "ucip:filesystem.write",
        "ucip:api.call",
    },
    TenantTier.PUBLIC: {
        "ucip:execution.python",
        "ucip:memory.read",
        "ucip:search.web",
        "ucip:filesystem.read",
        "ucip:api.call",
    },
}


@dataclass
class RBACResult:
    decision: RBACDecision
    reason: str
    required_tier: Optional[TenantTier] = None
    current_tier: Optional[TenantTier] = None


class RBACEngine:
    """Stateless RBAC evaluator. Evaluate whether an identity can perform a
    capability. Tier hierarchy is SYSTEM > AGENCY_OP > TENANT_ADMIN >
    TENANT_USER > PUBLIC."""

    _TIER_ORDER = [
        TenantTier.PUBLIC,
        TenantTier.TENANT_USER,
        TenantTier.TENANT_ADMIN,
        TenantTier.AGENCY_OP,
        TenantTier.SYSTEM,
    ]

    @classmethod
    def evaluate(cls, identity: IdentityContext, capability: str) -> RBACResult:
        """Evaluate whether the given identity can perform the capability."""
        tier = identity.trust_tier

        # Check if the capability is in the tier's allowed set
        if capability in TIER_CAPABILITIES.get(tier, set()):
            return RBACResult(
                decision=RBACDecision.ALLOW,
                reason=f"Capability '{capability}' allowed for tier '{tier.value}'",
                current_tier=tier,
            )

        # Find the minimum tier required
        for required_tier in cls._TIER_ORDER:
            if capability in TIER_CAPABILITIES.get(required_tier, set()):
                if cls._tier_rank(tier) >= cls._tier_rank(required_tier):
                    return RBACResult(
                        decision=RBACDecision.ALLOW,
                        reason=f"Capability '{capability}' allowed via tier hierarchy",
                        required_tier=required_tier,
                        current_tier=tier,
                    )
                return RBACResult(
                    decision=RBACDecision.DENY,
                    reason=f"Capability '{capability}' requires tier '{required_tier.value}' (current: '{tier.value}')",
                    required_tier=required_tier,
                    current_tier=tier,
                )

        return RBACResult(
            decision=RBACDecision.DENY,
            reason=f"Unknown capability: '{capability}'",
            current_tier=tier,
        )

    @classmethod
    def evaluate_capability_tokens(cls, identity: IdentityContext,
                                    capability: str) -> RBACResult:
        """Check if the identity has explicit capability tokens that grant
        access beyond their tier. Capability tokens are additive — they can
        only grant, never revoke."""
        tier_result = cls.evaluate(identity, capability)
        if tier_result.decision == RBACDecision.ALLOW:
            return tier_result

        # Check explicit tokens (CapabilityToken carries `caps` as a list[str])
        for token in identity.capability_tokens:
            if capability in token.caps:
                if not token.is_expired():
                    return RBACResult(
                        decision=RBACDecision.ALLOW,
                        reason=f"Capability '{capability}' granted by explicit token (issued by {token.issued_by})",
                        current_tier=identity.trust_tier,
                    )
                return RBACResult(
                    decision=RBACDecision.DENY,
                    reason=f"Capability token for '{capability}' is expired",
                    current_tier=identity.trust_tier,
                )

        return tier_result

    @classmethod
    def _tier_rank(cls, tier: TenantTier) -> int:
        """Return numeric rank for tier comparison."""
        return {
            TenantTier.PUBLIC: 0,
            TenantTier.TENANT_USER: 1,
            TenantTier.TENANT_ADMIN: 2,
            TenantTier.AGENCY_OP: 3,
            TenantTier.SYSTEM: 4,
        }.get(tier, 0)

    @classmethod
    def list_capabilities_for_tier(cls, tier: TenantTier) -> list[str]:
        """List all capabilities available to a given tier."""
        return sorted(TIER_CAPABILITIES.get(tier, set()))


# Singleton
_rbac = RBACEngine()


def get_rbac() -> RBACEngine:
    return _rbac


def evaluate_rbac(identity: IdentityContext, capability: str) -> RBACResult:
    """Convenience: evaluate RBAC with capability tokens."""
    return RBACEngine.evaluate_capability_tokens(identity, capability)