"""Test RBAC engine — tier-based capability evaluation and token augmentation."""
import pytest
from datetime import datetime, timezone, timedelta

from governance.identity_context import IdentityContext, TenantTier, CapabilityToken
from governance.rbac import RBACEngine, RBACDecision, TIER_CAPABILITIES


def _make_identity(tier: TenantTier, tokens: list = None) -> IdentityContext:
    return IdentityContext(
        actor_id="test-user",
        tenant_id="test-tenant",
        trust_tier=tier,
        capability_tokens=tokens or [],
    )


class TestTierCapabilities:
    """Verify each tier maps to the correct capability set."""

    def test_system_has_all_capabilities(self):
        caps = TIER_CAPABILITIES[TenantTier.SYSTEM]
        assert "ucip:execution.python" in caps
        assert "ucip:system.shell" in caps
        assert "ucip:secret.read" in caps
        assert "ucip:vcs.push" in caps

    def test_public_has_limited_capabilities(self):
        caps = TIER_CAPABILITIES[TenantTier.PUBLIC]
        assert "ucip:execution.python" in caps
        assert "ucip:memory.read" in caps
        assert "ucip:filesystem.read" in caps
        assert "ucip:secret.read" not in caps
        assert "ucip:system.shell" not in caps
        assert "ucip:filesystem.delete" not in caps

    def test_tenant_user_has_more_than_public(self):
        public = TIER_CAPABILITIES[TenantTier.PUBLIC]
        user = TIER_CAPABILITIES[TenantTier.TENANT_USER]
        assert len(user) > len(public)
        assert public.issubset(user)

    def test_tier_hierarchy_is_strict_subset(self):
        """Each tier is a strict superset of the tier below it."""
        tiers = [TenantTier.PUBLIC, TenantTier.TENANT_USER, TenantTier.TENANT_ADMIN,
                 TenantTier.AGENCY_OP, TenantTier.SYSTEM]
        for i in range(len(tiers) - 1):
            lower = TIER_CAPABILITIES[tiers[i]]
            higher = TIER_CAPABILITIES[tiers[i + 1]]
            assert lower.issubset(higher), f"{tiers[i].value} not subset of {tiers[i+1].value}"


class TestRBACEngineEvaluate:
    """Core RBAC evaluation — tier-based decisions."""

    def test_system_allows_all(self):
        identity = _make_identity(TenantTier.SYSTEM)
        result = RBACEngine.evaluate(identity, "ucip:system.shell")
        assert result.decision == RBACDecision.ALLOW

    def test_public_denies_secret_read(self):
        identity = _make_identity(TenantTier.PUBLIC)
        result = RBACEngine.evaluate(identity, "ucip:secret.read")
        assert result.decision == RBACDecision.DENY

    def test_tenant_user_allows_execution(self):
        identity = _make_identity(TenantTier.TENANT_USER)
        result = RBACEngine.evaluate(identity, "ucip:execution.python")
        assert result.decision == RBACDecision.ALLOW

    def test_tenant_user_denies_vcs_push(self):
        identity = _make_identity(TenantTier.TENANT_USER)
        result = RBACEngine.evaluate(identity, "ucip:vcs.push")
        assert result.decision == RBACDecision.DENY

    def test_unknown_capability_denies(self):
        identity = _make_identity(TenantTier.TENANT_USER)
        result = RBACEngine.evaluate(identity, "ucip:does.not.exist")
        assert result.decision == RBACDecision.DENY


class TestRBACEngineCapabilityTokens:
    """Capability tokens augment tier-based access."""

    def test_token_grants_capability_beyond_tier(self):
        token = CapabilityToken(
            token_id="tok-1",
            caps=["ucip:secret.read"],
            issued_by="admin",
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        identity = _make_identity(TenantTier.TENANT_USER, tokens=[token])
        result = RBACEngine.evaluate_capability_tokens(identity, "ucip:secret.read")
        assert result.decision == RBACDecision.ALLOW
        assert "explicit token" in result.reason.lower()

    def test_expired_token_is_denied(self):
        token = CapabilityToken(
            token_id="tok-1",
            caps=["ucip:secret.read"],
            issued_by="admin",
            expires_at=datetime.now(timezone.utc) - timedelta(hours=1),
        )
        identity = _make_identity(TenantTier.TENANT_USER, tokens=[token])
        result = RBACEngine.evaluate_capability_tokens(identity, "ucip:secret.read")
        assert result.decision == RBACDecision.DENY
        assert "expired" in result.reason.lower()

    def test_token_without_expiry_never_expires(self):
        token = CapabilityToken(
            token_id="tok-1",
            caps=["ucip:secret.read"],
            issued_by="admin",
            expires_at=None,
        )
        identity = _make_identity(TenantTier.TENANT_USER, tokens=[token])
        result = RBACEngine.evaluate_capability_tokens(identity, "ucip:secret.read")
        assert result.decision == RBACDecision.ALLOW

    def test_multiple_caps_in_single_token(self):
        token = CapabilityToken(
            token_id="tok-1",
            caps=["ucip:secret.read", "ucip:vcs.push"],
            issued_by="admin",
            expires_at=None,
        )
        identity = _make_identity(TenantTier.TENANT_USER, tokens=[token])
        assert RBACEngine.evaluate_capability_tokens(identity, "ucip:secret.read").decision == RBACDecision.ALLOW
        assert RBACEngine.evaluate_capability_tokens(identity, "ucip:vcs.push").decision == RBACDecision.ALLOW


class TestRBACEngineTierRank:
    def test_tier_ranks_are_monotonic(self):
        ranks = [RBACEngine._tier_rank(t) for t in TenantTier]
        assert ranks == sorted(ranks)

    def test_system_has_highest_rank(self):
        assert RBACEngine._tier_rank(TenantTier.SYSTEM) == 4

    def test_public_has_lowest_rank(self):
        assert RBACEngine._tier_rank(TenantTier.PUBLIC) == 0


class TestListCapabilities:
    def test_list_capabilities_for_system(self):
        caps = RBACEngine.list_capabilities_for_tier(TenantTier.SYSTEM)
        assert len(caps) >= 10
        assert "ucip:system.shell" in caps

    def test_list_capabilities_for_public(self):
        caps = RBACEngine.list_capabilities_for_tier(TenantTier.PUBLIC)
        assert len(caps) >= 3
        assert "ucip:system.shell" not in caps