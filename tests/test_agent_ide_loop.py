"""Stage 3N — durable agentic IDE loop invariants + acceptance."""
from __future__ import annotations
import asyncio
import pytest

from brain.agent_runtime import (
    AgentTask, AgentMode, _emit, get_task_events,
    request_cancel, _TASKS, _EVENT_SEQ, _TASK_EVENTS,
)
from brain.agent_runtime import _select_related_tests


def test_event_sequence_monotonic():
    task = AgentTask(
        id="t-seq-1", user_id="u1", tenant_id="ten", project_id="p",
        session_id="s", objective="x", mode=AgentMode.AGENT,
    )
    _TASKS[task.id] = task
    _EVENT_SEQ[task.id] = 0
    _TASK_EVENTS[task.id] = []
    e1 = _emit(task, "agent.started", {})
    e2 = _emit(task, "agent.tool_call", {"tool": "read_file"})
    e3 = _emit(task, "agent.completed", {})
    assert e1["seq"] == 1
    assert e2["seq"] == 2
    assert e3["seq"] == 3
    assert e1["task_id"] == task.id
    missed = get_task_events(task.id, after_seq=1)
    assert len(missed) == 2
    assert missed[0]["seq"] == 2


def test_cancel_sets_flag():
    task = AgentTask(
        id="t-cancel-1", user_id="u1", tenant_id="ten", project_id="p",
        session_id="s", objective="x", mode=AgentMode.AGENT,
    )
    _TASKS[task.id] = task
    assert request_cancel(task.id) is True
    assert task.cancel_requested is True


def test_task_isolation_events():
    a = AgentTask(id="ta", user_id="u1", tenant_id="ten", project_id="p",
                  session_id="s", objective="a", mode=AgentMode.AGENT)
    b = AgentTask(id="tb", user_id="u1", tenant_id="ten", project_id="p",
                  session_id="s", objective="b", mode=AgentMode.AGENT)
    _TASKS[a.id] = a
    _TASKS[b.id] = b
    _EVENT_SEQ[a.id] = 0
    _EVENT_SEQ[b.id] = 0
    _TASK_EVENTS[a.id] = []
    _TASK_EVENTS[b.id] = []
    _emit(a, "agent.started", {})
    _emit(b, "agent.started", {})
    _emit(a, "agent.tool_call", {"tool": "write_file"})
    ea = get_task_events(a.id)
    eb = get_task_events(b.id)
    assert all(e["task_id"] == "ta" for e in ea)
    assert all(e["task_id"] == "tb" for e in eb)
    assert len(ea) == 2
    assert len(eb) == 1


def test_select_related_tests_bounded():
    out = _select_related_tests(["src/auth/login.py", "src/auth/session.py"], limit=5)
    assert len(out) <= 5
    assert any("test_" in x or "_test" in x for x in out)


def test_append_event_dedupes_seq(tmp_path, monkeypatch):
    try:
        import sqlalchemy  # noqa: F401
    except ImportError:
        pytest.skip("sqlalchemy not installed")

    async def _run():
        from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
        from core import database as dbmod
        db_url = f"sqlite+aiosqlite:///{tmp_path}/evt.db"
        monkeypatch.setenv("DATABASE_URL", db_url)
        dbmod.engine = create_async_engine(db_url, echo=False)
        dbmod.AsyncSessionLocal = async_sessionmaker(dbmod.engine, expire_on_commit=False)
        await dbmod.init_db()
        from core.database import AgentTaskRecord
        from brain.agent_task_store import append_event, load_task_events
        async with dbmod.AsyncSessionLocal() as db:
            db.add(AgentTaskRecord(
                id="dur-1", user_id="u1", tenant_id="t", project_id="p",
                session_id="s", objective="obj", mode="agent", status="running",
                events=[],
            ))
            await db.commit()
        evt = {"type": "agent.started", "task_id": "dur-1", "seq": 1, "data": {}}
        assert await append_event("dur-1", evt) is True
        assert await append_event("dur-1", evt) is True
        events = await load_task_events("dur-1", 0)
        assert sum(1 for e in events if e.get("seq") == 1) == 1

    asyncio.run(_run())


def test_durable_cancel(tmp_path, monkeypatch):
    try:
        import sqlalchemy  # noqa: F401
    except ImportError:
        pytest.skip("sqlalchemy not installed")

    async def _run():
        from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
        from core import database as dbmod
        db_url = f"sqlite+aiosqlite:///{tmp_path}/can.db"
        monkeypatch.setenv("DATABASE_URL", db_url)
        dbmod.engine = create_async_engine(db_url, echo=False)
        dbmod.AsyncSessionLocal = async_sessionmaker(dbmod.engine, expire_on_commit=False)
        await dbmod.init_db()
        from core.database import AgentTaskRecord
        from brain.agent_task_store import mark_task_cancelled
        async with dbmod.AsyncSessionLocal() as db:
            db.add(AgentTaskRecord(
                id="can-1", user_id="u1", tenant_id="t", project_id="p",
                session_id="s", objective="obj", mode="agent", status="running",
                events=[],
            ))
            await db.commit()
        r = await mark_task_cancelled("can-1", "other")
        assert r.get("ok") is False
        r = await mark_task_cancelled("can-1", "u1")
        assert r.get("ok") is True
        assert r.get("status") == "cancelled"
        r2 = await mark_task_cancelled("can-1", "u1")
        assert r2.get("already_terminal") is True

    asyncio.run(_run())
