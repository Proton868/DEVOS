"""
Authoritative consequential operation ledger (Stage 3M / 3M.1).

State machine (only these transitions allowed):
  RESERVED → RUNNING | CANCELLED
  RUNNING  → SUCCEEDED | FAILED | UNKNOWN | CANCELLED
Terminal states are immutable under normal execution.

operation_id  = one consequential execution identity
idempotency_key = semantic dedup key (tenant+owner+type+key)
attempt       = attempt counter on that operation

UNKNOWN → investigate only; execute=false; retry=false
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

# from_status -> allowed next
ALLOWED = {
    OP_RESERVED: frozenset({OP_RUNNING, OP_CANCELLED}),
    OP_RUNNING: frozenset({OP_SUCCEEDED, OP_FAILED, OP_UNKNOWN, OP_CANCELLED}),
}


def _now():
    return datetime.now(timezone.utc)


def digest_payload(obj: Any) -> str:
    try:
        raw = json.dumps(obj, sort_keys=True, default=str)[:8000]
    except Exception:
        raw = str(obj)[:8000]
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def is_consequential_side_effect(side_effect: str) -> bool:
    se = (side_effect or "").lower()
    return se in ("local", "external", "unknown", "network", "mutative")


async def transition_operation(operation_id: str, new_status: str, **fields) -> bool:
    """Atomic conditional transition. Returns True only if row was updated."""
    if new_status not in (
        OP_RESERVED, OP_RUNNING, OP_SUCCEEDED, OP_FAILED, OP_UNKNOWN, OP_CANCELLED
    ):
        return False
    try:
        from sqlalchemy import text
        from core.database import AsyncSessionLocal

        # Build allowed-from set
        allowed_from = [src for src, dests in ALLOWED.items() if new_status in dests]
        if not allowed_from:
            return False

        sets = ["status = :new_status"]
        params = {"op_id": operation_id, "new_status": new_status}
        if "error" in fields and fields["error"] is not None:
            sets.append("error = :error")
            params["error"] = str(fields["error"])[:2000]
        if "evidence_id" in fields and fields["evidence_id"] is not None:
            sets.append("evidence_id = :evidence_id")
            params["evidence_id"] = fields["evidence_id"]
        if "result_digest" in fields and fields["result_digest"] is not None:
            sets.append("result_digest = :result_digest")
            params["result_digest"] = fields["result_digest"]
        if new_status == OP_RUNNING:
            sets.append("started_at = :ts")
            params["ts"] = _now()
        if new_status in TERMINAL:
            sets.append("completed_at = :ts")
            params["ts"] = _now()

        placeholders = ", ".join(f":f{i}" for i in range(len(allowed_from)))
        for i, s in enumerate(allowed_from):
            params[f"f{i}"] = s

        sql = text(
            f"""
            UPDATE execution_operations
            SET {", ".join(sets)}
            WHERE id = :op_id
              AND status IN ({placeholders})
            """
        )
        async with AsyncSessionLocal() as db:
            result = await db.execute(sql, params)
            await db.commit()
            return int(getattr(result, "rowcount", 0) or 0) == 1
    except Exception:
        logger.exception("transition_operation failed")
        return False


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
    """
    Create RESERVED operation, or return existing id for same idempotency key.
    Never stores raw secrets from args — digests only.
    """
    try:
        from core.database import AsyncSessionLocal, ExecutionOperation
        from sqlalchemy import select

        # Scrub args for digest only
        safe_args = None
        if args is not None:
            safe_args = _scrub_args(args)
            if not input_digest:
                input_digest = digest_payload(safe_args)

        async with AsyncSessionLocal() as db:
            if idempotency_key:
                q = await db.execute(
                    select(ExecutionOperation).where(
                        ExecutionOperation.owner_id == owner_id,
                        ExecutionOperation.idempotency_key == idempotency_key,
                        ExecutionOperation.operation_type == operation_type,
                        ExecutionOperation.tenant_id == tenant_id if tenant_id is not None
                        else ExecutionOperation.tenant_id.is_(None),
                    )
                )
                existing = q.scalars().first()
                if existing:
                    return existing.id

            op_id = str(uuid.uuid4())
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
            try:
                db.add(row)
                await db.commit()
                return op_id
            except Exception as ie:
                # Concurrent insert race: re-select existing idempotent op
                await db.rollback()
                if idempotency_key:
                    q2 = await db.execute(
                        select(ExecutionOperation).where(
                            ExecutionOperation.owner_id == owner_id,
                            ExecutionOperation.idempotency_key == idempotency_key,
                            ExecutionOperation.operation_type == operation_type,
                            ExecutionOperation.tenant_id == tenant_id if tenant_id is not None
                            else ExecutionOperation.tenant_id.is_(None),
                        )
                    )
                    existing2 = q2.scalars().first()
                    if existing2:
                        return existing2.id
                logger.exception("reserve_operation insert failed: %s", ie)
                return None
    except Exception:
        logger.exception("reserve_operation failed")
        return None


def _scrub_args(args: dict) -> dict:
    out = {}
    banned = ("password", "token", "secret", "api_key", "authorization", "cookie", "credential", "bearer")
    for k, v in (args or {}).items():
        lk = str(k).lower()
        if any(b in lk for b in banned):
            out[k] = "[redacted]"
        elif isinstance(v, dict):
            out[k] = _scrub_args(v)
        elif isinstance(v, str) and len(v) > 500:
            out[k] = v[:500]
        else:
            out[k] = v
    return out


async def mark_running(operation_id: str) -> bool:
    return await transition_operation(operation_id, OP_RUNNING)


async def complete_operation(
    operation_id: str,
    *,
    success: bool,
    evidence_id: Optional[str] = None,
    result_digest: Optional[str] = None,
    error: Optional[str] = None,
) -> bool:
    return await transition_operation(
        operation_id,
        OP_SUCCEEDED if success else OP_FAILED,
        evidence_id=evidence_id,
        result_digest=result_digest,
        error=error,
    )


async def mark_unknown(operation_id: str, reason: str = "ambiguous_outcome") -> bool:
    return await transition_operation(operation_id, OP_UNKNOWN, error=reason)


async def cancel_operation(operation_id: str, reason: str = "cancelled") -> bool:
    return await transition_operation(operation_id, OP_CANCELLED, error=reason)


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


def validate_operation_identity(
    op: dict,
    *,
    expected_task_id: Optional[str] = None,
    expected_owner_id: Optional[str] = None,
    expected_tenant_id: Optional[str] = None,
    expected_correlation_id: Optional[str] = None,
) -> tuple[bool, str]:
    """Strict identity: missing field when expected is provided = fail."""
    if expected_owner_id is not None:
        if op.get("owner_id") is None or str(op["owner_id"]) != str(expected_owner_id):
            return False, "owner_mismatch"
    if expected_tenant_id is not None:
        if op.get("tenant_id") is None or str(op["tenant_id"]) != str(expected_tenant_id):
            return False, "tenant_mismatch"
    if expected_task_id is not None:
        if op.get("task_id") is None or str(op["task_id"]) != str(expected_task_id):
            return False, "task_mismatch"
    if expected_correlation_id is not None:
        if op.get("correlation_id") is None or str(op["correlation_id"]) != str(expected_correlation_id):
            return False, "correlation_mismatch"
    return True, "ok"


def validate_operation_evidence(op: dict, evidence: dict) -> tuple[bool, str]:
    """Strict evidence binding: missing required field is NOT a match."""
    body = evidence.get("body") if isinstance(evidence.get("body"), dict) else {}
    if not isinstance(body, dict):
        body = {}

    def _ev(field: str):
        if field in body and body[field] is not None:
            return body[field]
        if field in evidence and evidence[field] is not None:
            return evidence[field]
        return None

    # operation_id required always
    eoid = _ev("operation_id")
    if eoid is None or str(eoid) != str(op.get("id")):
        return False, "operation_id_mismatch"

    # outcome must be succeeded for SUCCESS transition
    if _ev("outcome") != "succeeded":
        return False, "outcome_not_succeeded"

    # For each binding on the operation, evidence MUST present and match
    checks = [
        ("owner_id", "owner_id"),
        ("tenant_id", "tenant_id"),
        ("task_id", "task_id"),
        ("correlation_id", "correlation_id"),
        ("tool_name", "tool"),
        ("input_digest", "input_digest"),
        ("target_digest", "target_digest"),
    ]
    for op_field, ev_field in checks:
        op_val = op.get(op_field)
        if op_val is None:
            continue  # operation did not set binding — skip
        ev_val = _ev(ev_field)
        if ev_val is None:
            return False, f"{ev_field}_missing"
        if str(ev_val) != str(op_val):
            return False, f"{ev_field}_mismatch"

    return True, "ok"


def validate_operation_job_binding(op: dict, job: dict) -> tuple[bool, str]:
    """Bidirectional operation ↔ ExecutionJob binding."""
    if not op or not job:
        return False, "missing"
    jid = str(job.get("id") or "")
    oid = str(op.get("id") or "")
    if not jid or not oid:
        return False, "missing_id"
    payload = job.get("payload") if isinstance(job.get("payload"), dict) else {}
    job_op = payload.get("operation_id") or job.get("operation_id")
    if job_op is None or str(job_op) != oid:
        return False, "job_operation_id_mismatch"
    if op.get("execution_job_id") is None or str(op["execution_job_id"]) != jid:
        return False, "operation_job_id_mismatch"
    if op.get("tenant_id") is not None:
        jt = job.get("tenant_id")
        if jt is None or str(jt) != str(op["tenant_id"]):
            return False, "tenant_mismatch"
    if op.get("owner_id") is not None:
        jo = job.get("owner_id")
        if jo is None or str(jo) != str(op["owner_id"]):
            return False, "owner_mismatch"
    return True, "ok"


async def find_matching_evidence(operation_id: str) -> Optional[dict]:
    """Deterministic lookup by EvidenceRecord.operation_id column, then body."""
    try:
        from core.database import AsyncSessionLocal, EvidenceRecord
        from sqlalchemy import select
        async with AsyncSessionLocal() as db:
            q = await db.execute(
                select(EvidenceRecord).where(EvidenceRecord.operation_id == operation_id).limit(5)
            )
            rows = list(q.scalars().all())
            if not rows:
                # Fallback: body.operation_id for pre-migration rows
                q2 = await db.execute(select(EvidenceRecord).order_by(EvidenceRecord.created_at.desc()).limit(100))
                for row in q2.scalars().all():
                    body = row.body if isinstance(row.body, dict) else {}
                    if body.get("operation_id") == operation_id:
                        rows = [row]
                        break
            if not rows:
                return None
            row = rows[0]
            body = row.body if isinstance(row.body, dict) else {}
            return {
                "id": row.id,
                "body": body,
                "owner_id": row.owner_id,
                "tenant_id": row.tenant_id,
                "operation_id": getattr(row, "operation_id", None) or body.get("operation_id"),
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
    """Reconcile without executing. Fail closed on identity/evidence mismatch."""
    op = await load_operation(operation_id)
    if op is None:
        return {"ok": False, "reason_code": "operation_missing", "execute": False, "retry": False, "status": None}

    ok_id, reason = validate_operation_identity(
        op,
        expected_task_id=expected_task_id,
        expected_owner_id=expected_owner_id,
        expected_tenant_id=expected_tenant_id,
        expected_correlation_id=expected_correlation_id,
    )
    if not ok_id:
        return {"ok": False, "reason_code": reason, "execute": False, "retry": False, "status": op["status"], "operation": op}

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
        await cancel_operation(operation_id, "reserved_never_started")
        op = await load_operation(operation_id)
        return {"ok": True, "reason_code": "reserved_cancelled", "execute": False, "retry": False, "status": OP_CANCELLED, "operation": op}

    if status == OP_RUNNING:
        evidence = await find_matching_evidence(operation_id)
        if evidence:
            valid, vreason = validate_operation_evidence(op, evidence)
            if valid:
                await complete_operation(operation_id, success=True, evidence_id=evidence.get("id"))
                op = await load_operation(operation_id)
                return {"ok": True, "reason_code": "reconciled_succeeded", "execute": False, "retry": False, "status": OP_SUCCEEDED, "operation": op}
            # Mismatched evidence → UNKNOWN (do not trust)
            await mark_unknown(operation_id, f"evidence_invalid:{vreason}")
            op = await load_operation(operation_id)
            return {"ok": True, "reason_code": "evidence_invalid", "execute": False, "retry": False, "status": OP_UNKNOWN, "operation": op}
        await mark_unknown(operation_id, "running_no_matching_evidence")
        op = await load_operation(operation_id)
        return {"ok": True, "reason_code": "marked_unknown", "execute": False, "retry": False, "status": OP_UNKNOWN, "operation": op}

    return {"ok": False, "reason_code": "unhandled_status", "execute": False, "retry": False, "status": status, "operation": op}


def is_consequential_job_type(job_type: str) -> bool:
    """Unknown/new job types default to consequential."""
    SAFE = frozenset({"read", "inspect", "list", "status", "health", "ping"})
    jt = (job_type or "").lower()
    return jt not in SAFE and not jt.startswith("read_")
