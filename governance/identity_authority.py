from __future__ import annotations
from typing import Optional, Iterable
from governance.identity_context import IdentityContext, ActorKind, TenantRole, resolve_identity, resolve_worker_identity
from governance.ucip import AgentIdentity, TrustLevel, ALWAYS_BLOCKED_CAPS
from governance.capability_registry import authorize_capability_slug

def identity_from_user(user_id, session_id="api", *, tenant_id=None, is_admin=False, trust=TrustLevel.OPERATOR,
    actor_kind=ActorKind.HUMAN, tenant_role=TenantRole.MEMBER, caps=None):
    return resolve_identity(user_id, session_id, tenant_id=tenant_id or f"user:{user_id}", trust_level=trust,
        actor_kind=actor_kind, tenant_role=tenant_role, is_platform_admin=bool(is_admin and actor_kind==ActorKind.HUMAN), caps=caps)

def identity_from_worker(worker_id, session_id="worker", *, tenant_id, trust=TrustLevel.AUTONOMOUS, caps=None):
    return resolve_worker_identity(worker_id, session_id, tenant_id=tenant_id, trust_level=trust, caps=caps)

def effective_caps(ctx): return set(ctx.effective_capabilities()) - set(ALWAYS_BLOCKED_CAPS)
def authorize_capability(ctx, capability): return authorize_capability_slug(capability, effective_caps(ctx))
def agent_identity_from_context(ctx): return ctx.to_agent_identity()
