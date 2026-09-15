"""Ponytail is mandatory: broken agent output must never become accepted Nuha output."""
from __future__ import annotations

import asyncio
import os
import uuid
from pathlib import Path

import pytest

os.environ.setdefault("REQUIRE_POSTGRES", "false")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./data/test_ponytail.db")
os.environ["DEVOS_ORCH_FAKE_RUNTIME"] = "1"
os.environ["DEVOS_ALLOW_FAKE_RUNTIME"] = "1"

Path("data").mkdir(exist_ok=True)


def _run(c):
    return asyncio.run(c)


@pytest.fixture(scope="module")
def db():
    os.environ["REQUIRE_POSTGRES"] = "false"
    os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///./data/test_ponytail.db"
    try:
        from core.database import init_db

        _run(init_db())
    except Exception as e:
        pytest.skip(str(e))
    yield


@pytest.fixture
def user_id(db):
    from core.database import AsyncSessionLocal, User

    uid = "u-" + uuid.uuid4().hex[:10]

    async def setup():
        async with AsyncSessionLocal() as s:
            s.add(User(id=uid, username=f"p_{uid[-6:]}", email=f"{uid}@t.local"))
            await s.commit()

    _run(setup())
    return uid


def test_gate_result_has_required_fields(user_id, tmp_path, monkeypatch):
    import execution.files as files_mod
    from cognitive.ponytail_gate import validate_agent_artifacts

    monkeypatch.setattr(files_mod, "PROJECTS_DIR", tmp_path / "projects")
    (tmp_path / "projects").mkdir(parents=True, exist_ok=True)
    fs = files_mod.FileService(user_id, "default")
    fs.write("index.html", "<!DOCTYPE html><html><body><h1>Ok</h1></body></html>")

    gate = _run(
        validate_agent_artifacts(
            user_id=user_id,
            goal="Create a website page",
            files_changed=[{"path": "index.html"}],
            agent_id="agent:web",
            mission_id="m1",
            task_id="t1",
            force_code_gate=True,
        )
    )
    d = gate.to_dict()
    for k in (
        "check_id",
        "status",
        "failures",
        "warnings",
        "evidence",
        "files_checked",
        "agent_id",
        "task_id",
        "timestamp",
    ):
        assert k in d
    assert gate.passed is True


def test_intentional_broken_python_fails_gate(user_id, tmp_path, monkeypatch):
    import execution.files as files_mod
    from cognitive.ponytail_gate import validate_agent_artifacts, assert_accepted

    monkeypatch.setattr(files_mod, "PROJECTS_DIR", tmp_path / "projects")
    (tmp_path / "projects").mkdir(parents=True, exist_ok=True)
    fs = files_mod.FileService(user_id, "default")
    fs.write(
        "broken.py",
        "def boom(\n  # PONYTAIL_FAIL intentional syntax error\n  return 1\n",
    )

    gate = _run(
        validate_agent_artifacts(
            user_id=user_id,
            goal="add helper",
            files_changed=[{"path": "broken.py"}],
            agent_id="agent:code",
            force_code_gate=True,
        )
    )
    assert gate.passed is False
    assert gate.failures
    with pytest.raises(PermissionError):
        assert_accepted(gate)


def test_intentional_break_marker_in_html_fails(user_id, tmp_path, monkeypatch):
    import execution.files as files_mod
    from cognitive.ponytail_gate import validate_agent_artifacts

    monkeypatch.setattr(files_mod, "PROJECTS_DIR", tmp_path / "projects")
    (tmp_path / "projects").mkdir(parents=True, exist_ok=True)
    fs = files_mod.FileService(user_id, "default")
    fs.write(
        "index.html",
        "<!DOCTYPE html><html><body>INTENTIONAL_BREAK broken site</body></html>",
    )
    gate = _run(
        validate_agent_artifacts(
            user_id=user_id,
            goal="Create a website",
            files_changed=["index.html"],
            force_code_gate=True,
        )
    )
    assert gate.passed is False
    assert any("intentional" in f.lower() or "INTENTIONAL" in f for f in gate.failures)


