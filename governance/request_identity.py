"""Tenant-scoped IdentityContext + WorldContext for authenticated API requests."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from fastapi import HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import User
from governance.identity_context import ActorKind, IdentityContext, TenantRole
from governance.identity_authority import identity_from_user
from governance.tenant_store import ensure_personal_tenant, is_tenant_member, user_tenant_ids
from governance.ucip import TrustLevel
from governance.world_context import (
    WorldBoundaryError,
    WorldContext,
    resolve_world_from_identity,
    resolve_world_from_request,
)


@dataclass
class TenantContext:
    """Authenticated request context. WorldContext is the canonical world identity."""

    user: User
    tenant_id: str
    identity: IdentityContext
    world: WorldContext

    @property
    def world_id(self) -> str:
        return self.world.world_id

    @property
    def principal_id(self) -> str:
        return self.world.principal_id


async def get_tenant_context(
    request: Request,
    db: AsyncSession,
    user: User,
    *,
    trust: TrustLevel = TrustLevel.OPERATOR,
    tenant_id: str | None = None,
) -> TenantContext:
    """
    Authenticate → membership → authoritative WorldContext.

    Client may request a world via X-Tenant-Id / X-World-Id / tenant_id arg,
    but membership decides validity. No membership → DENY.
    Never falls back to a global/default/admin world.
    """
    personal = await ensure_personal_tenant(db, user)
    membership = await user_tenant_ids(db, user.id)
    if personal and personal.id:
        membership = set(membership) | {personal.id}

    # Client-supplied candidates (never authoritative alone)
    hdr_world = None
    if request is not None:
        hdr_world = (
            request.headers.get("X-World-Id")
            or request.headers.get("X-Tenant-Id")
        )
    client_world = tenant_id or hdr_world

    try:
        world = resolve_world_from_request(
            authenticated_user_id=str(user.id),
            membership_tenant_ids=set(membership or set()),
            preferred_tenant_id=personal.id if personal else None,
            client_supplied_world_id=client_world,
        )
    except WorldBoundaryError as e:
        # Ambiguous multi-tenant without preferred: fall back to personal when available
        if e.code == "WORLD_AMBIGUOUS" and personal and personal.id in membership:
            world = WorldContext(world_id=personal.id, principal_id=str(user.id))
        else:
            raise HTTPException(
                403 if e.code in ("CROSS_WORLD_DENIED", "WORLD_REQUIRED", "WORLD_AMBIGUOUS") else 400,
                e.message,
            )

    # Extra membership check (defense in depth)
    if world.world_id != (personal.id if personal else None):
        if not await is_tenant_member(db, user.id, world.world_id):
            raise HTTPException(403, "not a member of tenant")

    identity = identity_from_user(
        user.id,
        session_id=getattr(getattr(request, "state", None), "session_id", None) or "api",
        tenant_id=world.world_id,
        is_admin=bool(getattr(user, "is_admin", False)),
        trust=trust,
        actor_kind=ActorKind.HUMAN,
        tenant_role=TenantRole.ADMIN if getattr(user, "is_admin", False) else TenantRole.MEMBER,
    )
    # Re-bind world with identity (capabilities from identity when present)
    try:
        world = resolve_world_from_identity(identity)
    except WorldBoundaryError:
        # Keep membership-resolved world if identity lacks tenant
        world = WorldContext(
            world_id=world.world_id,
            principal_id=str(user.id),
            identity=identity,
        )

    # Stash on request.state for downstream layers
    if request is not None and hasattr(request, "state"):
        try:
            request.state.world = world
            request.state.tenant_id = world.world_id
        except Exception:
            pass

    return TenantContext(user=user, tenant_id=world.world_id, identity=identity, world=world)


async def require_world_context(
    request: Request,
    db: AsyncSession,
    user: User,
    *,
    trust: TrustLevel = TrustLevel.OPERATOR,
) -> WorldContext:
    """FastAPI-friendly dependency that returns only WorldContext."""
    ctx = await get_tenant_context(request, db, user, trust=trust)
    return ctx.world
