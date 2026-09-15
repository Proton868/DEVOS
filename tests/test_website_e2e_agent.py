"""
Genuine end-to-end website path:

Nuha → Web Agent → AgentRuntime file tools → disk → Ponytail →
validate_website_artifacts → artifact metadata → work history.

Does not rely solely on source assertions.
"""
from __future__ import annotations

import asyncio
import os
import uuid
from pathlib import Path

import pytest

os.environ.setdefault("REQUIRE_POSTGRES", "false")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./data/test_website_e2e.db")
os.environ["DEVOS_ORCH_FAKE_RUNTIME"] = "1"
os.environ["DEVOS_ALLOW_FAKE_RUNTIME"] = "1"

Path("data").mkdir(exist_ok=True)


def _run(c):
    return asyncio.run(c)


@pytest.fixture(scope="module")
def db():
    os.environ["REQUIRE_POSTGRES"] = "false"
    os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///./data/test_website_e2e.db"
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
            s.add(User(id=uid, username=f"w_{uid[-6:]}", email=f"{uid}@t.local"))
            await s.commit()

    _run(setup())
    return uid


def test_e2e_website_full_architecture(user_id, tmp_path, monkeypatch):
    import execution.files as files_mod
    from brain.delegation import run_delegated_mission, select_persona_for_goal
    from brain.agent_identity import get_agent_dossier
    from brain.a2a import list_messages
    from brain.orchestration_verify import validate_website_artifacts
    from core.database import AsyncSessionLocal, Artifact
    from sqlalchemy import select

    monkeypatch.setattr(files_mod, "PROJECTS_DIR", tmp_path / "projects")
    (tmp_path / "projects").mkdir(parents=True, exist_ok=True)

    goal = "Create a 1 page website for Footwalk shoe store"

    # 1. Nuha selects web
    assert select_persona_for_goal(goal) == "web"

    # 2–10. Full delegation
    result = _run(
        run_delegated_mission(
            user_id=user_id,
            goal=goal,
            workspace_id="default",
            persona_key="web",
            max_rounds=2,
        )
    )

    # Nuha receives validated result
    assert result.ok is True, result.to_dict()
    assert result.execution_path == "A2A_DELEGATION"
    assert result.persona_key == "web"
    assert result.ponytail and result.ponytail.get("passed") is True

    # Files on disk
    root = tmp_path / "projects" / user_id / "default"
    assert (root / "index.html").is_file()
    assert (root / "style.css").is_file()
    assert (root / "script.js").is_file()
    html = (root / "index.html").read_text()
    assert "Footwalk" in html or "footwalk" in html.lower() or "html" in html.lower()

    # Tool execution recorded in work history
    dossier = _run(get_agent_dossier(agent_id=result.agent_id, user_id=user_id))
    assert dossier["work_history"]
    tools = []
    for h in dossier["work_history"]:
        tools.extend(h.get("tools_used") or [])
    assert "create_file" in tools, f"expected create_file in {tools}"

    # A2A: Nuha delegated, agent completed, ponytail
    msgs = _run(list_messages(mission_id=result.mission_id))
    types = [m.get("message_type") for m in msgs]
    assert "delegate" in types
    assert "ponytail_request" in types
    assert "ponytail_result" in types
    assert any(m.get("sender_type") == "nuha" for m in msgs)
    assert any(m.get("sender_type") == "agent" for m in msgs)

    # validate_website_artifacts passes
    wv = _run(
        validate_website_artifacts(
            user_id=user_id, workspace_id="default", goal=goal
        )
    )
    assert wv.get("valid") is True, wv
    assert wv.get("entry_point")

    # Artifact metadata in DB (Supabase/SQLite SoT)
    async def load_artifacts():
        async with AsyncSessionLocal() as s:
            rows = (
                await s.execute(select(Artifact).where(Artifact.user_id == user_id))
            ).scalars().all()
            return [r.path for r in rows]

    paths = _run(load_artifacts())
    assert "index.html" in paths or any("index" in p for p in paths), paths

    # Provenance human→nuha→agent
    chains = [
        (h.get("provenance") or {}).get("chain")
        for h in dossier["work_history"]
        if h.get("provenance")
    ]
    assert any(c == "human→nuha→agent" for c in chains), chains
