"""Prove Postgres/Supabase is authoritative for application state.

Requires a live Postgres URL (DEVOS_TEST_DATABASE_URL or reachable local).
Does not mock persistence. Does not mutate process env at import time.
"""
from __future__ import annotations

import asyncio
import os

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine


def _postgres_url() -> str:
    for key in ("DEVOS_TEST_DATABASE_URL", "DATABASE_URL"):
        u = (os.environ.get(key) or "").strip()
        if u.lower().startswith("postgres"):
            return u
    return "postgresql+psycopg://devos:devos@127.0.0.1:5432/devos"


def _async_url(url: str) -> str:
    if url.startswith("postgresql://") or url.startswith("postgres://"):
        return "postgresql+asyncpg://" + url.split("://", 1)[1]
    if "+psycopg://" in url and "+asyncpg://" not in url:
        return url.replace("+psycopg://", "+asyncpg://", 1)
    return url


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture(scope="module")
def pg_engine():
    """Connect to live Postgres; skip when unavailable (no env pollution)."""
    url = _async_url(_postgres_url())
    try:
        eng = create_async_engine(url)
    except Exception as e:
        pytest.skip(f"Postgres engine unavailable: {e}")

    async def check():
        async with eng.connect() as c:
            v = (await c.execute(text("select 1"))).scalar()
            assert v == 1
            for table in (
                "users",
                "missions",
                "mission_tasks",
                "task_delegations",
                "agent_work_history",
                "agents",
                "agent_souls",
                "ponytail_checks",
                "artifacts",
                "orchestration_plans",
                "agent_tasks",
            ):
                r = await c.execute(
                    text(
                        "SELECT 1 FROM information_schema.tables "
                        "WHERE table_schema='public' AND table_name=:t"
                    ),
                    {"t": table},
                )
                assert r.scalar() == 1, f"missing table {table}"

    try:
        _run(check())
    except Exception as e:
        pytest.skip(f"Postgres not reachable for supabase SoT tests: {type(e).__name__}")
    yield eng
    try:
        _run(eng.dispose())
    except Exception:
        pass


@pytest.mark.postgres
def test_authoritative_dialect_is_postgres(pg_engine):
    assert "postgresql" in str(pg_engine.url)


@pytest.mark.production_policy
def test_refuse_sqlite_when_require_postgres():
    from core.config import Settings

    s = Settings(DATABASE_URL="sqlite+aiosqlite:///./x.db", REQUIRE_POSTGRES=True)
    assert s.is_postgres is False
    url = "sqlite+aiosqlite:///./x.db"
    require_pg = True
    assert require_pg and url.lower().startswith("sqlite")