def test_e2e_broken_agent_change_not_accepted_by_nuha(user_id, tmp_path, monkeypatch):
    """Agent claims success with broken code → Ponytail fails → DelegationResult.ok is False."""
    from brain.delegation import run_delegated_mission
    from brain.orchestration_runtime import NodeExecutionResult
    import brain.delegation as del_mod
    import execution.files as files_mod

    monkeypatch.setattr(files_mod, "PROJECTS_DIR", tmp_path / "projects")
    (tmp_path / "projects").mkdir(parents=True, exist_ok=True)

    async def broken_agent(**kwargs):
        fs = files_mod.FileService(kwargs["user_id"], kwargs["workspace_id"])
        fs.write(
            "app.py",
            "def broken(\nPONYTAIL_FAIL\n",
        )
        return NodeExecutionResult(
            success=True,  # agent claims done
            status="succeeded",
            summary="done",
            files_changed=[{"path": "app.py"}],
        ).to_dict()

    monkeypatch.setattr(del_mod, "_run_agent_node", broken_agent)

    result = _run(
        run_delegated_mission(
            user_id=user_id,
            goal="Add a Python helper module",
            persona_key="code",
            max_rounds=1,  # single attempt — must not accept
        )
    )
    assert result.ok is False, "broken code must not become accepted Nuha output"
    assert result.ponytail is None or result.ponytail.get("passed") is False
    assert result.status in ("failed", "unknown") or result.error


def test_e2e_good_agent_passes_after_ponytail(user_id, tmp_path, monkeypatch):
    from brain.delegation import run_delegated_mission
    from brain.orchestration_runtime import NodeExecutionResult
    import brain.delegation as del_mod
    import execution.files as files_mod

    monkeypatch.setattr(files_mod, "PROJECTS_DIR", tmp_path / "projects")
    (tmp_path / "projects").mkdir(parents=True, exist_ok=True)

    async def good_agent(**kwargs):
        fs = files_mod.FileService(kwargs["user_id"], kwargs["workspace_id"])
        fs.write(
            "index.html",
            "<!DOCTYPE html><html><head><title>Ok</title></head>"
            "<body><h1>Footwalk</h1></body></html>",
        )
        return NodeExecutionResult(
            success=True,
            status="succeeded",
            files_changed=[{"path": "index.html"}],
            summary="ok",
        ).to_dict()

    monkeypatch.setattr(del_mod, "_run_agent_node", good_agent)
    result = _run(
        run_delegated_mission(
            user_id=user_id,
            goal="Create a website landing page",
            persona_key="web",
            max_rounds=1,
        )
    )
    assert result.ok is True
    assert result.ponytail and result.ponytail.get("passed") is True
    assert result.ponytail.get("check_id")


def test_document_path_not_fake_code_review(user_id, tmp_path, monkeypatch):
    import execution.files as files_mod
    from cognitive.ponytail_gate import validate_agent_artifacts

    monkeypatch.setattr(files_mod, "PROJECTS_DIR", tmp_path / "projects")
    (tmp_path / "projects").mkdir(parents=True, exist_ok=True)
    fs = files_mod.FileService(user_id, "default")
    fs.write("notes.md", "# Research notes\n\nFindings about the market.\n")

    gate = _run(
        validate_agent_artifacts(
            user_id=user_id,
            goal="summarize research",
            files_changed=["notes.md"],
            agent_id="agent:research",
        )
    )
    assert gate.artifact_kind == "document"
    assert gate.passed is True
    assert gate.applicable is True


def test_empty_claim_fails():
    from cognitive.ponytail_gate import validate_agent_artifacts

    gate = _run(
        validate_agent_artifacts(
            user_id="nobody",
            goal="do something",
            files_changed=[],
            force_code_gate=True,
        )
    )
    assert gate.passed is False
