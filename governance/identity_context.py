"""
Governance — IdentityContext (Agency OS Master Plan §2).

Closes the gap: UCIP v0.1's IntentRequest only carries `actor` as a hint.
This module adds the full IdentityContext as specified in the master plan,
extending the existing AgentIdentity (governance/ucip.py) with tenant
awareness, formal trust tier typing, capability tokens, and expected outcome
schemas — all wired into the execution model:

IntentRequest(+IdentityContext) → ExecutionPlan (worker/capability selection
gated by trust_tier) → Capability Execution → EvidenceChain → Outcome
(validated against expected_outcome_schema)
"""
from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from governance.ucip import TrustLevel, AgentIdentity  # noqa: F401 — re-exported


class TenantTier(str, Enum):
    """Per-tenant trust classification. Controls which capabilities a tenant
    can access regardless of individual user trust levels."""
    PUBLIC       = "public"         # Unauthenticated visitors
    TENANT_USER  = "tenant_user"    # Member of a tenant org
    TENANT_ADMIN = "tenant_admin"   # Admin of a tenant org
    AGENCY_OP    = "agency_operator" # Agency OS platform operator
    SYSTEM       = "system"         # The platform itself (internal)


@dataclass
class CapabilityToken:
    """A signed grant of one or more capabilities to an actor. Each token
    carries its own expiry, issuer, and evidence reference so Audit can
    trace every capability grant back to its source — not just the actor
    making the call, but who authorized them to make it."""
    token_id:    str
    caps:        list[str]          # e.g. ["ucip:execution.python", "ucip:memory.read"]
    issued_by:   str                # agent_id or system
    issued_at:   datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at:  Optional[datetime] = None
    evidence_ref: Optional[str] = None  # link to EvidenceChain node

    def is_expired(self) -> bool:
        if self.expires_at is None:
            return False
        return datetime.now(timezone.utc) > self.expires_at

    def to_dict(self) -> dict:
        return {
            "token_id": self.token_id,
            "caps": self.caps,
            "issued_by": self.issued_by,
            "issued_at": self.issued_at.isoformat(),
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "evidence_ref": self.evidence_ref,
        }


@dataclass
class IdentityContext:
    """Full identity context attached to every IntentRequest, per the
    Agency OS Master Plan §2. This extends the bare `actor` field of
    UCIP v0.1 with tenant awareness, trust tier, capability tokens,
    expected outcome schema, and full delegation lineage.

    Wire this into the execution model as:
      IntentRequest(+IdentityContext) → ExecutionPlan
      → Capability Execution → EvidenceChain → Outcome
    """
    actor_id:            str
    tenant_id:           str
    trust_tier:          TenantTier
    capability_tokens:   list[CapabilityToken] = field(default_factory=list)
    expected_outcome_schema: Optional[dict] = None
    delegation_chain:    list[str] = field(default_factory=list)
    agent_identity:      Optional[AgentIdentity] = None  # resolved from actor_id
    session_id:          Optional[str] = None
    request_id:          str = field(default_factory=lambda: hashlib.sha256(
        f"{time.time()}:{datetime.now(timezone.utc).isoformat()}".encode()
    ).hexdigest()[:16])

    @classmethod
    def from_agent(cls, agent: AgentIdentity, tenant_id: str = "default",
                   trust_tier: TenantTier = TenantTier.AGENCY_OP) -> "IdentityContext":
        """Create an IdentityContext from an existing AgentIdentity, bridging
        the current UCIP model to the new IdentityContext model without
        breaking existing callers."""
        caps = [CapabilityToken(
            token_id=f"ctx:{agent.agent_id}",
            caps=list(agent.capabilities),
            issued_by="system",
        )]
        return cls(
            actor_id=agent.agent_id,
            tenant_id=tenant_id,
            trust_tier=trust_tier,
            capability_tokens=caps,
            delegation_chain=agent.delegation_chain,
            agent_identity=agent,
            session_id=agent.session_id,
        )

    def effective_capabilities(self) -> set[str]:
        """All capabilities currently active across all unexpired tokens."""
        caps: set[str] = set()
        for token in self.capability_tokens:
            if not token.is_expired():
                caps.update(token.caps)
        return caps

    def has_cap(self, cap: str) -> bool:
        return cap in self.effective_capabilities()

    def can_act_as(self, required_tier: TenantTier) -> bool:
        """Check if this identity's trust tier meets or exceeds a required tier."""
        tier_order = {
            TenantTier.PUBLIC: 0,
            TenantTier.TENANT_USER: 1,
            TenantTier.TENANT_ADMIN: 2,
            TenantTier.AGENCY_OP: 3,
            TenantTier.SYSTEM: 4,
        }
        return tier_order.get(self.trust_tier, 0) >= tier_order.get(required_tier, 0)

    def to_dict(self) -> dict:
        return {
            "actor_id": self.actor_id,
            "tenant_id": self.tenant_id,
            "trust_tier": self.trust_tier.value,
            "capability_tokens": [t.to_dict() for t in self.capability_tokens],
            "expected_outcome_schema": self.expected_outcome_schema,
            "delegation_chain": self.delegation_chain,
            "session_id": self.session_id,
            "request_id": self.request_id,
        }


def resolve_identity(user_id: str, session_id: str, tenant_id: str = "default",
                     trust_level: TrustLevel = TrustLevel.OPERATOR) -> IdentityContext:
    """Convenience: create an AgentIdentity and wrap it in IdentityContext
    in one call. This is the standard entry point for new code — existing
    callers that already create AgentIdentity separately can use
    IdentityContext.from_agent() instead."""
    agent = AgentIdentity.create(user_id, session_id, trust_level=trust_level)
    tier = TenantTier.AGENCY_OP if trust_level >= TrustLevel.AUTONOMOUS else TenantTier.TENANT_USER
    return IdentityContext.from_agent(agent, tenant_id=tenant_id, trust_tier=tier)