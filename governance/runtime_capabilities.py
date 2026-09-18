"""
Register runtime lifecycle capabilities on the unified substrate.

IDE / Flow / Preview / Nuha request these; UCIP still authorizes.
"""
from __future__ import annotations

import logging

from governance.capability_substrate import (
    CapabilityContract,
    CapabilitySubstrate,
    InvocationRequest,
    get_capability_substrate,
)

logger = logging.getLogger("devos.runtime_capabilities")


async def _lifecycle(contract: CapabilityContract, req: InvocationRequest) -> dict:
    from execution.runtime_service import run_lifecycle_action

    inputs = req.inputs or {}
    project_id = str(inputs.get("project_id") or "")
    action = str(inputs.get("action") or "status")
    port = int(inputs.get("port") or 3911)
    ctx = getattr(req, "context", None)
    user_id = str(
        (getattr(ctx, "owner_id", None) if ctx is not None else None)
        or (getattr(ctx, "user_id", None) if ctx is not None else None)
        or inputs.get("user_id")
        or ""
    )
    if not project_id or not user_id:
        raise ValueError("project_id and authenticated user_id required")
    # Never accept client-forged user_id over context
    if ctx is not None and getattr(ctx, "owner_id", None):
        user_id = str(ctx.owner_id)
    snap = await run_lifecycle_action(user_id, project_id, action, port=port)
    return snap.to_dict()


async def _health(contract: CapabilityContract, req: InvocationRequest) -> dict:
    from execution.runtime_service import snapshot

    inputs = req.inputs or {}
    project_id = str(inputs.get("project_id") or "")
    ctx = getattr(req, "context", None)
    user_id = str(
        (getattr(ctx, "owner_id", None) if ctx is not None else None)
        or (getattr(ctx, "user_id", None) if ctx is not None else None)
        or inputs.get("user_id")
        or ""
    )
    if ctx is not None and getattr(ctx, "owner_id", None):
        user_id = str(ctx.owner_id)
    if not project_id or not user_id:
        raise ValueError("project_id and authenticated user_id required")
    return snapshot(user_id, project_id, probe=True).to_dict()


def ensure_runtime_capabilities_registered() -> None:
    try:
        from governance.capability_registry import (
            CapabilityCategory,
            CapabilityDescriptor,
            CapabilityRisk,
            get_registry,
        )

        reg = get_registry()
        descriptors = [
            CapabilityDescriptor(
                slug="devos.runtime.lifecycle",
                name="Project Runtime Lifecycle",
                category=CapabilityCategory.SYSTEM,
                description="install/build/start/stop/restart/rebuild/test application runtime",
                risk=CapabilityRisk.MEDIUM,
                trust_required="standard",
                timeout_s=600,
                max_retries=0,
                input_schema={
                    "type": "object",
                    "required": ["project_id", "action"],
                    "properties": {
                        "project_id": {"type": "string"},
                        "action": {"type": "string"},
                        "port": {"type": "integer"},
                    },
                },
                output_schema={"type": "object"},
            ),
            CapabilityDescriptor(
                slug="devos.runtime.health",
                name="Project Runtime Health",
                category=CapabilityCategory.SYSTEM,
                description="Runtime snapshot and health probe",
                risk=CapabilityRisk.LOW,
                trust_required="read_only",
                timeout_s=15,
                max_retries=0,
                input_schema={
                    "type": "object",
                    "required": ["project_id"],
                    "properties": {"project_id": {"type": "string"}},
                },
                output_schema={"type": "object"},
            ),
        ]
        for d in descriptors:
            if reg.get(d.slug) is None:
                reg.register(d)
    except Exception as e:
        logger.debug("runtime capability descriptor registration skipped: %s", type(e).__name__)

    try:
        sub = get_capability_substrate()
        sub.register_executor("devos.runtime.lifecycle", _lifecycle)
        sub.register_executor("devos.runtime.health", _health)
        sub.register_alias("runtime.lifecycle", "devos.runtime.lifecycle")
        sub.register_alias("runtime.health", "devos.runtime.health")
    except Exception as e:
        logger.debug("runtime executor registration skipped: %s", type(e).__name__)


try:
    ensure_runtime_capabilities_registered()
except Exception:
    pass
