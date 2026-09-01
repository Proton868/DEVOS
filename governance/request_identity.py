"""Tenant-scoped IdentityContext for authenticated API requests."""
from __future__ import annotations
from dataclasses import dataclass
from fastapi import Request, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from core.database import User
from governance.identity_context import IdentityContext, ActorKind, TenantRole
from governance.identity_authority import identity_from_user
from governance.tenant_store import ensure_personal_tenant, is_tenant_member
from governance.ucip import TrustLevel

@dataclass
class TenantContext:
    user: User
    tenant_id: str
    identity: IdentityContext

async def get_tenant_context(request: Request, db: AsyncSession, user: User, *,
    trust: TrustLevel = TrustLevel.OPERATOR, tenant_id: str | None = None) -> TenantContext:
    tenant = await ensure_personal_tenant(db, user)
    tid = tenant_id or (request.headers.get("X-Tenant-Id") if request else None) or tenant.id
    if tid != tenant.id and not await is_tenant_member(db, user.id, tid):
        raise HTTPException(403, "not a member of tenant")
    identity = identity_from_user(
        user.id, session_id=getattr(getattr(request, "state", None), "session_id", None) or "api",
        tenant_id=tid, is_admin=bool(getattr(user, "is_admin", False)), trust=trust,
        actor_kind=ActorKind.HUMAN,
        tenant_role=TenantRole.ADMIN if getattr(user, "is_admin", False) else TenantRole.MEMBER,
    )
    return TenantContext(user=user, tenant_id=tid, identity=identity)
