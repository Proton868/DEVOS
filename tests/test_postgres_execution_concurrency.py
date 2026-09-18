"""PostgreSQL concurrency proofs for durable execution invariants.

Requires live Postgres (CI service or DEVOS_TEST_DATABASE_URL).
Uses real concurrent connections — not sequential loops.
"""
from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest

pytestmark = pytest.mark.postgres


def _pg_url() -> str:
    url = (
        os.environ.get("DEVOS_TEST_DATABASE_URL")
        or os.environ.get("DATABASE_URL")
        or ""
    ).strip()
    if not url or not url.lower().startswith("postgres"):
        pytest.skip("PostgreSQL DATABASE_URL / DEVOS_TEST_DATABASE_URL required")
    return url


def _utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


@pytest.fixture
async def pg_db(monkeypatch):
    """Isolated Postgres schema for concurrency tests (wide pool for races)."""
    url = _pg_url()
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("REQUIRE_POSTGRES", "true")
    monkeypatch.setenv("DEVOS_ALLOW_LIVE_POSTGRES", "1")
    monkeypatch.setenv("DEVOS_SCHEMA_CREATE_ALL", "1")

    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from core import database as dbmod

    # Normalize for asyncpg
    low = url.lower()
    async_url = url
    if low.startswith("postgresql://") or low.startswith("postgres://"):
        async_url = "postgresql+asyncpg://" + url.split("://", 1)[1]
    elif "+psycopg" in low:
        async_url = "postgresql+asyncpg://" + url.split("://", 1)[1]

    # Wide pool so concurrent reserve/claim tasks get real overlapping connections
    engine = create_async_engine(
        async_url,
        echo=False,
        pool_size=16,
        max_overflow=8,
        pool_pre_ping=True,
    )
    dbmod.engine = engine
    dbmod.AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)

    # Postgres production refuses create_all in init_db (migrations own schema).
    # CI/test harness creates missing tables then applies the unique index migration.
    from sqlalchemy import text
    async with engine.begin() as conn:
        await conn.run_sync(dbmod.Base.metadata.create_all)
        await conn.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS ux_execution_operations_idempotency "
            "ON execution_operations (owner_id, operation_type, idempotency_key, COALESCE(tenant_id, '')) "
            "WHERE idempotency_key IS NOT NULL"
        ))
    await dbmod.init_db()  # column/index bootstrap where still applicable

    yield dbmod
    await engine.dispose()


@pytest.mark.asyncio
async def test_pg_unique_index_exists(pg_db):
    from sqlalchemy import text
    async with pg_db.engine.connect() as conn:
        row = (await conn.execute(text(
            "SELECT indexname, indexdef FROM pg_indexes "
            "WHERE tablename = 'execution_operations' "
            "AND indexname = 'ux_execution_operations_idempotency'"
        ))).fetchone()
    assert row is not None, "ux_execution_operations_idempotency must exist on Postgres"
    assert "UNIQUE" in (row[1] or "").upper()
    assert "idempotency_key" in (row[1] or "")


@pytest.mark.asyncio
async def test_pg_concurrent_reserve_same_key_one_operation(pg_db):
    """Multiple concurrent connections: same scoped key → exactly one operation."""
    from governance.execution_operations import reserve_operation
    from core.database import ExecutionOperation
    from sqlalchemy import select, func

    barrier = asyncio.Barrier(8)
    key = f"pg-race-{uuid.uuid4().hex}"

    async def contender(i: int) -> str:
        await barrier.wait()  # release all at once
        op_id = await reserve_operation(
            owner_id="owner-pg",
            tenant_id="tenant-pg",
            operation_type="tool",
            idempotency_key=key,
            tool_name="consequential.write",
        )
        assert op_id, f"contender {i} got None"
        return op_id

    results = await asyncio.gather(*[contender(i) for i in range(8)])
    assert len(set(results)) == 1
    assert all(r == results[0] for r in results)

    async with pg_db.AsyncSessionLocal() as session:
        cnt = await session.scalar(
            select(func.count()).select_from(ExecutionOperation).where(
                ExecutionOperation.idempotency_key == key,
                ExecutionOperation.owner_id == "owner-pg",
                ExecutionOperation.tenant_id == "tenant-pg",
            )
        )
    assert cnt == 1


