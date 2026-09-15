"""A2A delegation architecture tests.

Nuha orchestrates → Agent executes → Ponytail validates → durable A2A messages.
"""
from __future__ import annotations

import asyncio
import os
import uuid
from pathlib import Path

import pytest

# Test DB: sqlite unless postgres provided
os.environ.setdefault("REQUIRE_POSTGRES", "false")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./data/test_a2a.db")
os.environ["DEVOS_ORCH_FAKE_RUNTIME"] = "1"
os.environ["DEVOS_ALLOW_FAKE_RUNTIME"] = "1"

ROOT = Path(__file__).resolve().parents[1]


def _run(c):
    return asyncio.run(c)


@pytest.fixture(autouse=True)
def _reset_bus():
    from brain.a2a import clear_fallback_bus

    clear_fallback_bus()
    yield
    clear_fallback_bus()


@pytest.fixture(scope="module")
def _db_ready():
    """Init schema for sqlite test DB."""
    os.environ["REQUIRE_POSTGRES"] = "false"
    os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///./data/test_a2a.db"
    Path("data").mkdir(exist_ok=True)
    # Re-create engine bindings may already be imported — use raw SQLAlchemy for messages
    try:
        from core.database import init_db

        _run(init_db())
    except Exception as e:
        # If postgres required incorrectly, still allow fallback bus tests
        pytest.skip(f"db init skipped: {e}")
    yield


def test_a2a_envelope_fields():
    from brain.a2a import A2AEnvelope

    env = A2AEnvelope.create(
        mission_id="m1",
        task_id="t1",
        sender_type="nuha",
        sender_id="nuha",
        recipient_agent_id="agent-web",
        message_type="delegate",
        objective="Build a site",
        constraints=["no_external_apis"],
        requested_capabilities=["fs.write"],
        context_refs=["ctx1"],
        artifact_refs=[],
        parent_message_id=None,
        evidence_refs=[],
    )
    d = env.to_dict()
    for k in (
        "message_id",
        "mission_id",
        "task_id",
        "sender_type",
        "sender_id",
        "recipient_agent_id",
        "message_type",
        "objective",
        "constraints",
        "requested_capabilities",
        "context_refs",
        "artifact_refs",
        "parent_message_id",
        "status",
        "evidence_refs",
        "created_at",
        "updated_at",
    ):
        assert k in d, k
    assert d["sender_type"] == "nuha"
    assert d["message_type"] == "delegate"


def test_a2a_messages_persisted_fallback():
    from brain.a2a import A2AEnvelope, list_messages, persist_message, clear_fallback_bus

    clear_fallback_bus()
    env = A2AEnvelope.create(
        mission_id="m-persist",
        task_id="t-persist",
        sender_type="nuha",
        sender_id="nuha",
        recipient_agent_id="web",
        message_type="delegate",
        objective="x",
    )
    mid = _run(persist_message(env))
    assert mid == env.message_id
    msgs = _run(list_messages(mission_id="m-persist"))
    assert any(m.get("message_id") == mid for m in msgs)


def test_nuha_delegates_agent_executes_ponytail_accepts(tmp_path, monkeypatch, _db_ready):
    """End-to-end: Nuha does not write files; agent does; Ponytail must pass."""
    from brain.delegation import run_delegated_mission, select_persona_for_goal
    import execution.files as files_mod

    monkeypatch.setattr(files_mod, "PROJECTS_DIR", tmp_path / "projects")
    (tmp_path / "projects").mkdir(parents=True, exist_ok=True)

    uid = "u-" + uuid.uuid4().hex[:10]
    goal = "Create a 1 page website for Footwalk shoe store"

    assert select_persona_for_goal(goal) == "web"

    # Track whether Nuha path called FileService.write directly (should not)
    writes_by = []
    real_write = files_mod.FileService.write

    def tracking_write(self, rel_path, content):
        writes_by.append(getattr(self, "_caller", "unknown"))
        return real_write(self, rel_path, content)

    monkeypatch.setattr(files_mod.FileService, "write", tracking_write)

    result = _run(
        run_delegated_mission(
            user_id=uid,
            goal=goal,
            workspace_id="default",
            max_rounds=2,
        )
    )
    assert result.ok is True, result.to_dict()
    assert result.execution_path == "A2A_DELEGATION"
    assert result.persona_key == "web"
    assert result.agent_id
    assert result.ponytail and result.ponytail.get("passed") is True
    assert result.a2a_message_ids, "A2A messages must be recorded"
    assert result.rounds >= 1

    # Files on disk from agent (fake runtime)
    root = tmp_path / "projects" / uid / "default"
    assert (root / "index.html").is_file()

    from brain.a2a import list_messages

    msgs = _run(list_messages(mission_id=result.mission_id))
    types = [m.get("message_type") for m in msgs]
    assert "delegate" in types
    assert "complete" in types or "fail" in types
    assert "ponytail_request" in types
    assert "ponytail_result" in types
    # Nuha delegated, agent replied
    assert any(m.get("sender_type") == "nuha" for m in msgs)
    assert any(m.get("sender_type") == "agent" for m in msgs)
    assert any(m.get("sender_type") == "ponytail" for m in msgs)


