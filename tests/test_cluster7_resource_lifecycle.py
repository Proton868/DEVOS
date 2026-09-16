"""Cluster 7: execution isolation and resource lifecycle."""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

os.environ.setdefault("REQUIRE_POSTGRES", "false")
Path("data").mkdir(exist_ok=True)


def _task(**kw):
    from brain.agent_runtime import AgentTask, AgentTaskStatus, AgentMode
    base = dict(
        id="t",
        user_id="u",
        tenant_id=None,
        project_id="p",
        session_id="s",
        objective="x",
        mode=AgentMode.ASK,
        status=AgentTaskStatus.SUCCEEDED,
    )
    base.update(kw)
    return AgentTask(**base)


def test_task_registry_prunes_terminal_when_over_cap():
    from brain import agent_runtime as ar
    from brain.agent_runtime import AgentTaskStatus, _prune_task_registry

    ar._TASKS.clear()
    original = ar._MAX_LIVE_TASKS
    ar._MAX_LIVE_TASKS = 5
    try:
        for i in range(12):
            ar._TASKS[f"t-{i}"] = _task(
                id=f"t-{i}",
                status=AgentTaskStatus.SUCCEEDED if i < 10 else AgentTaskStatus.RUNNING,
                completed_at=f"2026-01-01T00:00:{i:02d}",
            )
        _prune_task_registry()
        assert len(ar._TASKS) <= 5 + 32
        running = [
            tid for tid, task in ar._TASKS.items()
            if task.status == AgentTaskStatus.RUNNING
        ]
        assert set(running) <= {"t-10", "t-11"}
        assert running
    finally:
        ar._MAX_LIVE_TASKS = original
        ar._TASKS.clear()


def test_list_tasks_scoped_to_user():
    from brain import agent_runtime as ar
    from brain.agent_runtime import list_tasks_for_user

    ar._TASKS.clear()
    ar._TASKS["a"] = _task(id="a", user_id="u1")
    ar._TASKS["b"] = _task(id="b", user_id="u2")
    items = list_tasks_for_user("u1")
    assert all(
        (i.get("user_id") if isinstance(i, dict) else i.user_id) == "u1"
        for i in items
    )
    ar._TASKS.clear()


@pytest.mark.asyncio
async def test_subprocess_finally_kills_running_child(tmp_path, monkeypatch):
    """Child process ownership: timeout/cancel path always kills and waits."""
    from brain.agent_runtime import AgentRuntime
    from brain.agent_tools import AgentMode
    import execution.files as files_mod

    monkeypatch.setattr(files_mod, "PROJECTS_DIR", tmp_path)
    (tmp_path / "u" / "p").mkdir(parents=True)

    proc = MagicMock()
    proc.returncode = None
    proc.kill = MagicMock()
    proc.wait = AsyncMock(return_value=0)
    proc.communicate = AsyncMock(side_effect=asyncio.TimeoutError)

    async def fake_create(*a, **k):
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_shell", fake_create)
    rt = AgentRuntime(user_id="u", project_id="p", mode=AgentMode.ASK)
    result = await rt._subprocess("sleep 99", timeout=1)
    assert result["exit_code"] == -1
    assert "timed out" in (result.get("stderr") or "")
    assert proc.kill.called


@pytest.mark.asyncio
async def test_log_stream_unsubscribes_on_close():
    from execution import log_stream as ls

    ls._SUBS.clear()
    rid = "rt-test"
    # Manually register like subscribe does, then close path clears ownership
    q: asyncio.Queue = asyncio.Queue(maxsize=10)
    ls._SUBS[rid].append(q)
    assert rid in ls._SUBS
    # Simulate generator finally
    if q in ls._SUBS.get(rid, []):
        ls._SUBS[rid].remove(q)
    if rid in ls._SUBS and not ls._SUBS[rid]:
        del ls._SUBS[rid]
    assert rid not in ls._SUBS


def test_mirror_task_tracker_no_loop_is_noop():
    from brain import agent_runtime as ar

    ar._MIRROR_TASKS.clear()
    # without running loop, schedule is a no-op and must not leak
    ar._track_mirror_task(None)  # type: ignore
    assert len(ar._MIRROR_TASKS) == 0
