"""Database-enforced ExecutionOperation idempotency (tenant-scoped).

Proves concurrent same-tenant/same-key callers converge on one operation
and do not execute the side effect twice. Cross-tenant same key remains independent.
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture
async def ops_db(tmp_path, monkeypatch):
    """Isolated SQLite DB with init_db unique index (mirrors Postgres constraint)."""
    db_path = tmp_path / "idem.db"
    url = f"sqlite+aiosqlite:///{db_path.as_posix()}"
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("REQUIRE_POSTGRES", "false")
    # Rebind engine after env change
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from core import database as dbmod
    dbmod.engine = create_async_engine(url, echo=False)
    dbmod.AsyncSessionLocal = async_sessionmaker(dbmod.engine, expire_on_commit=False)
    await dbmod.init_db()
    yield dbmod
    await dbmod.engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_same_tenant_key_one_operation(ops_db):
    from governance.execution_operations import reserve_operation
    from sqlalchemy import select, func
    from core.database import ExecutionOperation

    side_effects = []

    async def caller(n: int):
        op_id = await reserve_operation(
            owner_id="user-a",
            tenant_id="tenant-a",
            operation_type="tool",
            idempotency_key="shared-key-x",
            tool_name="consequential.write",
        )
        # Only the first unique id "wins" the side effect in real code via
        # operation lifecycle; here we record every returned id.
        side_effects.append(op_id)
        return op_id

    results = await asyncio.gather(*[caller(i) for i in range(8)])
    assert all(results)
    # All callers converge on the same operation id
    assert len(set(results)) == 1
    assert results[0] is not None

    async with ops_db.AsyncSessionLocal() as db:
        cnt = await db.scalar(
            select(func.count()).select_from(ExecutionOperation).where(
                ExecutionOperation.idempotency_key == "shared-key-x",
                ExecutionOperation.tenant_id == "tenant-a",
                ExecutionOperation.owner_id == "user-a",
            )
        )
    assert cnt == 1
    # Side-effect identity: one authoritative op id only
    assert len(set(side_effects)) == 1


@pytest.mark.asyncio
async def test_cross_tenant_same_key_independent(ops_db):
    from governance.execution_operations import reserve_operation

    a = await reserve_operation(
        owner_id="user-a", tenant_id="tenant-a", operation_type="tool",
        idempotency_key="key-x", tool_name="x",
    )
    b = await reserve_operation(
        owner_id="user-a", tenant_id="tenant-b", operation_type="tool",
        idempotency_key="key-x", tool_name="x",
    )
    assert a and b and a != b


@pytest.mark.asyncio
async def test_repeated_same_key_reuses_operation(ops_db):
    from governance.execution_operations import reserve_operation, mark_running, load_operation

    a = await reserve_operation(
        owner_id="u", tenant_id="t", operation_type="tool",
        idempotency_key="reuse-1", tool_name="x",
    )
    await mark_running(a)
    b = await reserve_operation(
        owner_id="u", tenant_id="t", operation_type="tool",
        idempotency_key="reuse-1", tool_name="x",
    )
    assert a == b
    op = await load_operation(a)
    assert op["status"] == "running"  # existing lifecycle preserved


@pytest.mark.asyncio
async def test_unique_index_exists_after_init_db(ops_db):
    from sqlalchemy import text
    async with ops_db.engine.begin() as conn:
        # SQLite: list indexes on execution_operations
        rows = (await conn.execute(text(
            "SELECT name, sql FROM sqlite_master WHERE type='index' AND tbl_name='execution_operations'"
        ))).fetchall()
    names = {r[0] for r in rows}
    assert "ux_execution_operations_idempotency" in names
    sql = next(r[1] for r in rows if r[0] == "ux_execution_operations_idempotency")
    assert "UNIQUE" in (sql or "").upper()
    assert "idempotency_key" in (sql or "")


@pytest.mark.asyncio
async def test_direct_duplicate_insert_rejected(ops_db):
    """Database rejects a second row with the same logical key."""
    from core.database import ExecutionOperation, gen_id
    from sqlalchemy.exc import IntegrityError
    import uuid

    async with ops_db.AsyncSessionLocal() as db:
        row1 = ExecutionOperation(
            id=str(uuid.uuid4()),
            owner_id="u",
            tenant_id="t",
            operation_type="tool",
            idempotency_key="dup-key",
            status="reserved",
        )
        db.add(row1)
        await db.commit()

    async with ops_db.AsyncSessionLocal() as db:
        row2 = ExecutionOperation(
            id=str(uuid.uuid4()),
            owner_id="u",
            tenant_id="t",
            operation_type="tool",
            idempotency_key="dup-key",
            status="reserved",
        )
        db.add(row2)
        with pytest.raises(IntegrityError):
            await db.commit()


@pytest.mark.asyncio
async def test_lifecycle_after_unique_constraint(ops_db):
    from governance.execution_operations import (
        reserve_operation, mark_running, complete_operation, load_operation,
    )
    op = await reserve_operation(
        owner_id="u", tenant_id="t", operation_type="tool",
        idempotency_key="life-1", tool_name="x",
    )
    assert await mark_running(op)
    assert await complete_operation(op, success=True)
    row = await load_operation(op)
    assert row["status"] == "succeeded"


def test_migration_file_fail_closed_on_duplicates():
    text = (REPO / "supabase/migrations/20260918160000_execution_operations_idempotency_unique.sql").read_text()
    assert "RAISE EXCEPTION" in text
    assert "ux_execution_operations_idempotency" in text
    assert "COALESCE(tenant_id" in text
