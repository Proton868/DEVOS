"""
HAI recovery boundary (Stage 3L / 3L.1).

Restores cognitive state from durable checkpoint and reconciles ExecutionJob.
Does NOT execute tools, grant authority, or create a second runtime.

Execution truth comes from durable ExecutionJob rows — never from caller args.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from cognitive.hai_checkpoint import CheckpointError, HAICheckpoint, reconcile_with_execution

logger = logging.getLogger("devos.hai_recovery")


async def _load_execution_job(job_id: Optional[str]) -> Optional[dict]:
    if not job_id:
        return None
    try:
        from core.database import AsyncSessionLocal, ExecutionJob
        async with AsyncSessionLocal() as db:
            row = await db.get(ExecutionJob, job_id)
            if row is None:
                return None
            return {
                "id": row.id,
                "status": row.status,
                "owner_id": getattr(row, "owner_id", None),
                "tenant_id": getattr(row, "tenant_id", None),
                "correlation": getattr(row, "correlation", None),
                "request_id": getattr(row, "request_id", None),
                "job_type": getattr(row, "job_type", None),
            }
    except Exception:
        logger.debug("load ExecutionJob failed", exc_info=True)
        return None


async def recover_hai_task(
    task_id: str,
    *,
    owner_id: str,
    # Deprecated: ignored for execution truth (kept for call-site compatibility)
    job_status: Optional[str] = None,
    job_id: Optional[str] = None,
    lease_seconds: int = 60,
) -> dict[str, Any]:
    """
    Process-safe recovery entrypoint.

    - Claims atomic recovery lease
    - Loads AgentTaskRecord identity
    - Validates HAICheckpoint (checksum + task_id bind)
    - Loads durable ExecutionJob referenced by checkpoint
    - Reconciles; never executes tools
    """
    from brain.agent_task_store import (
        claim_task_recovery,
        load_hai_checkpoint,
        load_task_identity,
        release_task_recovery,
    )

    # Note: job_status / job_id parameters are intentionally NOT used as authority.
    _ = job_status  # discarded
    caller_job_id = job_id  # only used if checkpoint has no last_job_id; still verified via DB

    claimed = await claim_task_recovery(task_id, owner_id, lease_seconds=lease_seconds)
    if not claimed:
        return {
            "ok": False,
            "reason_code": "lease_denied",
            "message": "Could not claim recovery lease",
            "execute": False,
            "retry": False,
        }

    try:
        identity = await load_task_identity(task_id)
        if identity is None:
            return {
                "ok": False,
                "reason_code": "task_not_found",
                "execute": False,
                "retry": False,
            }

        status = (identity.get("status") or "").lower()
        if status in ("cancelled", "canceled"):
            return {
                "ok": True,
                "reason_code": "cancelled",
                "identity": identity,
                "execute": False,
                "retry": False,
                "outcome": "cancelled",
            }

        raw = await load_hai_checkpoint(task_id)
        if raw is None:
            return {
                "ok": False,
                "reason_code": "checkpoint_missing",
                "identity": identity,
                "execute": False,
                "retry": False,
            }

        try:
            checkpoint = HAICheckpoint.from_dict(raw, verify=True)
        except CheckpointError as e:
            return {
                "ok": False,
                "reason_code": "checkpoint_invalid",
                "message": str(e),
                "identity": identity,
                "execute": False,
                "retry": False,
            }

        # Bind checkpoint to task
        if str(checkpoint.task_id or "") != str(task_id):
            return {
                "ok": False,
                "reason_code": "checkpoint_invalid",
                "message": "checkpoint.task_id does not match AgentTaskRecord.id",
                "identity": identity,
                "execute": False,
                "retry": False,
            }

        # Correlation integrity: when both present, must match
        task_corr = identity.get("correlation_id") or ""
        cp_corr = checkpoint.correlation_id or ""
        if task_corr and cp_corr and str(task_corr) != str(cp_corr):
            return {
                "ok": False,
                "reason_code": "checkpoint_invalid",
                "message": "correlation_id mismatch between task and checkpoint",
                "identity": identity,
                "execute": False,
                "retry": False,
            }

        # Authoritative ExecutionJob from durable store
        ref_job_id = checkpoint.last_job_id or None
        # If checkpoint has no job, ignore caller job_id for truth (no job to reconcile)
        durable_job = await _load_execution_job(ref_job_id) if ref_job_id else None

        if ref_job_id and durable_job is None:
            return {
                "ok": False,
                "reason_code": "job_missing",
                "message": f"Referenced ExecutionJob {ref_job_id} not found",
                "identity": identity,
                "checkpoint_job_id": ref_job_id,
                "execute": False,
                "retry": False,
            }

        # Optional tenant/user consistency when job carries identity
        if durable_job:
            # Soft consistency: job owner_id should match task user_id when both set
            if durable_job.get("owner_id") and identity.get("user_id"):
                if str(durable_job["owner_id"]) != str(identity["user_id"]):
                    return {
                        "ok": False,
                        "reason_code": "job_identity_mismatch",
                        "message": "ExecutionJob owner_id does not match AgentTask user_id",
                        "identity": identity,
                        "execute": False,
                        "retry": False,
                    }
            if durable_job.get("tenant_id") and identity.get("tenant_id"):
                if str(durable_job["tenant_id"]) != str(identity["tenant_id"]):
                    return {
                        "ok": False,
                        "reason_code": "job_identity_mismatch",
                        "message": "ExecutionJob tenant_id does not match AgentTask",
                        "identity": identity,
                        "execute": False,
                        "retry": False,
                    }

        durable_status = durable_job["status"] if durable_job else None
        durable_jid = durable_job["id"] if durable_job else None

        recon = reconcile_with_execution(
            checkpoint,
            job_status=durable_status,
            job_id=durable_jid,
            task_status=status,
        )

        return {
            "ok": True,
            "reason_code": "recovered",
            "identity": identity,
            "checkpoint": {
                "task_id": checkpoint.task_id,
                "lifecycle": checkpoint.lifecycle,
                "state_version": checkpoint.state_version,
                "correlation_id": checkpoint.correlation_id,
                "last_job_id": checkpoint.last_job_id,
                "last_job_status": checkpoint.last_job_status,
                "checksum": checkpoint.checksum,
                "state": checkpoint.state,
            },
            "execution_job": durable_job,
            "reconciliation": recon.to_dict(),
            "execute": False,
            "retry": bool(recon.retry),
            "outcome": recon.outcome,
        }
    finally:
        pass


async def finish_recovery(task_id: str, owner_id: str) -> bool:
    from brain.agent_task_store import release_task_recovery
    return await release_task_recovery(task_id, owner_id)
