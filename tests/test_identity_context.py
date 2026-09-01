
from governance.identity_context import IdentityContext, TenantTier, CapabilityToken, resolve_identity, ActorKind, AutonomyProfile, resolve_worker_identity
from governance.ucip import AgentIdentity, TrustLevel

def test_create():
    ctx = IdentityContext.create("u", "t", trust_level=TrustLevel.OPERATOR)
    assert ctx.trust_tier == TenantTier.TENANT_USER

def test_autonomous_not_agency_op():
    ctx = resolve_identity("u", "s", trust_level=TrustLevel.AUTONOMOUS)
    assert ctx.trust_tier == TenantTier.TENANT_USER
    assert ctx.autonomy == AutonomyProfile.AUTONOMOUS

def test_worker():
    ctx = resolve_worker_identity("w", "s", "t")
    assert ctx.actor_kind == ActorKind.WORKER
    assert not ctx.is_platform_operator()

def test_admin():
    assert resolve_identity("a", "s", is_platform_admin=True).trust_tier == TenantTier.AGENCY_OP

def test_token():
    t = CapabilityToken.issue(["ucip:memory.read"])
    assert t.verify(); t.caps.append("x"); assert not t.verify()