@pytest.mark.asyncio
async def test_pg_cross_tenant_same_key_independent(pg_db):
    from governance.execution_operations import reserve_operation

    key = f"pg-xt-{uuid.uuid4().hex}"
    a = await reserve_operation(
        owner_id="o", tenant_id="tenant-a", operation_type="tool",
        idempotency_key=key, tool_name="x",
    )
    b = await reserve_operation(
        owner_id="o", tenant_id="tenant-b", operation_type="tool",
        idempotency_key=key, tool_name="x",
    )
    assert a and b and a != b


@pytest.mark.asyncio
async def test_pg_different_owners_same_key_independent(pg_db):
    from governance.execution_operations import reserve_operation

    key = f"pg-xo-{uuid.uuid4().hex}"
    a = await reserve_operation(
        owner_id="owner-1", tenant_id="t", operation_type="tool",
        idempotency_key=key, tool_name="x",
    )
    b = await reserve_operation(
        owner_id="owner-2", tenant_id="t", operation_type="tool",
        idempotency_key=key, tool_name="x",
    )
    assert a and b and a != b


@pytest.mark.asyncio
async def test_pg_direct_duplicate_insert_rejected(pg_db):
    from core.database import ExecutionOperation
    from sqlalchemy.exc import IntegrityError
    import uuid as _uuid

    key = f"pg-dup-{_uuid.uuid4().hex}"
    async with pg_db.AsyncSessionLocal() as session:
        session.add(ExecutionOperation(
            id=str(_uuid.uuid4()), owner_id="u", tenant_id="t",
            operation_type="tool", idempotency_key=key, status="reserved",
        ))
        await session.commit()

    async with pg_db.AsyncSessionLocal() as session:
        session.add(ExecutionOperation(
            id=str(_uuid.uuid4()), owner_id="u", tenant_id="t",
            operation_type="tool", idempotency_key=key, status="reserved",
        ))
        with pytest.raises(IntegrityError):
            await session.commit()


@pytest.mark.asyncio
async def test_pg_operation_lifecycle_after_unique(pg_db):
    from governance.execution_operations import (
        reserve_operation, mark_running, complete_operation, load_operation,
    )
    key = f"pg-life-{uuid.uuid4().hex}"
    op = await reserve_operation(
        owner_id="u", tenant_id="t", operation_type="tool",
        idempotency_key=key, tool_name="x",
    )
    assert await mark_running(op)
    assert await complete_operation(op, success=True)
    row = await load_operation(op)
    assert row["status"] == "succeeded"


@pytest.mark.asyncio
async def test_pg_two_workers_cannot_both_claim_same_job(pg_db):
    """FOR UPDATE SKIP LOCKED / CAS: only one worker owns the job."""
    from workers.job_queue import enqueue, claim_next
    from core.database import ExecutionJob
    from sqlalchemy import select, func

    job = await enqueue(
        owner_id="u1", tenant_id="t1", job_type="script",
        payload={"x": 1}, idempotency_key=f"pg-claim-{uuid.uuid4().hex}",
    )
    assert job.status == "queued"
    op_id = job.operation_id

    barrier = asyncio.Barrier(6)

    async def worker(wid: str):
        await barrier.wait()
        return await claim_next(worker=wid)

    results = await asyncio.gather(
        *[worker(f"w-{i}") for i in range(6)]
    )
    winners = [r for r in results if r is not None]
    assert len(winners) == 1, f"expected exactly one claim, got {len(winners)}"
    assert winners[0].id == job.id
    assert winners[0].worker_id is not None
    assert winners[0].operation_id == op_id

    async with pg_db.AsyncSessionLocal() as session:
        row = await session.get(ExecutionJob, job.id)
        assert row.status == "running"
        assert row.worker_id == winners[0].worker_id
        # Exactly one running claim for this job
        running = await session.scalar(
            select(func.count()).select_from(ExecutionJob).where(
                ExecutionJob.id == job.id,
                ExecutionJob.status == "running",
            )
        )
    assert running == 1
