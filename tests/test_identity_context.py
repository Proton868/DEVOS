"""Test IdentityContext — UCIP identity model with tenant awareness and capability tokens."""
import pytest
from datetime import datetime, timezone, timedelta

from governance.identity_context import (
    IdentityContext, TenantTier, CapabilityToken, resolve_identity,
)
from governance.ucip import AgentIdentity, TrustLevel


class TestCapabilityToken:
    def test_token_creation(self):
        token = CapabilityToken(
            token_id="tok-1",
            caps=["ucip:execution.python"],
            issued_by="system",
        )
        assert token.token_id == "tok-1"
        assert token.caps == ["ucip:execution.python"]
        assert token.issued_by == "system"
        assert token.issued_at is not None

    def test_is_expired_without_expiry(self):
        token = CapabilityToken(token_id="t1", caps=[], issued_by="sys")
        assert not token.is_expired()

    def test_is_expired_future(self):
        token = CapabilityToken(
            token_id="t1", caps=[], issued_by="sys",
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        assert not token.is_expired()

    def test_is_expired_past(self):
        token = CapabilityToken(
            token_id="t1", caps=[], issued_by="sys",
            expires_at=datetime.now(timezone.utc) - timedelta(hours=1),
        )
        assert token.is_expired()

    def test_to_dict(self):
        token = CapabilityToken(
            token_id="tok-1",
            caps=["ucip:execution.python"],
            issued_by="system",
            evidence_ref="ev-1",
        )
        d = token.to_dict()
        assert d["token_id"] == "tok-1"
        assert d["caps"] == ["ucip:execution.python"]
        assert d["issued_by"] == "system"
        assert d["evidence_ref"] == "ev-1"


class TestIdentityContextCreation:
    def test_default_creation(self):
        ctx = IdentityContext(
            actor_id="user-1",
            tenant_id="tenant-1",
            trust_tier=TenantTier.TENANT_USER,
        )
        assert ctx.actor_id == "user-1"
        assert ctx.tenant_id == "tenant-1"
        assert ctx.trust_tier == TenantTier.TENANT_USER
        assert ctx.request_id is not None
        assert len(ctx.request_id) == 16

    def test_from_agent(self):
        agent = AgentIdentity.create("user-1", "session-1", trust_level=TrustLevel.OPERATOR)
        ctx = IdentityContext.from_agent(agent, tenant_id="t1", trust_tier=TenantTier.AGENCY_OP)
        assert ctx.actor_id == agent.agent_id
        assert ctx.tenant_id == "t1"
        assert ctx.trust_tier == TenantTier.AGENCY_OP
        assert ctx.agent_identity is agent
        assert len(ctx.capability_tokens) == 1
        assert ctx.capability_tokens[0].issued_by == "system"

    def test_resolve_identity_defaults(self):
        """OPERATOR (3) < AUTONOMOUS (4), so default maps to TENANT_USER."""
        ctx = resolve_identity("user-1", "session-1")
        assert ctx.actor_id is not None
        assert ctx.tenant_id == "default"
        # OPERATOR trust level is below AUTONOMOUS, so tier is TENANT_USER
        assert ctx.trust_tier == TenantTier.TENANT_USER

    def test_resolve_identity_read_only(self):
        ctx = resolve_identity("user-1", "session-1", trust_level=TrustLevel.READ_ONLY)
        assert ctx.trust_tier == TenantTier.TENANT_USER

    def test_resolve_identity_autonomous(self):
        ctx = resolve_identity("user-1", "session-1", trust_level=TrustLevel.AUTONOMOUS)
        assert ctx.trust_tier == TenantTier.AGENCY_OP


class TestIdentityContextCapabilities:
    def test_effective_capabilities_empty(self):
        ctx = IdentityContext(actor_id="u1", tenant_id="t1", trust_tier=TenantTier.TENANT_USER)
        assert ctx.effective_capabilities() == set()

    def test_effective_capabilities_with_tokens(self):
        token = CapabilityToken(
            token_id="t1", caps=["ucip:execution.python", "ucip:memory.read"],
            issued_by="sys",
        )
        ctx = IdentityContext(
            actor_id="u1", tenant_id="t1", trust_tier=TenantTier.TENANT_USER,
            capability_tokens=[token],
        )
        caps = ctx.effective_capabilities()
        assert "ucip:execution.python" in caps
        assert "ucip:memory.read" in caps

    def test_effective_capabilities_excludes_expired(self):
        expired = CapabilityToken(
            token_id="t1", caps=["ucip:execution.python"], issued_by="sys",
            expires_at=datetime.now(timezone.utc) - timedelta(hours=1),
        )
        valid = CapabilityToken(
            token_id="t2", caps=["ucip:memory.read"], issued_by="sys",
        )
        ctx = IdentityContext(
            actor_id="u1", tenant_id="t1", trust_tier=TenantTier.TENANT_USER,
            capability_tokens=[expired, valid],
        )
        caps = ctx.effective_capabilities()
        assert "ucip:execution.python" not in caps
        assert "ucip:memory.read" in caps

    def test_has_cap(self):
        token = CapabilityToken(token_id="t1", caps=["ucip:execution.python"], issued_by="sys")
        ctx = IdentityContext(actor_id="u1", tenant_id="t1", trust_tier=TenantTier.TENANT_USER,
                              capability_tokens=[token])
        assert ctx.has_cap("ucip:execution.python")
        assert not ctx.has_cap("ucip:secret.read")


class TestIdentityContextTierCheck:
    def test_can_act_as_same_tier(self):
        ctx = IdentityContext(actor_id="u1", tenant_id="t1", trust_tier=TenantTier.TENANT_ADMIN)
        assert ctx.can_act_as(TenantTier.TENANT_ADMIN)
        assert ctx.can_act_as(TenantTier.TENANT_USER)
        assert ctx.can_act_as(TenantTier.PUBLIC)

    def test_cannot_act_as_higher_tier(self):
        ctx = IdentityContext(actor_id="u1", tenant_id="t1", trust_tier=TenantTier.TENANT_USER)
        assert not ctx.can_act_as(TenantTier.TENANT_ADMIN)
        assert not ctx.can_act_as(TenantTier.AGENCY_OP)
        assert not ctx.can_act_as(TenantTier.SYSTEM)

    def test_system_can_act_as_all(self):
        ctx = IdentityContext(actor_id="u1", tenant_id="t1", trust_tier=TenantTier.SYSTEM)
        for tier in TenantTier:
            assert ctx.can_act_as(tier)


class TestIdentityContextToDict:
    def test_to_dict_roundtrip_info(self):
        ctx = IdentityContext(
            actor_id="u1", tenant_id="t1", trust_tier=TenantTier.TENANT_USER,
            session_id="s1", delegation_chain=["d1", "d2"],
            expected_outcome_schema={"type": "object"},
        )
        d = ctx.to_dict()
        assert d["actor_id"] == "u1"
        assert d["tenant_id"] == "t1"
        assert d["trust_tier"] == "tenant_user"
        assert d["session_id"] == "s1"
        assert d["delegation_chain"] == ["d1", "d2"]
        assert d["expected_outcome_schema"] == {"type": "object"}