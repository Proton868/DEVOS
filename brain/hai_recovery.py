"""
HAI recovery boundary (Stage 3L).

Restores cognitive state from durable checkpoint and reconciles ExecutionJob.
Does NOT execute tools, grant authority, or create a second runtime.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from cognitive.hai_checkpoint import CheckpointError, HAICheckpoint, reconcile_with_execution

logger = logging.getLogger("devos.hai_recovery")


async def recover_hai_task(
    task_id: str,
    *,
    owner_id: str,
    job_status: Optional[str] = None,
    job_id: Optional[str] = None,
    lease_seconds: int = 60,
) -> dict[str, Any]:
    """
    Process-safe recovery entrypoint.

    Returns structured recovery result. Never invents authority from checkpoint.
    """
    from brain.agent_task_store import (
        claim_task_recovery,
        load_hai_checkpoint,
        load_task_identity,
        release_task_recovery,
    )

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

        # Identity NEVER comes from checkpoint
        recon = reconcile_with_execution(
            checkpoint,
            job_status=job_status or checkpoint.last_job_status,
            job_id=job_id or checkpoint.last_job_id,
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
                # cognitive state only
                "state": checkpoint.state,
            },
            "reconciliation": recon.to_dict(),
            "execute": False,  # recovery never auto-executes
            "retry": bool(recon.retry),
            "outcome": recon.outcome,
        }
    finally:
        # Leave lease held by caller for multi-step recovery; auto-release only on hard deny paths is avoided.
        # Caller should release when done. For failed validation we release.
        pass


async def finish_recovery(task_id: str, owner_id: str) -> bool:
    from brain.agent_task_store import release_task_recovery
    return await release_task_recovery(task_id, owner_id)
