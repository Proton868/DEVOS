"""
World-binding helpers for execution, jobs, streams, and resources.

Central enforcement: every internal operation that touches world-owned state
must carry a trusted WorldContext. Client-supplied IDs are never sufficient.
"""
from __future__ import annotations

from typing import Any, Mapping, Optional

from governance.world_context import WorldBoundaryError, WorldContext, require_world_context


def require_world(ctx: Any) -> WorldContext:
    """Accept WorldContext or fail closed."""
    return require_world_context(ctx)


def world_from_identity_fields(
    *,
    tenant_id: Optional[str],
    owner_id: Optional[str],
    mission_id: Optional[str] = None,
) -> WorldContext:
    """Build WorldContext from already-trusted server fields (not client input)."""
    tid = str(tenant_id or "").strip()
    oid = str(owner_id or "").strip()
    if not tid:
        raise WorldBoundaryError("WORLD_REQUIRED", "tenant_id/world_id required")
    if not oid:
        raise WorldBoundaryError("PRINCIPAL_REQUIRED", "owner_id/principal_id required")
    return WorldContext(world_id=tid, principal_id=oid, mission_id=mission_id)


def assert_resource_in_world(
    world: WorldContext,
    resource: Mapping[str, Any] | Any,
    *,
    resource_name: str = "resource",
    world_keys: tuple[str, ...] = ("world_id", "tenant_id"),
    owner_keys: tuple[str, ...] = ("owner_id", "user_id", "principal_id"),
    require_owner_match: bool = False,
) -> None:
    """
    Fail closed if resource is outside world (and optionally outside principal).
    Looks up world/tenant fields on dicts or objects.
    """
    world = require_world(world)

    def _get(obj: Any, key: str) -> Any:
        if isinstance(obj, Mapping):
            return obj.get(key)
        return getattr(obj, key, None)

    resource_world = None
    for k in world_keys:
        v = _get(resource, k)
        if v is not None and str(v).strip():
            resource_world = str(v).strip()
            break

    if resource_world is None:
        raise WorldBoundaryError(
            "WORLD_REQUIRED",
            f"{resource_name}: missing world/tenant association",
        )
    world.assert_same_world(resource_world, resource=resource_name)

    if require_owner_match:
        resource_owner = None
        for k in owner_keys:
            v = _get(resource, k)
            if v is not None and str(v).strip():
                resource_owner = str(v).strip()
                break
        if resource_owner is None or resource_owner != world.principal_id:
            raise WorldBoundaryError(
                "OWNERSHIP_DENIED",
                f"{resource_name}: principal mismatch",
            )


def bind_job_payload(world: WorldContext, payload: Optional[dict] = None) -> dict:
    """Attach authoritative world fields to a job/queue payload."""
    world = require_world(world)
    out = dict(payload or {})
    # Never allow client to override these after binding
    out["world_id"] = world.world_id
    out["tenant_id"] = world.world_id
    out["principal_id"] = world.principal_id
    out["owner_id"] = world.principal_id
    if world.mission_id:
        out.setdefault("mission_id", world.mission_id)
    return out


def extract_job_world(payload: Mapping[str, Any]) -> WorldContext:
    """Restore WorldContext from a durable job payload. Fail closed if missing."""
    if not payload:
        raise WorldBoundaryError("WORLD_REQUIRED", "job payload missing")
    tid = str(payload.get("world_id") or payload.get("tenant_id") or "").strip()
    pid = str(
        payload.get("principal_id")
        or payload.get("owner_id")
        or payload.get("user_id")
        or ""
    ).strip()
    if not tid or not pid:
        raise WorldBoundaryError("WORLD_REQUIRED", "job payload missing world/principal")
    return WorldContext(
        world_id=tid,
        principal_id=pid,
        mission_id=str(payload.get("mission_id") or "") or None,
    )


def assert_stream_subscription(
    world: WorldContext,
    resource: Mapping[str, Any] | Any,
    *,
    resource_name: str = "stream",
) -> None:
    """SSE/WebSocket subscriptions must match world (and principal)."""
    assert_resource_in_world(
        world,
        resource,
        resource_name=resource_name,
        require_owner_match=True,
    )


def invocation_context_from_world(
    world: WorldContext,
    *,
    grants: Optional[set] = None,
    surface: str = "api",
    actor_type: str = "user",
    correlation_id: Optional[str] = None,
    metadata: Optional[dict] = None,
):
    """Build substrate InvocationContext from trusted WorldContext."""
    from governance.capability_substrate import InvocationContext

    world = require_world(world)
    caps = set(grants) if grants is not None else set(world.capabilities or set())
    return InvocationContext(
        tenant_id=world.world_id,
        owner_id=world.principal_id,
        granted_capabilities=caps,
        actor_type=actor_type,
        actor_id=world.principal_id,
        surface=surface,
        correlation_id=correlation_id,
        metadata=dict(metadata or {}),
        client_supplied_grants=False,
    )
