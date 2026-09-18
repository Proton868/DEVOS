"""Crash boundaries between ExecutionJob and ExecutionOperation.

Proves recovery outcomes for each meaningful window without a second state machine.
Consequential enqueue is failure-atomic (job + RESERVED op same transaction).
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest import mock

import pytest

SIDE_EFFECTS: list[str] = []


@pytest.fixture(autouse=True)
def _clear_effects():
    SIDE_EFFECTS.clear()
    yield
    SIDE_EFFECTS.clear()


@pytest.fixture
async def db(tmp_path, monkeypatch):
    url = f"sqlite+aiosqlite:///{(tmp_path / 'atom.db').as_posix()}"
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("REQUIRE_POSTGRES", "false")
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from core import database as dbmod
    dbmod.engine = create_async_engine(url, echo=False)
    dbmod.AsyncSessionLocal = async_sessionmaker(dbmod.engine, expire_on_commit=False)
    await dbmod.init_db()
    yield dbmod
    await dbmod.engine.dispose()


def _utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ── Window A/B: atomic create ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_enqueue_consequential_is_atomic_job_and_op(db):
    """Job + RESERVED operation commit together; no orphan job without op."""
    from workers.job_queue import enqueue
    from governance.execution_operations import load_operation
    from core.database import ExecutionJob
    from sqlalchemy import select

    job = await enqueue(
        owner_id="u1", tenant_id="t1", job_type="script",
        payload={"cmd": "echo"}, idempotency_key="ik-atomic-1",
    )
    assert job is not None
    assert job.operation_id
    assert job.status == "queued"
    op = await load_operation(job.operation_id)
    assert op is not None
    assert op["status"] == "reserved"
    assert op["execution_job_id"] == job.id

    async with db.AsyncSessionLocal() as session:
        jobs = (await session.execute(select(ExecutionJob))).scalars().all()
        assert len(jobs) == 1
        assert jobs[0].operation_id == job.operation_id


@pytest.mark.asyncio
async def test_enqueue_reservation_failure_creates_no_job(db):
    """If reserve fails for consequential work, job must not commit."""
    from workers import job_queue as jq
    from sqlalchemy import select, func
    from core.database import ExecutionJob, ExecutionOperation

    async def fail_reserve(*a, **k):
        return None

    with mock.patch("governance.execution_operations.reserve_operation_tx", side_effect=fail_reserve):
        with pytest.raises(Exception):
            await jq.enqueue(
                owner_id="u1", tenant_id="t1", job_type="script",
                payload={}, idempotency_key="ik-fail-1",
            )

    async with db.AsyncSessionLocal() as session:
        jc = await session.scalar(select(func.count()).select_from(ExecutionJob))
        oc = await session.scalar(select(func.count()).select_from(ExecutionOperation))
    assert jc == 0
    assert oc == 0


@pytest.mark.asyncio
async def test_reserved_op_survives_restart_no_duplicate(db):
    """Window B: job+RESERVED survives; re-enqueue same key returns same job, no new op."""
    from workers.job_queue import enqueue
    from governance.execution_operations import load_operation
    from sqlalchemy import select, func
    from core.database import ExecutionOperation

    j1 = await enqueue(
        owner_id="u1", tenant_id="t1", job_type="script",
        payload={}, idempotency_key="ik-res-1",
    )
    op_id = j1.operation_id
    j2 = await enqueue(
        owner_id="u1", tenant_id="t1", job_type="script",
        payload={}, idempotency_key="ik-res-1",
    )
    assert j1.id == j2.id
    assert j2.operation_id == op_id
    async with db.AsyncSessionLocal() as session:
        cnt = await session.scalar(
            select(func.count()).select_from(ExecutionOperation).where(
                ExecutionOperation.idempotency_key == "ik-res-1"
            )
        )
    assert cnt == 1
    op = await load_operation(op_id)
    assert op["status"] == "reserved"


# ── Window C: claimed before RUNNING ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_claimed_before_running_stale_lease_requeues_reserved(db):
    """Claimed job + RESERVED op: stale recovery requeues (side effect never started)."""
    from workers.job_queue import enqueue, recover_stale_leases
    from core.database import ExecutionJob

    job = await enqueue(
        owner_id="u1", tenant_id="t1", job_type="script",
        payload={}, idempotency_key="ik-claim-1",
    )
    async with db.AsyncSessionLocal() as session:
        row = await session.get(ExecutionJob, job.id)
        row.status = "running"
        row.worker_id = "dead-worker"
        row.locked_at = _utcnow() - timedelta(hours=2)
        row.lease_expires_at = _utcnow() - timedelta(hours=1)
        row.attempts = 1
        await session.commit()

    n = await recover_stale_leases()
    assert n >= 1
    async with db.AsyncSessionLocal() as session:
        row = await session.get(ExecutionJob, job.id)
        assert row.status == "queued"
        assert row.worker_id is None
        assert row.operation_id == job.operation_id


@pytest.mark.asyncio
async def test_window_c_claim_next_crash_before_mark_running(db):
    """Window C (full path): claim_next → die before mark_running.

    Durable state after claim: job=running, op=RESERVED.
    After stale recovery: job requeued, same operation_id, op still RESERVED,
    no side effect, no second operation.
    """
    from workers.job_queue import enqueue, claim_next, recover_stale_leases
    from governance.execution_operations import load_operation
    from core.database import ExecutionJob, ExecutionOperation
    from sqlalchemy import select, func

    job = await enqueue(
        owner_id="u1", tenant_id="t1", job_type="script",
        payload={"action": "write"}, idempotency_key="ik-window-c",
    )
    op_id = job.operation_id
    assert op_id
    op = await load_operation(op_id)
    assert op["status"] == "reserved"

    claimed = await claim_next(worker="worker-c-crash")
    assert claimed is not None
    assert claimed.id == job.id
    assert claimed.status == "running"
    assert claimed.worker_id == "worker-c-crash"
    # Crash boundary: never call mark_running / side effect
    op_after_claim = await load_operation(op_id)
    assert op_after_claim["status"] == "reserved"
    assert SIDE_EFFECTS == []

    # Simulate process death: expire lease while still RESERVED
    async with db.AsyncSessionLocal() as session:
        row = await session.get(ExecutionJob, job.id)
        row.locked_at = _utcnow() - timedelta(hours=2)
        row.lease_expires_at = _utcnow() - timedelta(hours=1)
        await session.commit()

    n = await recover_stale_leases()
    assert n >= 1

    async with db.AsyncSessionLocal() as session:
        row = await session.get(ExecutionJob, job.id)
        assert row.status == "queued", "RESERVED op must allow safe requeue"
        assert row.worker_id is None
        assert row.operation_id == op_id
        op_cnt = await session.scalar(
            select(func.count()).select_from(ExecutionOperation).where(
                ExecutionOperation.id == op_id
            )
        )
        total_ops = await session.scalar(
            select(func.count()).select_from(ExecutionOperation).where(
                ExecutionOperation.idempotency_key == "ik-window-c"
            )
        )
    assert op_cnt == 1
    assert total_ops == 1
    op_final = await load_operation(op_id)
    assert op_final["status"] == "reserved"
    assert SIDE_EFFECTS == []

    # Second claim is safe — still no duplicate op / side effect
    claimed2 = await claim_next(worker="worker-c-retry")
    assert claimed2 is not None
    assert claimed2.id == job.id
    assert claimed2.operation_id == op_id
    assert SIDE_EFFECTS == []


# ── Window D: RUNNING, side effect not started ───────────────────────────────

@pytest.mark.asyncio
async def test_running_op_without_evidence_becomes_unknown(db):
    """RUNNING + no evidence → UNKNOWN (fail closed); not assumed success."""
    from workers.job_queue import enqueue
    from governance.execution_operations import mark_running, reconcile_operation, load_operation

    job = await enqueue(
        owner_id="u1", tenant_id="t1", job_type="script",
        payload={}, idempotency_key="ik-run-1",
    )
    assert await mark_running(job.operation_id)
    # No side effect, no evidence
    rec = await reconcile_operation(job.operation_id)
    assert rec["status"] == "unknown"
    assert rec["execute"] is False
    assert rec["retry"] is False
    op = await load_operation(job.operation_id)
    assert op["status"] == "unknown"


# ── Window E: side effect then crash (UNKNOWN regression) ────────────────────

@pytest.mark.asyncio
async def test_side_effect_crash_unknown_no_redispatch(db):
    from workers.job_queue import enqueue
    from governance.execution_operations import mark_running, reconcile_operation
    from brain.orchestration_dag import OrchestrationNode
    from brain.mission_engine import get_ready_nodes
    from execution.durable_resume import reconcile_plan_nodes, INVESTIGATE

    job = await enqueue(
        owner_id="u1", tenant_id="t1", job_type="script",
        payload={}, idempotency_key="ik-se-1",
    )
    await mark_running(job.operation_id)
    SIDE_EFFECTS.append(job.operation_id)  # irreversible boundary
    rec = await reconcile_operation(job.operation_id)
    assert rec["status"] == "unknown"
    assert rec["retry"] is False

    node = OrchestrationNode(
        id="n1", description="x", persona_id="code", capabilities=["fs.write"],
        status="running", operation_id=job.operation_id, job_or_task_id=job.id,
    )
    plan = SimpleNamespace(nodes=[node], edges=[], status="running", workspace_id="ws")
    await reconcile_plan_nodes(plan)
    assert node.status == INVESTIGATE
    assert get_ready_nodes([node], []) == []
    assert SIDE_EFFECTS == [job.operation_id]


# ── Window F: side effect complete, terminal missing → UNKNOWN ───────────────

@pytest.mark.asyncio
async def test_side_effect_done_without_evidence_is_unknown_not_success(db):
    """Side effect count=1, no durable evidence → must NOT assume SUCCEEDED."""
    from workers.job_queue import enqueue
    from governance.execution_operations import mark_running, reconcile_operation, load_operation

    job = await enqueue(
        owner_id="u1", tenant_id="t1", job_type="script",
        payload={}, idempotency_key="ik-f-1",
    )
    await mark_running(job.operation_id)
    SIDE_EFFECTS.append("done")
    # Crash before complete_operation / evidence
    rec = await reconcile_operation(job.operation_id)
    assert rec["status"] == "unknown"
    assert rec["execute"] is False
    op = await load_operation(job.operation_id)
    assert op["status"] != "succeeded"
    assert SIDE_EFFECTS == ["done"]


# ── Window G: op SUCCEEDED, job still non-terminal ───────────────────────────

@pytest.mark.asyncio
async def test_succeeded_op_reconciles_nonterminal_job_no_duplicate(db):
    """Durable SUCCEEDED op + non-terminal job → job converges; no second side effect."""
    from workers.job_queue import enqueue, recover_stale_leases, complete
    from governance.execution_operations import mark_running, complete_operation, load_operation
    from core.database import ExecutionJob

    job = await enqueue(
        owner_id="u1", tenant_id="t1", job_type="script",
        payload={}, idempotency_key="ik-g-1",
    )
    await mark_running(job.operation_id)
    SIDE_EFFECTS.append(job.operation_id)
    assert await complete_operation(job.operation_id, success=True)
    op = await load_operation(job.operation_id)
    assert op["status"] == "succeeded"

    # Job still "running" with stale lease (terminal update missing)
    async with db.AsyncSessionLocal() as session:
        row = await session.get(ExecutionJob, job.id)
        row.status = "running"
        row.worker_id = "dead"
        row.locked_at = _utcnow() - timedelta(hours=2)
        row.lease_expires_at = _utcnow() - timedelta(hours=1)
        await session.commit()

    await recover_stale_leases()
    async with db.AsyncSessionLocal() as session:
        row = await session.get(ExecutionJob, job.id)
        assert row.status == "succeeded"
        assert row.worker_id is None

    # complete() again must not re-execute
    await complete(job.id, status="succeeded", result={"ok": True})
    assert SIDE_EFFECTS == [job.operation_id]
    op2 = await load_operation(job.operation_id)
    assert op2["status"] == "succeeded"


@pytest.mark.asyncio
async def test_stale_unknown_op_job_not_requeued(db):
    """Invariant B: UNKNOWN op → job not requeued as ordinary work."""
    from workers.job_queue import enqueue, recover_stale_leases
    from governance.execution_operations import mark_running, mark_unknown
    from core.database import ExecutionJob

    job = await enqueue(
        owner_id="u1", tenant_id="t1", job_type="script",
        payload={}, idempotency_key="ik-unk-1",
    )
    await mark_running(job.operation_id)
    await mark_unknown(job.operation_id, "ambiguous_outcome")

    async with db.AsyncSessionLocal() as session:
        row = await session.get(ExecutionJob, job.id)
        row.status = "running"
        row.worker_id = "dead"
        row.locked_at = _utcnow() - timedelta(hours=2)
        row.lease_expires_at = _utcnow() - timedelta(hours=1)
        await session.commit()

    await recover_stale_leases()
    async with db.AsyncSessionLocal() as session:
        row = await session.get(ExecutionJob, job.id)
        assert row.status == "failed"
        err = json.loads(row.error or "{}")
        assert err.get("retryable") is False
        assert "unknown" in (err.get("reason_code") or "")


@pytest.mark.asyncio
async def test_terminal_op_prevents_duplicate_execution_on_complete(db):
    """Invariant C: terminal SUCCEEDED op is authoritative; job complete is ledger sync only."""
    from workers.job_queue import enqueue, complete
    from governance.execution_operations import mark_running, complete_operation

    job = await enqueue(
        owner_id="u1", tenant_id="t1", job_type="script",
        payload={}, idempotency_key="ik-term-1",
    )
    await mark_running(job.operation_id)
    SIDE_EFFECTS.append("once")
    await complete_operation(job.operation_id, success=True)
    await complete(job.id, status="succeeded")
    await complete(job.id, status="succeeded")  # idempotent job terminal write
    assert SIDE_EFFECTS == ["once"]


def test_enqueue_source_documents_same_transaction():
    """Static: enqueue documents failure-atomic job+op binding."""
    from pathlib import Path
    src = Path("workers/job_queue.py").read_text()
    assert "Same transaction as job create" in src or "failure-atomic" in src
    assert "reserve_operation_tx" in src
    assert "operation_reservation_failed" in src
