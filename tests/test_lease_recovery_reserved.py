"""Unit: recover_stale_leases requeues RESERVED ops without reconcile/cancel."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch


def test_reserved_op_requeues_not_cancelled():
    from workers import job_queue as jq

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    job = SimpleNamespace(
        id="job-1",
        status="running",
        payload={"operation_id": "op-1"},
        operation_id="op-1",
        correlation=None,
        job_type="staging_p0_hold",
        worker_id="worker-dead",
        locked_at=now - timedelta(seconds=60),
        lease_expires_at=now - timedelta(seconds=10),
        error=None,
    )

    result = MagicMock()
    result.scalars.return_value.all.return_value = [job]
    session = AsyncMock()
    session.execute = AsyncMock(return_value=result)
    session.commit = AsyncMock()

    async def _run():
        with patch(
            "governance.execution_operations.load_operation",
            new=AsyncMock(return_value={"id": "op-1", "status": "reserved"}),
        ):
            with patch(
                "governance.execution_operations.reconcile_operation",
                new=AsyncMock(side_effect=AssertionError("reconcile must not run for reserved")),
            ):
                return await jq.recover_stale_leases(session)

    n = asyncio.run(_run())
    assert n == 1
    assert job.status == "queued"
    assert job.worker_id is None
    assert job.lease_expires_at is None


def test_running_op_unknown_not_requeued():
    from workers import job_queue as jq

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    job = SimpleNamespace(
        id="job-2",
        status="running",
        payload={"operation_id": "op-2"},
        operation_id="op-2",
        correlation=None,
        job_type="send_email",
        worker_id="worker-dead",
        locked_at=now - timedelta(seconds=60),
        lease_expires_at=now - timedelta(seconds=10),
        error=None,
    )
    result = MagicMock()
    result.scalars.return_value.all.return_value = [job]
    session = AsyncMock()
    session.execute = AsyncMock(return_value=result)
    session.commit = AsyncMock()

    async def _run():
        with patch(
            "governance.execution_operations.load_operation",
            new=AsyncMock(return_value={"id": "op-2", "status": "running"}),
        ):
            with patch(
                "governance.execution_operations.reconcile_operation",
                new=AsyncMock(
                    return_value={
                        "ok": True,
                        "status": "unknown",
                        "reason_code": "marked_unknown",
                        "retry": False,
                    }
                ),
            ):
                return await jq.recover_stale_leases(session)

    n = asyncio.run(_run())
    assert n == 1
    assert job.status == "failed"
    assert "operation_unknown" in (job.error or "")
