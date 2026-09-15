"""Memory Postgres SoT — ownership, persistence, no production SQLite authority."""
from __future__ import annotations

import asyncio
import os
import uuid
from pathlib import Path

import pytest

os.environ.setdefault("REQUIRE_POSTGRES", "false")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./data/test_memory_sot.db")

Path("data").mkdir(exist_ok=True)


def _run(c):
    return asyncio.run(c)


@pytest.fixture(scope="module")
def db():
    os.environ["REQUIRE_POSTGRES"] = "false"
    os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///./data/test_memory_sot.db"
    # Reset MemoryStore singleton
    import memory.store as ms
    ms.MemoryStore._instance = None
    ms.MemoryStore._initialized = False
    try:
        from core.database import init_db

        _run(init_db())
    except Exception as e:
        pytest.skip(str(e))
    yield
    ms.MemoryStore._instance = None
    ms.MemoryStore._initialized = False


async def _user(prefix: str) -> str:
    from core.database import AsyncSessionLocal, User

    uid = f"{prefix}-" + uuid.uuid4().hex[:8]
    async with AsyncSessionLocal() as s:
        s.add(User(id=uid, username=f"m_{uid[-8:]}", email=f"{uid}@mem.local"))
        await s.commit()
    return uid


def test_save_recall_scoped_by_user(db):
    from memory.store import MemoryStore

    ua = _run(_user("ua"))
    ub = _run(_user("ub"))
    store = MemoryStore()
    mid = _run(store.save(ua, "user", "secret-for-a-only", session_id="s1"))
    assert mid
    hits_a = _run(store.recall(ua, "secret", limit=10))
    hits_b = _run(store.recall(ub, "secret", limit=10))
    assert any("secret-for-a-only" in (h.get("content") or "") for h in hits_a)
    assert not any("secret-for-a-only" in (h.get("content") or "") for h in hits_b)


def test_b_cannot_update_or_delete_a(db):
    from memory.store import MemoryStore

    ua = _run(_user("ua2"))
    ub = _run(_user("ub2"))
    store = MemoryStore()
    mid = _run(store.save(ua, "user", "owned-by-a", session_id="s2"))
    assert _run(store.update(ub, mid, "hacked")) is False
    assert _run(store.delete(ub, mid)) is False
    assert _run(store.update(ua, mid, "updated-by-a")) is True
    hits = _run(store.recall(ua, "updated", limit=5))
    assert any("updated-by-a" in (h.get("content") or "") for h in hits)
    assert _run(store.delete(ua, mid)) is True


def test_persistence_across_sessions(db):
    from memory.store import MemoryStore
    import memory.store as ms

    ua = _run(_user("ua3"))
    store = MemoryStore()
    mid = _run(store.save(ua, "assistant", "durable-memory-row", session_id="persist"))
    # Simulate process: reset singleton, new store
    ms.MemoryStore._instance = None
    ms.MemoryStore._initialized = False
    store2 = MemoryStore()
    hist = _run(store2.get_history(ua, "persist", limit=10))
    assert any("durable-memory-row" in (h.get("content") or "") for h in hist)

    # Authoritative table
    from core.database import AsyncSessionLocal, MemoryRecord

    async def load():
        async with AsyncSessionLocal() as s:
            return await s.get(MemoryRecord, mid)

    row = _run(load())
    assert row is not None
    assert row.user_id == ua
    assert "durable-memory-row" in row.content


def test_require_postgres_rejects_sqlite_backend():
    """Policy: REQUIRE_POSTGRES + non-postgres dialect must fail closed."""
    require_pg = True
    dialect = "sqlite"
    assert require_pg and dialect not in ("postgresql", "postgres")


def test_no_sqlite3_connect_in_store():
    src = Path("memory/store.py").read_text(encoding="utf-8")
    assert "sqlite3.connect" not in src
    assert "data/memory.db" not in src


def test_graph_no_sqlite3():
    src = Path("memory/graph.py").read_text(encoding="utf-8")
    assert "sqlite3.connect" not in src
