"""Idempotency-key construction contract for consequential operations.

Documents and proves current semantics: the key identifies the logical
operation; arguments are not a uniqueness axis.
"""
from __future__ import annotations

import pytest


@pytest.fixture
async def db(tmp_path, monkeypatch):
    url = f"sqlite+aiosqlite:///{(tmp_path / 'idemp-contract.db').as_posix()}"
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("REQUIRE_POSTGRES", "false")
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from core import database as dbmod
    dbmod.engine = create_async_engine(url, echo=False)
    dbmod.AsyncSessionLocal = async_sessionmaker(dbmod.engine, expire_on_commit=False)
    await dbmod.init_db()
    yield dbmod
    await dbmod.engine.dispose()


@pytest.mark.asyncio
async def test_same_logical_operation_same_key_same_op(db):
    from governance.execution_operations import reserve_operation, load_operation

    a = await reserve_operation(
        owner_id="u1", tenant_id="t1", operation_type="tool",
        idempotency_key="logical-op-1", tool_name="write",
        args={"path": "/a", "content": "x"},
    )
    b = await reserve_operation(
        owner_id="u1", tenant_id="t1", operation_type="tool",
        idempotency_key="logical-op-1", tool_name="write",
        args={"path": "/a", "content": "x"},
    )
    assert a and a == b
    op = await load_operation(a)
    assert op["idempotency_key"] == "logical-op-1"


@pytest.mark.asyncio
async def test_different_logical_ops_use_different_keys(db):
    from governance.execution_operations import reserve_operation

    a = await reserve_operation(
        owner_id="u1", tenant_id="t1", operation_type="tool",
        idempotency_key="op-write-file-a", tool_name="write",
    )
    b = await reserve_operation(
        owner_id="u1", tenant_id="t1", operation_type="tool",
        idempotency_key="op-write-file-b", tool_name="write",
    )
    assert a and b and a != b


@pytest.mark.asyncio
async def test_same_key_different_tenants_independent(db):
    from governance.execution_operations import reserve_operation

    a = await reserve_operation(
        owner_id="u1", tenant_id="tenant-a", operation_type="tool",
        idempotency_key="shared-key", tool_name="x",
    )
    b = await reserve_operation(
        owner_id="u1", tenant_id="tenant-b", operation_type="tool",
        idempotency_key="shared-key", tool_name="x",
    )
    assert a and b and a != b


@pytest.mark.asyncio
async def test_same_key_different_owners_independent(db):
    from governance.execution_operations import reserve_operation

    a = await reserve_operation(
        owner_id="owner-a", tenant_id="t1", operation_type="tool",
        idempotency_key="shared-key", tool_name="x",
    )
    b = await reserve_operation(
        owner_id="owner-b", tenant_id="t1", operation_type="tool",
        idempotency_key="shared-key", tool_name="x",
    )
    assert a and b and a != b


@pytest.mark.asyncio
async def test_reusing_key_does_not_create_second_operation(db):
    from governance.execution_operations import reserve_operation
    from core.database import ExecutionOperation
    from sqlalchemy import select, func

    key = "once-only-op"
    ids = []
    for _ in range(5):
        oid = await reserve_operation(
            owner_id="u1", tenant_id="t1", operation_type="tool",
            idempotency_key=key, tool_name="x",
        )
        ids.append(oid)
    assert len(set(ids)) == 1
    async with db.AsyncSessionLocal() as session:
        cnt = await session.scalar(
            select(func.count()).select_from(ExecutionOperation).where(
                ExecutionOperation.idempotency_key == key,
                ExecutionOperation.owner_id == "u1",
                ExecutionOperation.tenant_id == "t1",
            )
        )
    assert cnt == 1


@pytest.mark.asyncio
async def test_same_key_different_args_reuses_operation_current_contract(db):
    """Documented contract: args are NOT a uniqueness axis.

    Same scoped key + materially different arguments still returns the
    existing operation identity. Callers must encode argument identity
    into the key when different args mean different logical operations.
    """
    from governance.execution_operations import reserve_operation, load_operation

    a = await reserve_operation(
        owner_id="u1", tenant_id="t1", operation_type="tool",
        idempotency_key="path-sensitive-missing",
        tool_name="write",
        args={"path": "/one", "content": "aaa"},
    )
    b = await reserve_operation(
        owner_id="u1", tenant_id="t1", operation_type="tool",
        idempotency_key="path-sensitive-missing",
        tool_name="write",
        args={"path": "/two", "content": "bbb"},
    )
    assert a == b
    op = await load_operation(a)
    assert op["id"] == a
    # First reservation's digest may be present; second does not create a new row
    assert op["idempotency_key"] == "path-sensitive-missing"


@pytest.mark.asyncio
async def test_new_idempotency_key_includes_body_for_callers(db):
    """Recommended helper: different bodies → different keys."""
    from governance.reliability import new_idempotency_key

    k1 = new_idempotency_key(
        tenant_id="t", actor_id="u", capability="fs.write",
        operation="write", body={"path": "/a"},
    )
    k2 = new_idempotency_key(
        tenant_id="t", actor_id="u", capability="fs.write",
        operation="write", body={"path": "/b"},
    )
    k1b = new_idempotency_key(
        tenant_id="t", actor_id="u", capability="fs.write",
        operation="write", body={"path": "/a"},
    )
    assert k1 != k2
    assert k1 == k1b


@pytest.mark.asyncio
async def test_absent_key_is_non_idempotent(db):
    from governance.execution_operations import reserve_operation

    a = await reserve_operation(
        owner_id="u1", tenant_id="t1", operation_type="tool", tool_name="x",
    )
    b = await reserve_operation(
        owner_id="u1", tenant_id="t1", operation_type="tool", tool_name="x",
    )
    assert a and b and a != b
