"""
World-scoped governance context.

Canonical mapping:
  world_id  ≡  tenant_id  (existing DevOS tenancy)

Invariant:
  Every operation executes within exactly one world.
  Shared infrastructure never implies shared authority.
  Client-supplied world IDs are never authoritative.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from governance.identity_context import IdentityContext, TenantRole


class WorldBoundaryError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


@dataclass(frozen=True)
class WorldContext:
    """Mandatory world-scoped context for every internal operation."""

    world_id: str
    principal_id: str
    mission_id: Optional[str] = None
    authorized_scope: tuple[str, ...] = ()
    capabilities: frozenset[str] = field(default_factory=frozenset)
    economic_policy: Optional[str] = None
    evidence_chain_root: Optional[str] = None
    identity: Optional[IdentityContext] = None
    tenant_role: Optional[TenantRole] = None

    def __post_init__(self):
        if not self.world_id or not str(self.world_id).strip():
            raise WorldBoundaryError("WORLD_REQUIRED", "world_id is required")
        if not self.principal_id or not str(self.principal_id).strip():
            raise WorldBoundaryError("PRINCIPAL_REQUIRED", "principal_id is required")
        wid = str(self.world_id).strip()
        if "\x00" in wid or ".." in wid or "/" in wid or "\\" in wid:
            raise WorldBoundaryError("INVALID_WORLD", f"invalid world_id: {wid!r}")

    @property
    def tenant_id(self) -> str:
        """Backward-compatible alias — world_id is the canonical tenant."""
        return self.world_id

    def assert_same_world(self, other_world_id: Any, *, resource: str = "resource") -> None:
        oid = str(other_world_id or "").strip()
        if not oid or oid != self.world_id:
            raise WorldBoundaryError(
                "CROSS_WORLD_DENIED",
                f"{resource}: world {oid!r} is outside {self.world_id!r}",
            )

    def to_dict(self) -> dict:
        return {
            "world_id": self.world_id,
            "tenant_id": self.world_id,
            "principal_id": self.principal_id,
            "mission_id": self.mission_id,
            "authorized_scope": list(self.authorized_scope),
            "capabilities": sorted(self.capabilities),
            "economic_policy": self.economic_policy,
            "evidence_chain_root": self.evidence_chain_root,
            "tenant_role": self.tenant_role.value if self.tenant_role else None,
        }


def resolve_world_from_identity(identity: IdentityContext) -> WorldContext:
    """Build WorldContext from trusted IdentityContext only."""
    if identity is None:
        raise WorldBoundaryError("IDENTITY_REQUIRED", "identity required")
    tid = str(getattr(identity, "tenant_id", None) or "").strip()
    if not tid:
        raise WorldBoundaryError("WORLD_REQUIRED", "identity has no tenant_id/world")
    pid = str(getattr(identity, "actor_id", None) or getattr(identity, "user_id", None) or "").strip()
    if not pid:
        raise WorldBoundaryError("PRINCIPAL_REQUIRED", "identity has no principal")
    caps = frozenset(getattr(identity, "capabilities", None) or getattr(identity, "caps", None) or [])
    role = getattr(identity, "tenant_role", None)
    return WorldContext(
        world_id=tid,
        principal_id=pid,
        capabilities=caps,
        identity=identity,
        tenant_role=role if isinstance(role, TenantRole) else None,
    )


def resolve_world_from_request(
    *,
    authenticated_user_id: str,
    membership_tenant_ids: set[str],
    preferred_tenant_id: Optional[str] = None,
    client_supplied_world_id: Optional[str] = None,
) -> WorldContext:
    """
    Resolve world at the authoritative boundary.

    Client-supplied world IDs are only accepted when they match membership.
    Never invent a global/default world for unscoped users with no membership.
    """
    uid = str(authenticated_user_id or "").strip()
    if not uid:
        raise WorldBoundaryError("PRINCIPAL_REQUIRED", "authenticated user required")

    # Reject client forgery: if client sends world_id not in membership → deny
    if client_supplied_world_id is not None and str(client_supplied_world_id).strip():
        cid = str(client_supplied_world_id).strip()
        if cid not in membership_tenant_ids:
            raise WorldBoundaryError(
                "CROSS_WORLD_DENIED",
                "client-supplied world_id not in principal membership",
            )
        return WorldContext(world_id=cid, principal_id=uid)

    if preferred_tenant_id and preferred_tenant_id in membership_tenant_ids:
        return WorldContext(world_id=preferred_tenant_id, principal_id=uid)

    if len(membership_tenant_ids) == 1:
        return WorldContext(world_id=next(iter(membership_tenant_ids)), principal_id=uid)

    if not membership_tenant_ids:
        # Fail closed — no silent global world
        raise WorldBoundaryError("WORLD_REQUIRED", "principal has no world membership")

    raise WorldBoundaryError(
        "WORLD_AMBIGUOUS",
        "multiple worlds; preferred_tenant_id required",
    )


def require_world_context(ctx: Any) -> WorldContext:
    if isinstance(ctx, WorldContext):
        return ctx
    raise WorldBoundaryError("WORLD_REQUIRED", "valid WorldContext required")
