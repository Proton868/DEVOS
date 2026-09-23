"""
World execution boundary enforcement (PASS 4).

Every execution path must:
  1. Obtain trusted WorldContext at the trust boundary
  2. Carry it through Nuha / agent / terminal / runtime / MCP / queue
  3. Validate before side effects
  4. Fail closed on missing / mismatched / forged world

Does not replace UCIP or economics — sits alongside them.
"""
from __future__ import annotations

from typing import Any, Mapping, Optional

from governance.world_context import WorldBoundaryError, WorldContext, require_world_context
from governance.world_binding import (
    assert_resource_in_world,
    assert_stream_subscription,
    bind_job_payload,
    extract_job_world,
    world_from_identity_fields,
)


def require_execution_world(
    world: Any,
    *,
    resource_world_id: Optional[str] = None,
    resource_name: str = "execution",
) -> WorldContext:
    """Validate WorldContext before any side-effecting execution."""
    ctx = require_world_context(world)
    if resource_world_id is not None:
        ctx.assert_same_world(resource_world_id, resource=resource_name)
    return ctx


def nuha_world_from_fields(
    *,
    user_id: str,
    world_id: Optional[str] = None,
    tenant_id: Optional[str] = None,
    mission_id: Optional[str] = None,
) -> WorldContext:
    """
    Build WorldContext for Nuha/delegation from trusted server fields.

    Prefer explicit world_id; fall back to tenant_id. Never invent a world.
    user_id alone is not a world.
    """
    wid = str(world_id or tenant_id or "").strip()
    uid = str(user_id or "").strip()
    if not uid:
        raise WorldBoundaryError("PRINCIPAL_REQUIRED", "Nuha requires principal_id")
    if not wid:
        raise WorldBoundaryError("WORLD_REQUIRED", "Nuha requires world_id/tenant_id")
    return WorldContext(world_id=wid, principal_id=uid, mission_id=mission_id)


def assert_delegation_world(
    world: WorldContext,
    *,
    task_tenant_id: Optional[str] = None,
    task_owner_id: Optional[str] = None,
    forged_tenant_id: Optional[str] = None,
) -> None:
    """Hard DENY on world mismatch / forgery during Nuha→agent delegation."""
    world = require_world_context(world)
    if forged_tenant_id is not None and str(forged_tenant_id).strip() != world.world_id:
        raise WorldBoundaryError("CROSS_WORLD_DENIED", "forged tenant_id rejected")
    if task_tenant_id is not None and str(task_tenant_id).strip():
        world.assert_same_world(task_tenant_id, resource="delegated_task")
    if task_owner_id is not None and str(task_owner_id).strip() != world.principal_id:
        raise WorldBoundaryError("OWNERSHIP_DENIED", "delegated task principal mismatch")


def assert_terminal_world(
    world: WorldContext,
    *,
    user_id: str,
    project_id: str,
    project_owner_id: Optional[str] = None,
) -> WorldContext:
    """Terminal must run only inside principal's world + project."""
    world = require_world_context(world)
    uid = str(user_id or "").strip()
    if uid != world.principal_id:
        raise WorldBoundaryError("OWNERSHIP_DENIED", "terminal principal mismatch")
    if project_owner_id is not None and str(project_owner_id).strip() not in ("", world.principal_id):
        raise WorldBoundaryError("CROSS_WORLD_DENIED", "terminal project outside world")
    pid = str(project_id or "").strip()
    if not pid or ".." in pid or pid.startswith("/") or "\\" in pid:
        raise WorldBoundaryError("INVALID_PROJECT", "invalid project_id")
    # Path traversal in project_id
    if any(p == ".." for p in pid.replace("\\", "/").split("/")):
        raise WorldBoundaryError("INVALID_PROJECT", "path traversal in project_id")
    return world


def assert_runtime_world(
    world: WorldContext,
    runtime: Mapping[str, Any] | Any,
    *,
    resource_name: str = "runtime",
) -> None:
    assert_resource_in_world(
        world,
        runtime,
        resource_name=resource_name,
        require_owner_match=True,
    )


def assert_mcp_world(
    world: WorldContext,
    *,
    target_world_id: Optional[str] = None,
    resource_id: Optional[str] = None,
) -> WorldContext:
    """MCP retains originating world; cannot target another world's internal resources."""
    world = require_world_context(world)
    if target_world_id is not None and str(target_world_id).strip():
        world.assert_same_world(target_world_id, resource="mcp_target")
    return world


def assert_worker_job_world(payload: Mapping[str, Any] | None, job: Any = None) -> WorldContext:
    """
    Every queue consumer must call this before execution.
    Combines payload world with durable job tenant/owner when present.
    """
    payload = payload or {}
    world = extract_job_world(payload) if payload.get("world_id") or payload.get("tenant_id") else None

    if job is not None:
        jtid = str(getattr(job, "tenant_id", None) or "").strip()
        joid = str(getattr(job, "owner_id", None) or "").strip()
        if jtid and joid:
            durable = WorldContext(world_id=jtid, principal_id=joid)
            if world is not None:
                durable.assert_same_world(world.world_id, resource="job_payload")
                if world.principal_id != durable.principal_id:
                    raise WorldBoundaryError("OWNERSHIP_DENIED", "job principal mismatch")
            return durable
        if jtid and world is not None:
            world.assert_same_world(jtid, resource="job")
            return world

    if world is None:
        raise WorldBoundaryError("WORLD_REQUIRED", "job missing world binding")
    return world


def assert_evidence_world(
    world: WorldContext,
    evidence: Mapping[str, Any] | Any,
    *,
    resource_name: str = "evidence",
) -> None:
    assert_resource_in_world(
        world,
        evidence,
        resource_name=resource_name,
        require_owner_match=True,
    )


def assert_sse_world(
    world: WorldContext,
    resource: Mapping[str, Any] | Any,
    *,
    resource_name: str = "sse",
) -> None:
    assert_stream_subscription(world, resource, resource_name=resource_name)


# Re-exports for callers
__all__ = [
    "require_execution_world",
    "nuha_world_from_fields",
    "assert_delegation_world",
    "assert_terminal_world",
    "assert_runtime_world",
    "assert_mcp_world",
    "assert_worker_job_world",
    "assert_evidence_world",
    "assert_sse_world",
    "bind_job_payload",
    "extract_job_world",
    "WorldBoundaryError",
    "WorldContext",
]


def filter_sse_event_for_subscriber(
    subscriber_world: WorldContext,
    event: Mapping[str, Any] | None,
) -> bool:
    """
    Delivery-time isolation for long-lived SSE.

    Returns True if the event may be delivered to the subscriber.
    Events without world/tenant metadata are allowed only when they carry
    no owner/user fields (global operational events). Otherwise DENY.
    """
    subscriber_world = require_world_context(subscriber_world)
    if not event or not isinstance(event, Mapping):
        return False
    eworld = str(
        event.get("world_id")
        or event.get("tenant_id")
        or (event.get("data") or {}).get("world_id")
        or (event.get("data") or {}).get("tenant_id")
        or ""
    ).strip()
    eowner = str(
        event.get("owner_id")
        or event.get("user_id")
        or (event.get("data") or {}).get("owner_id")
        or (event.get("data") or {}).get("user_id")
        or ""
    ).strip()
    if eworld:
        if eworld != subscriber_world.world_id:
            return False
    if eowner and eowner != subscriber_world.principal_id:
        return False
    # If event claims a different plan/user owner, deny when present
    return True
