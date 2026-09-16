"""ObservabilityStore canonical contract — Postgres/SQLite isolated tests."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("REQUIRE_POSTGRES", "false")
Path("data").mkdir(exist_ok=True)


@pytest.fixture()
def iso(tmp_path, monkeypatch):
    url = f"sqlite+aiosqlite:///{tmp_path}/obs.db"
    monkeypatch.setenv("REQUIRE_POSTGRES", "false")
    monkeypatch.setenv("DATABASE_URL", url)
    from core.config import settings
    from core.sync_session import dispose_sync_engine
    from governance.observability import ObservabilityStore

    monkeypatch.setattr(settings, "DATABASE_URL", url)
    monkeypatch.setattr(settings, "REQUIRE_POSTGRES", False)
    dispose_sync_engine()
    ObservabilityStore._instance = None
    yield ObservabilityStore()
    ObservabilityStore._instance = None
    dispose_sync_engine()


def test_record_and_list_errors(iso):
    store = iso
    entry = store.record_error(
        "builder", "build failed", trace_id="trace-1", user_id="u-1"
    )
    assert entry["component"] == "builder"
    assert entry["message"] == "build failed"
    errors = store.list_errors(limit=5)
    assert any(item["trace_id"] == "trace-1" for item in errors)
    scoped = store.list_errors(user_id="u-1")
    assert all(e["user_id"] == "u-1" for e in scoped)


def test_trace_lifecycle_and_tool_call(iso):
    store = iso
    store.start_trace("task-1", "agent-web", "sess-1", "build site", "omniroute", "default")
    store.record_tool_call(
        "task-1", "agent-web", "sess-1", "create_file",
        {"path": "index.html"}, "ok", "success", "allow", 12.0, iteration=1,
    )
    store.finish_trace(
        "task-1", status="complete", decision="COMPLETE",
        iterations=1, total_tokens=10, total_latency_ms=50, tool_calls=1,
        final_answer="done",
    )
    traces = store.list_traces(limit=10)
    assert any(t["trace_id"] == "task-1" for t in traces)
    replay = store.replay_trace("task-1")
    assert replay["trace"]["status"] == "complete"
    assert isinstance(replay["spans"], list)
    m = store.metrics()
    assert m["trace_count"] >= 1
    assert "backend" in m


def test_not_authority_backend_property(iso):
    assert isinstance(iso.backend, str)
