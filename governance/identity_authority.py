"""IdentityContext as capability authority (UCI control plane)."""
from __future__ import annotations
from typing import Optional
from governance.identity_context import IdentityContext, TenantTier, resolve_identity
from governance.ucip import AgentIdentity, TrustLevel, ALWAYS_BLOCKED_CAPS
from governance.capability_registry import authorize_capability_slug

def identity_from_user(user_id: str, session_id: str="api", *, tenant_id: Optional[str]=None,
                       is_admin: bool=False, trust: TrustLevel=TrustLevel.OPERATOR) -> IdentityContext:
    return resolve_identity(user_id, session_id, tenant_id=tenant_id or f"user:{user_id}", trust_level=trust)

def effective_caps(ctx: IdentityContext) -> set:
    caps = set(ctx.effective_capabilities()) if hasattr(ctx, "effective_capabilities") else set()
    return caps - set(ALWAYS_BLOCKED_CAPS)

def authorize_capability(ctx: IdentityContext, capability: str) -> tuple:
    return authorize_capability_slug(capability, effective_caps(ctx))

def agent_identity_from_context(ctx: IdentityContext) -> AgentIdentity:
    if getattr(ctx, "agent_identity", None) is not None:
        return ctx.agent_identity
    return AgentIdentity.create(str(ctx.actor_id), ctx.session_id or "session",
                                trust_level=TrustLevel.OPERATOR, extra_caps=effective_caps(ctx))
