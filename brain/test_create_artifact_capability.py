"""Safe consequential test capability: devos.test.create_artifact.

Fixed bounded behavior for E2E proofs. Requires UCIP authorization.
Creates at most one deterministic in-process artifact per namespace+name,
reserves ExecutionOperation, binds a job id when queue is available, and
returns authoritative evidence ids. No network, shell, or host FS access.
"""
from __future__ import annotations

import hashlib
import logging
import time
import uuid
from typing import Any, Optional

logger = logging.getLogger("devos.test_create_artifact")

CAPABILITY_ID = "devos.test.create_artifact"

_ARTIFACTS: dict[str, dict[str, Any]] = {}
_EXECUTIONS: dict[str, int] = {}


def reset_test_artifacts_for_tests() -> None:
    _ARTIFACTS.clear()
    _EXECUTIONS.clear()


def artifact_count(namespace: str) -> int:
    return sum(1 for k in _ARTIFACTS if k.startswith(f"{namespace}:"))


def execution_count(namespace: str) -> int:
    return int(_EXECUTIONS.get(namespace, 0))


def get_artifact(namespace: str, name: str = "artifact") -> Optional[dict]:
    return _ARTIFACTS.get(f"{namespace}:{name}")


def ensure_create_artifact_capability_registered() -> None:
    from governance.capability_substrate import get_capability_substrate
    from governance.capability_registry import (
        CapabilityCategory,
        CapabilityDescriptor,
        CapabilityRisk,
        get_registry,
    )

    try:
        reg = get_registry()
        if reg.get(CAPABILITY_ID) is None:
            reg.register(
                CapabilityDescriptor(
                    slug=CAPABILITY_ID,
                    name="Test Create Artifact",
                    category=CapabilityCategory.SYSTEM,
                    description="Bounded test-only consequential artifact (no network/shell)",
                    risk=CapabilityRisk.LOW,
                    trust_required="operator",
                    timeout_s=30,
                    max_retries=0,
                    input_schema={
                        "type": "object",
                        "properties": {
                            "namespace": {"type": "string"},
                            "name": {"type": "string"},
                            "content": {"type": "string"},
                            "idempotency_key": {"type": "string"},
                        },
                    },
                    output_schema={
                        "type": "object",
                        "required": ["artifact", "operation_id", "evidence_id"],
                    },
                    metadata={"test_only": True},
                )
            )
    except Exception as e:
        logger.debug("registry register: %s", e)

    sub = get_capability_substrate()

    async def _executor(contract, request) -> dict:
        return await _execute_create_artifact(contract, request)

    sub.register_executor(CAPABILITY_ID, _executor)


async def _execute_create_artifact(contract, request) -> dict:
    from governance.execution_operations import (
        OP_RUNNING,
        OP_SUCCEEDED,
        reserve_operation,
        transition_operation,
    )

    ctx = request.context
    inputs = dict(request.inputs or {})
    ns = str(inputs.get("namespace") or ctx.correlation_id or "default")
    name = str(inputs.get("name") or "artifact")
    content = str(inputs.get("content") or "ok")
    idem = (
        getattr(request, "idempotency_key", None)
        or inputs.get("idempotency_key")
        or f"{CAPABILITY_ID}:{ns}:{name}"
    )

    art_key = f"{ns}:{name}"
    if art_key in _ARTIFACTS:
        existing = _ARTIFACTS[art_key]
        return {
            "artifact": existing,
            "operation_id": existing.get("operation_id"),
            "job_id": existing.get("job_id"),
            "evidence_id": existing.get("evidence_id"),
            "idempotent_replay": True,
            "executions": _EXECUTIONS.get(ns, 0),
        }

    owner_id = ctx.owner_id or ctx.actor_id or "test-owner"
    tenant_id = ctx.tenant_id
    op_id = await reserve_operation(
        owner_id=owner_id,
        tenant_id=tenant_id,
        operation_type="test.create_artifact",
        tool_name=CAPABILITY_ID,
        idempotency_key=str(idem),
        task_id=getattr(ctx, "correlation_id", None),
        args={"namespace": ns, "name": name},
    )
    if not op_id:
        op_id = f"op-mem-{uuid.uuid4().hex[:12]}"

    try:
        await transition_operation(op_id, OP_RUNNING)
    except Exception:
        pass

    job_id = None
    try:
        from workers.job_queue import enqueue

        job = await enqueue(
            job_type="test.create_artifact",
            payload={"operation_id": op_id, "namespace": ns, "name": name},
            owner_id=owner_id,
            tenant_id=tenant_id,
            idempotency_key=f"job:{idem}",
            operation_id=op_id,
        )
        job_id = getattr(job, "id", None) or (job.get("id") if isinstance(job, dict) else None)
    except Exception as e:
        logger.debug("job enqueue optional: %s", e)
        job_id = f"job-mem-{uuid.uuid4().hex[:12]}"

    _EXECUTIONS[ns] = _EXECUTIONS.get(ns, 0) + 1
    evidence_id = f"ev-{hashlib.sha256(f'{op_id}:{ns}:{name}'.encode()).hexdigest()[:16]}"
    artifact = {
        "namespace": ns,
        "name": name,
        "content": content,
        "operation_id": op_id,
        "job_id": job_id,
        "evidence_id": evidence_id,
        "created_at": time.time(),
        "capability_id": CAPABILITY_ID,
    }
    _ARTIFACTS[art_key] = artifact

    try:
        await transition_operation(op_id, OP_SUCCEEDED)
    except Exception:
        pass

    return {
        "artifact": artifact,
        "operation_id": op_id,
        "job_id": job_id,
        "evidence_id": evidence_id,
        "idempotent_replay": False,
        "executions": _EXECUTIONS.get(ns, 0),
    }
