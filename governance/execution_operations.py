"""
Durable consequential operation ledger (Stage 3M).

RESERVED → RUNNING → SUCCEEDED | FAILED | UNKNOWN | CANCELLED

UNKNOWN means completion cannot be proven. UNKNOWN is never automatically retried.
"""
from __future__ import annotations

import hashlib
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger("devos.execution_operations")

OP_RESERVED = "reserved"
OP_RUNNING = "running"
OP_SUCCEEDED = "succeeded"
OP_FAILED = "failed"
OP_UNKNOWN = "unknown"
OP_CANCELLED = "cancelled"

TERMINAL = frozenset({OP_SUCCEEDED, OP_FAILED, OP_UNKNOWN, OP_CANCELLED})


def _now():
    return datetime.now(timezone.utc)


def digest_payload(obj: Any) -> str:
    try:
        raw = json.dumps(obj, sort_keys=True, default=str)[:8000]
    except Exception:
        raw = str(obj)[:8000]
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


async def reserve_operation(
    *,
    owner_id: str,
    tenant_id: Optional[str] = None,
    actor_id: Optional[str] = None,
    task_id: Optional[str] = None,
    execution_job_id: Optional[str] = None,
    operation_type: str = "tool",
    tool_name: Optional[str] = None,
    correlation_id: Optional[str] = None,
    request_id: Optional[str] = None,
    idempotency_key: Optional[str] = None,
    input_digest: Optional[str] = None,
    target_digest: Optional[str] = None,
    args: Optional[dict] = None,
) -> Optional[str]:
    """Create durable RESERVED operation. Returns operation_id or None on failure."""
    try:
        from core.database import AsyncSessionLocal, ExecutionOperation
        op_id = str(uuid.uuid4())
        if args is not None and not input_digest:
            input_digest = digest_payload(args)
        async with AsyncSessionLocal() as db:
            row = ExecutionOperation(
                id=op_id,
                tenant_id=tenant_id,
                owner_id=owner_id,
                actor_id=actor_id or owner_id,
                task_id=task_id,
                execution_job_id=execution_job_id,
                operation_type=operation_type,
                tool_name=tool_name,
                status=OP_RESERVED,
                idempotency_key=idempotency_key,
                request_id=request_id,
                correlation_id=correlation_id,
                input_digest=input_digest,
                target_digest=target_digest,
                attempt=1,
                created_at=_now(),
            )
            db.add(row)
            await db.commit()
            return op_id
    except Exception:
        logger.exception("reserve_operation failed")
        return None


async def mark_running(operation_id: str) -> bool:
    try:
        from core.database import AsyncSessionLocal, ExecutionOperation
        async with AsyncSessionLocal() as db:
            row = await db.get(ExecutionOperation, operation_id)
            if row is None or row.status not in (OP_RESERVED, OP_RUNNING):
                return False
            row.status = OP_RUNNING
            row.started_at = _now()
            await db.commit()
            return True
    except Exception:
        logger.exception("mark_running failed")
        return False


async def complete_operation(
    operation_id: str,
    *,
    success: bool,
    evidence_id: Optional[str] = None,
    result_digest: Optional[str] = None,
    error: Optional[str] = None,
) -> bool:
    try:
        from core.database import AsyncSessionLocal, ExecutionOperation
        async with AsyncSessionLocal() as db:
            row = await db.get(ExecutionOperation, operation_id)
            if row is None:
                return False
            if row.status in (OP_UNKNOWN, OP_SUCCEEDED) and not success:
                # Do not downgrade UNKNOWN/SUCCEEDED to failed blindly
                return False
            row.status = OP_SUCCEEDED if success else OP_FAILED
            row.evidence_id = evidence_id or row.evidence_id
            row.result_digest = result_digest
            row.error = (error or "")[:2000] if error else None
            row.completed_at = _now()
            await db.commit()
            return True
    except Exception:
        logger.exception("complete_operation failed")
        return False


async def mark_unknown(operation_id: str, reason: str = "ambiguous_outcome") -> bool:
    try:
        from core.database import AsyncSessionLocal, ExecutionOperation
        async with AsyncSessionLocal() as db:
            row = await db.get(ExecutionOperation, operation_id)
            if row is None:
                return False
            if row.status == OP_SUCCEEDED:
                return False  # never downgrade success
            row.status = OP_UNKNOWN
            row.error = reason[:2000]
            row.completed_at = _now()
            await db.commit()
            return True
    except Exception:
        logger.exception("mark_unknown failed")
        return False


async def load_operation(operation_id: str) -> Optional[dict]:
    try:
        from core.database import AsyncSessionLocal, ExecutionOperation
        async with AsyncSessionLocal() as db:
            row = await db.get(ExecutionOperation, operation_id)
            if row is None:
                return None
            return {
                "id": row.id,
                "tenant_id": row.tenant_id,
                "owner_id": row.owner_id,
                "actor_id": row.actor_id,
                "task_id": row.task_id,
                "execution_job_id": row.execution_job_id,
                "operation_type": row.operation_type,
                "tool_name": row.tool_name,
                "status": row.status,
                "idempotency_key": row.idempotency_key,
                "correlation_id": row.correlation_id,
                "input_digest": row.input_digest,
                "target_digest": row.target_digest,
                "result_digest": row.result_digest,
                "evidence_id": row.evidence_id,
                "attempt": row.attempt,
                "error": row.error,
            }
    except Exception:
        logger.debug("load_operation failed", exc_info=True)
        return None


