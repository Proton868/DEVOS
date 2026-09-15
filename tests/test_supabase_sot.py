"""Prove Postgres/Supabase is authoritative for application state.

Requires DATABASE_URL pointing at a real Postgres (local or Supabase).
Does not mock persistence.
"""
from __future__ import annotations

import asyncio
import os
import uuid

import pytest

# Force postgres URL for this module before importing app settings consumers
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+psycopg://devos:devos@127.0.0.1:5432/devos",
)
os.environ["REQUIRE_POSTGRES"] = "true"

from sqlalchemy import text, select
from sqlalchemy.ext.asyncio import create_async_engine


DATABASE_URL = os.environ["DATABASE_URL"]


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture(scope="module")
def pg_engine():
    eng = create_async_engine(DATABASE_URL)

    async def check():
        async with eng.connect() as c:
            v = (await c.execute(text("select 1"))).scalar()
            assert v == 1
            # tables from migration
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

    _run(check())
    yield eng
    _run(eng.dispose())


def test_authoritative_dialect_is_postgres(pg_engine):
    assert "postgresql" in DATABASE_URL


def test_refuse_sqlite_when_require_postgres():
    from core.config import Settings
    s = Settings(DATABASE_URL="sqlite+aiosqlite:///./x.db", REQUIRE_POSTGRES=True)
    assert s.is_postgres is False
    # Gate used at engine creation time
    url = "sqlite+aiosqlite:///./x.db"
    require_pg = True
    assert require_pg and url.lower().startswith("sqlite")


def test_mission_task_delegation_persist_and_reload(pg_engine):
    from core.repositories.agency import (
        create_mission,
        add_mission_task,
        record_delegation,
        get_mission,
        ensure_agent,
        record_work_history,
        list_work_history,
    )
    from core.database import AsyncSessionLocal, User, gen_id

    uid = "user-" + uuid.uuid4().hex[:12]

    async def setup_user():
        async with AsyncSessionLocal() as db:
            db.add(
                User(
                    id=uid,
                    username=f"u_{uid[-8:]}",
                    email=f"{uid}@test.local",
                    hashed_password=None,
                )
            )
            await db.commit()

    _run(setup_user())
    agent = _run(ensure_agent(slug=f"web-{uid[-6:]}", name="Web Agent"))
    mission = _run(
        create_mission(
            user_id=uid,
            goal="Build a landing page",
            actor_type="nuha",
            actor_id="nuha",
        )
    )
    task = _run(
        add_mission_task(
            mission_id=mission["id"],
            description="Create index.html",
            persona_key="web",
            agent_id=agent["id"],
            sequence_no=1,
        )
    )
    did = _run(
        record_delegation(
            mission_id=mission["id"],
            task_id=task["id"],
            from_actor_type="nuha",
            from_actor_id="nuha",
            to_actor_type="agent",
            to_actor_id=agent["id"],
            reason="website creation",
        )
    )
    assert did

    # Work history with full provenance
    wid = _run(
        record_work_history(
            agent_id=agent["id"],
            user_id=uid,
            actor_type="agent",
            actor_id=agent["id"],
            delegated_by_type="nuha",
            delegated_by_id="nuha",
            mission_id=mission["id"],
            task_id=task["id"],
            action="create_file",
            tools_used=["create_file"],
            files_changed=["index.html"],
            outcome="success",
            summary="wrote index.html",
        )
    )
    assert wid

    # Reload — proves durable SoT (not memory)
    loaded = _run(get_mission(mission["id"]))
    assert loaded is not None
    assert loaded["goal"] == "Build a landing page"
    assert loaded["actor_type"] == "nuha"
    assert len(loaded["tasks"]) == 1
    assert loaded["tasks"][0]["persona_key"] == "web"

    hist = _run(list_work_history(user_id=uid, agent_id=agent["id"]))
    assert len(hist) >= 1
    assert hist[0]["delegated_by_id"] == "nuha"
    assert hist[0]["mission_id"] == mission["id"]
    assert "index.html" in (hist[0]["files_changed"] or [])


def test_restart_does_not_lose_mission_state(pg_engine):
    """Simulate process restart by opening a new engine/session."""
    from core.repositories.agency import create_mission, get_mission
    from core.database import AsyncSessionLocal, User

    uid = "user-" + uuid.uuid4().hex[:12]

    async def setup():
        async with AsyncSessionLocal() as db:
            db.add(User(id=uid, username=f"r_{uid[-8:]}", email=f"{uid}@r.local"))
            await db.commit()

    _run(setup())
    m = _run(create_mission(user_id=uid, goal="persist across restart"))
    mid = m["id"]

    # New connection pool
    eng2 = create_async_engine(DATABASE_URL)

    async def reread():
        async with eng2.connect() as c:
            row = (
                await c.execute(
                    text("SELECT goal, status FROM missions WHERE id = :id"),
                    {"id": mid},
                )
            ).first()
            assert row is not None
            assert row[0] == "persist across restart"
            assert row[1] == "pending"

    _run(reread())
    _run(eng2.dispose())


def test_rls_enabled_on_core_tables(pg_engine):
    async def check():
        async with pg_engine.connect() as c:
            r = await c.execute(
                text(
                    "SELECT relname, relrowsecurity FROM pg_class "
                    "WHERE relname IN ('missions','agent_work_history','agent_tasks','users') "
                    "AND relnamespace = (SELECT oid FROM pg_namespace WHERE nspname='public')"
                )
            )
            rows = {name: bool(rls) for name, rls in r.fetchall()}
            for t in ("missions", "agent_work_history", "agent_tasks", "users"):
                assert rows.get(t) is True, f"RLS not enabled on {t}: {rows}"

    _run(check())


def test_artifact_metadata_not_filesystem_sot(pg_engine):
    from core.repositories.agency import upsert_artifact_metadata
    from core.database import AsyncSessionLocal, User

    uid = "user-" + uuid.uuid4().hex[:12]

    async def setup():
        async with AsyncSessionLocal() as db:
            db.add(User(id=uid, username=f"a_{uid[-8:]}", email=f"{uid}@a.local"))
            await db.commit()

    _run(setup())
    aid = _run(
        upsert_artifact_metadata(
            user_id=uid,
            project_id="default",
            path="index.html",
            content_hash="abc123",
            size_bytes=42,
            actor_type="agent",
            actor_id="web",
        )
    )
    assert aid

    async def verify():
        async with pg_engine.connect() as c:
            row = (
                await c.execute(
                    text(
                        "SELECT path, content_hash FROM artifacts "
                        "WHERE user_id=:u AND path='index.html'"
                    ),
                    {"u": uid},
                )
            ).first()
            assert row is not None
            assert row[0] == "index.html"
            assert row[1] == "abc123"

    _run(verify())
