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


# ── Application lifecycle capabilities ───────────────────────────────────────

async def _app_lifecycle(contract, req):
    from execution.app_lifecycle import advance, run_pipeline, get_lifecycle, create_lifecycle

    inputs = req.inputs or {}
    project_id = str(inputs.get("project_id") or "")
    ctx = getattr(req, "context", None)
    user_id = str(
        (getattr(ctx, "owner_id", None) if ctx is not None else None)
        or inputs.get("user_id")
        or ""
    )
    if ctx is not None and getattr(ctx, "owner_id", None):
        user_id = str(ctx.owner_id)
    if not project_id or not user_id:
        raise ValueError("project_id and authenticated user_id required")

    mode = str(inputs.get("mode") or "advance").lower()
    if mode == "status":
        rec = get_lifecycle(user_id, project_id) or create_lifecycle(user_id, project_id)
        return rec.to_dict()
    if mode == "pipeline":
        stages = inputs.get("stages")
        rec = await run_pipeline(
            user_id,
            project_id,
            stages=stages,
            params={k: v for k, v in inputs.items() if k not in ("project_id", "mode", "stages", "user_id")},
            stop_on_failure=bool(inputs.get("stop_on_failure", True)),
        )
        return rec.to_dict()
    stage = str(inputs.get("stage") or "CREATE")
    rec = await advance(
        user_id,
        project_id,
        stage,
        params={k: v for k, v in inputs.items() if k not in ("project_id", "mode", "stage", "user_id")},
    )
    return rec.to_dict()


def _register_lifecycle_capability() -> None:
    try:
        from governance.capability_registry import (
            CapabilityCategory,
            CapabilityDescriptor,
            CapabilityRisk,
            get_registry,
        )

        reg = get_registry()
        d = CapabilityDescriptor(
            slug="devos.app.lifecycle",
            name="Application Lifecycle",
            category=CapabilityCategory.SYSTEM,
            description="CREATE→DEVELOP→TEST→BUILD→PREVIEW→VERIFY→DEPLOY→OBSERVE→MAINTAIN",
            risk=CapabilityRisk.HIGH,
            trust_required="standard",
            timeout_s=900,
            max_retries=0,
            input_schema={
                "type": "object",
                "required": ["project_id"],
                "properties": {
                    "project_id": {"type": "string"},
                    "mode": {"type": "string"},
                    "stage": {"type": "string"},
                    "stages": {"type": "array"},
                    "provider": {"type": "string"},
                },
            },
            output_schema={"type": "object"},
        )
        if reg.get(d.slug) is None:
            reg.register(d)
    except Exception as e:
        logger.debug("lifecycle descriptor skipped: %s", type(e).__name__)
    try:
        sub = get_capability_substrate()
        sub.register_executor("devos.app.lifecycle", _app_lifecycle)
        sub.register_alias("app.lifecycle", "devos.app.lifecycle")
    except Exception as e:
        logger.debug("lifecycle executor skipped: %s", type(e).__name__)


try:
    _register_lifecycle_capability()
except Exception:
    pass