async def find_matching_evidence(operation_id: str) -> Optional[dict]:
    """Find EvidenceRecord body referencing this operation_id."""
    try:
        from core.database import AsyncSessionLocal, EvidenceRecord
        from sqlalchemy import select
        async with AsyncSessionLocal() as db:
            # Prefer scanning recent evidence; body JSON may contain operation_id
            q = await db.execute(select(EvidenceRecord).order_by(EvidenceRecord.created_at.desc()).limit(200))
            for row in q.scalars().all():
                body = row.body if isinstance(row.body, dict) else {}
                if body.get("operation_id") == operation_id:
                    return {
                        "id": row.id,
                        "body": body,
                        "owner_id": getattr(row, "owner_id", None),
                        "tenant_id": getattr(row, "tenant_id", None),
                    }
    except Exception:
        logger.debug("find_matching_evidence failed", exc_info=True)
    return None


async def reconcile_operation(
    operation_id: str,
    *,
    expected_task_id: Optional[str] = None,
    expected_owner_id: Optional[str] = None,
    expected_tenant_id: Optional[str] = None,
    expected_correlation_id: Optional[str] = None,
) -> dict:
    """
    Deterministic operation reconciliation. Never executes tools.
    Returns structured result with execute=False always.
    """
    op = await load_operation(operation_id)
    if op is None:
        return {
            "ok": False,
            "reason_code": "operation_missing",
            "execute": False,
            "retry": False,
            "status": None,
        }

    # Identity binding
    if expected_task_id and op.get("task_id") and str(op["task_id"]) != str(expected_task_id):
        return {"ok": False, "reason_code": "task_mismatch", "execute": False, "retry": False, "status": op["status"]}
    if expected_owner_id and str(op.get("owner_id")) != str(expected_owner_id):
        return {"ok": False, "reason_code": "owner_mismatch", "execute": False, "retry": False, "status": op["status"]}
    if expected_tenant_id and op.get("tenant_id") and str(op["tenant_id"]) != str(expected_tenant_id):
        return {"ok": False, "reason_code": "tenant_mismatch", "execute": False, "retry": False, "status": op["status"]}
    if expected_correlation_id and op.get("correlation_id") and str(op["correlation_id"]) != str(expected_correlation_id):
        return {"ok": False, "reason_code": "correlation_mismatch", "execute": False, "retry": False, "status": op["status"]}

    status = op["status"]
    if status == OP_SUCCEEDED:
        return {"ok": True, "reason_code": "already_succeeded", "execute": False, "retry": False, "status": OP_SUCCEEDED, "operation": op}
    if status == OP_UNKNOWN:
        return {"ok": True, "reason_code": "unknown", "execute": False, "retry": False, "status": OP_UNKNOWN, "operation": op}
    if status == OP_CANCELLED:
        return {"ok": True, "reason_code": "cancelled", "execute": False, "retry": False, "status": OP_CANCELLED, "operation": op}
    if status == OP_FAILED:
        return {"ok": True, "reason_code": "failed", "execute": False, "retry": False, "status": OP_FAILED, "operation": op}

    if status == OP_RESERVED:
        # Never started side effect — cancel, do not auto-execute
        try:
            from core.database import AsyncSessionLocal, ExecutionOperation
            async with AsyncSessionLocal() as db:
                row = await db.get(ExecutionOperation, operation_id)
                if row and row.status == OP_RESERVED:
                    row.status = OP_CANCELLED
                    row.error = "reserved_never_started"
                    row.completed_at = _now()
                    await db.commit()
        except Exception:
            pass
        return {"ok": True, "reason_code": "reserved_cancelled", "execute": False, "retry": False, "status": OP_CANCELLED, "operation": op}

    if status == OP_RUNNING:
        evidence = await find_matching_evidence(operation_id)
        if evidence and evidence.get("body", {}).get("outcome") == "succeeded":
            await complete_operation(operation_id, success=True, evidence_id=evidence.get("id"))
            op = await load_operation(operation_id)
            return {"ok": True, "reason_code": "reconciled_succeeded", "execute": False, "retry": False, "status": OP_SUCCEEDED, "operation": op}
        # Ambiguous — UNKNOWN
        await mark_unknown(operation_id, "running_no_matching_evidence")
        op = await load_operation(operation_id)
        return {"ok": True, "reason_code": "marked_unknown", "execute": False, "retry": False, "status": OP_UNKNOWN, "operation": op}

    return {"ok": False, "reason_code": "unhandled_status", "execute": False, "retry": False, "status": status, "operation": op}


def is_consequential_side_effect(side_effect: str) -> bool:
    """True for tools that mutate state and must not be blindly replayed."""
    se = (side_effect or "").lower()
    return se in ("local", "external", "unknown", "network", "mutative")