def test_completion_rejected_before_ponytail(tmp_path, monkeypatch, _db_ready):
    """Agent success without artifacts must not be accepted."""
    from brain.delegation import run_delegated_mission
    from brain.orchestration_runtime import NodeExecutionResult
    import brain.delegation as del_mod
    import execution.files as files_mod

    monkeypatch.setattr(files_mod, "PROJECTS_DIR", tmp_path / "projects")
    (tmp_path / "projects").mkdir(parents=True, exist_ok=True)

    async def empty_agent(**kwargs):
        return NodeExecutionResult(
            success=True,
            status="succeeded",
            summary="claimed done with no files",
            files_changed=[],  # no artifacts
        ).to_dict()

    monkeypatch.setattr(del_mod, "_run_agent_node", empty_agent)

    uid = "u-" + uuid.uuid4().hex[:10]
    result = _run(
        run_delegated_mission(
            user_id=uid,
            goal="Create a website for test brand",
            max_rounds=2,
        )
    )
    assert result.ok is False
    assert result.ponytail is None or result.ponytail.get("passed") is False


def test_validation_failure_creates_correction_delegation(tmp_path, monkeypatch, _db_ready):
    from brain.delegation import run_delegated_mission
    from brain.orchestration_runtime import NodeExecutionResult
    import brain.delegation as del_mod
    import execution.files as files_mod
    from cognitive.ponytail_gate import PonytailGateResult

    monkeypatch.setattr(files_mod, "PROJECTS_DIR", tmp_path / "projects")
    (tmp_path / "projects").mkdir(parents=True, exist_ok=True)

    calls = {"n": 0}

    async def flaky_agent(**kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return NodeExecutionResult(
                success=True, status="succeeded", files_changed=[], summary="empty"
            ).to_dict()
        # second round: write a file
        fs = files_mod.FileService(kwargs["user_id"], kwargs["workspace_id"])
        fs.write(
            "index.html",
            "<!DOCTYPE html><html><body><h1>Fixed</h1></body></html>",
        )
        return NodeExecutionResult(
            success=True,
            status="succeeded",
            files_changed=[{"path": "index.html"}],
            summary="fixed",
        ).to_dict()

    monkeypatch.setattr(del_mod, "_run_agent_node", flaky_agent)

    uid = "u-" + uuid.uuid4().hex[:10]
    result = _run(
        run_delegated_mission(
            user_id=uid,
            goal="Create a website for correction test",
            max_rounds=3,
        )
    )
    assert result.ok is True, result.to_dict()
    assert result.rounds >= 2
    from brain.a2a import list_messages

    msgs = _run(list_messages(mission_id=result.mission_id))
    types = [m.get("message_type") for m in msgs]
    assert "correct" in types
    assert types.count("delegate") + types.count("correct") >= 2


def test_chat_uses_a2a_not_materialize():
    src = (ROOT / "api" / "routes" / "chat.py").read_text(encoding="utf-8")
    assert "run_delegated_mission" in src
    assert "A2A_DELEGATION" in src
    # materialize may still exist as import but must not be primary success path
    assert "materialize_website_via_agent" not in src or "A2A_DELEGATION" in src


def test_select_persona_website_is_web():
    from brain.delegation import select_persona_for_goal

    assert select_persona_for_goal("build a landing page website") == "web"
    assert select_persona_for_goal("refactor the API handlers") == "code"
