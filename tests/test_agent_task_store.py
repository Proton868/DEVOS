"""Tests for durable AgentTask store and event sequencing."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from brain.agent_tools import AgentMode
from brain.agent_runtime import (
    AgentTask,
    AgentTaskStatus,
    _emit,
    get_task_events,
    request_cancel,
    _TASKS,
    _TASK_EVENTS,
    _CANCEL_FLAGS,
    _EVENT_SEQ,
)


def _fresh_task(tid="task-test-1"):
    t = AgentTask(
        id=tid,
        user_id="user-1",
        tenant_id="tenant-1",
        project_id="default",
        session_id="sess-1",
        objective="fix something",
        mode=AgentMode.AGENT,
        status=AgentTaskStatus.RUNNING,
        started_at="2026-01-01T00:00:00+00:00",
    )
    _TASKS[tid] = t
    _TASK_EVENTS[tid] = []
    _EVENT_SEQ[tid] = 0
    _CANCEL_FLAGS[tid] = asyncio.Event()
    return t


def test_emit_assigns_monotonic_seq():
    t = _fresh_task("seq-1")
    e1 = _emit(t, "agent.started", {"objective": "x"})
    e2 = _emit(t, "agent.thinking", {"step": 1})
    e3 = _emit(t, "agent.tool_call", {"tool": "list_files"})
    assert e1["seq"] == 1
    assert e2["seq"] == 2
    assert e3["seq"] == 3
    assert e1["task_id"] == "seq-1"
    assert e1["correlation_id"] == t.correlation_id


def test_get_task_events_after_seq():
    t = _fresh_task("seq-2")
    _emit(t, "agent.started", {})
    _emit(t, "agent.thinking", {})
    _emit(t, "agent.completed", {})
    all_ev = get_task_events("seq-2", 0)
    assert len(all_ev) == 3
    after = get_task_events("seq-2", 1)
    assert len(after) == 2
    assert after[0]["seq"] == 2


def test_cancel_sets_flag():
    t = _fresh_task("cancel-1")
    assert request_cancel("cancel-1") is True
    assert t.cancel_requested is True
    assert _CANCEL_FLAGS["cancel-1"].is_set()
    assert request_cancel("missing") is False


def test_no_cross_user_leak_in_memory_list():
    from brain.agent_runtime import list_tasks_for_user
    _fresh_task("u1-a")
    t2 = _fresh_task("u2-a")
    t2.user_id = "other-user"
    mine = list_tasks_for_user("user-1")
    assert all(x["user_id"] == "user-1" for x in mine)
    assert not any(x["id"] == "u2-a" for x in mine)


def test_agent_task_to_dict_shape():
    t = _fresh_task("dict-1")
    d = t.to_dict()
    for key in ("id", "user_id", "project_id", "objective", "status", "correlation_id", "mode"):
        assert key in d
