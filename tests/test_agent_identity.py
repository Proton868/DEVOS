"""Persistent agent identity, provenance, and work history."""
from __future__ import annotations

import asyncio
import os
import uuid
from pathlib import Path

import pytest

os.environ.setdefault("REQUIRE_POSTGRES", "false")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./data/test_identity.db")
os.environ["DEVOS_ORCH_FAKE_RUNTIME"] = "1"
os.environ["DEVOS_ALLOW_FAKE_RUNTIME"] = "1"

Path("data").mkdir(exist_ok=True)


def _run(c):
    return asyncio.run(c)


@pytest.fixture(scope="module")
def db():
    os.environ["REQUIRE_POSTGRES"] = "false"
    os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///./data/test_identity.db"
    try:
        from core.database import init_db

        _run(init_db())
    except Exception as e:
        pytest.skip(f"db: {e}")
    yield


@pytest.fixture
def user_id(db):
    from core.database import AsyncSessionLocal, User, gen_id

    uid = "u-" + uuid.uuid4().hex[:10]

    async def setup():
        async with AsyncSessionLocal() as s:
            s.add(User(id=uid, username=f"n_{uid[-6:]}", email=f"{uid}@t.local"))
            await s.commit()

    _run(setup())
    return uid


def test_provenance_chains():
    from brain.agent_identity import Provenance

    h = Provenance.human_to_agent("user1", "agent:web")
    assert h.chain == "human→agent"
    assert h.delegated_by_type is None

    n = Provenance.human_via_nuha("user1", "agent:web")
    assert n.chain == "human→nuha→agent"
    assert n.delegated_by_id == "nuha"

    a = Provenance.agent_to_agent("agent:code", "agent:web")
    assert a.chain == "agent→agent"
    assert a.delegated_by_type == "agent"


def test_ensure_identity_creates_profile_and_empty_soul(user_id):
    from brain.agent_identity import ensure_agent_identity, get_agent_dossier

    ident = _run(ensure_agent_identity(persona_key="web", user_id=user_id))
    assert ident["agent_id"]
    assert "fs.write" in (ident.get("capabilities") or []) or ident.get("capabilities") is not None

    dossier = _run(get_agent_dossier(agent_id=ident["agent_id"], user_id=user_id))
    assert dossier["identity"]["immutable"] is True
    assert dossier["soul"]["lessons"] == []
    assert dossier["soul"]["preferences"] == {}
    assert dossier["profile"]["stats"].get("tasks_completed", 0) == 0


def test_soul_does_not_accept_arbitrary_task_text(user_id):
    from brain.agent_identity import ensure_agent_identity, append_validated_lesson, get_agent_dossier

    ident = _run(ensure_agent_identity(persona_key="code", user_id=user_id))
    # Reject: wrong source event
    ok = _run(
        append_validated_lesson(
            agent_id=ident["agent_id"],
            user_id=user_id,
            lesson="This is a long enough fabricated memory from chat",
            source_event="random_chat",
        )
    )
    assert ok is False
    # Reject: too short
    ok = _run(
        append_validated_lesson(
            agent_id=ident["agent_id"],
            user_id=user_id,
            lesson="short",
            source_event="task_completed",
        )
    )
    assert ok is False
    dossier = _run(get_agent_dossier(agent_id=ident["agent_id"], user_id=user_id))
    assert dossier["soul"]["lessons"] == []


def test_validated_lesson_only_from_allowed_sources(user_id):
    from brain.agent_identity import (
        ensure_agent_identity,
        append_validated_lesson,
        get_agent_dossier,
        EVENT_PONYTAIL_VALIDATION,
    )

    ident = _run(ensure_agent_identity(persona_key="web", user_id=user_id))
    ok = _run(
        append_validated_lesson(
            agent_id=ident["agent_id"],
            user_id=user_id,
            lesson="Prefer relative CSS paths for static sites",
            source_event=EVENT_PONYTAIL_VALIDATION,
            mission_id="m1",
        )
    )
    assert ok is True
    dossier = _run(get_agent_dossier(agent_id=ident["agent_id"], user_id=user_id))
    assert len(dossier["soul"]["lessons"]) == 1
    assert "relative CSS" in dossier["soul"]["lessons"][0]["text"]


def test_work_history_records_nuha_provenance(user_id, tmp_path, monkeypatch):
    from brain.delegation import run_delegated_mission
    from brain.agent_identity import get_agent_dossier
    import execution.files as files_mod

    monkeypatch.setattr(files_mod, "PROJECTS_DIR", tmp_path / "projects")
    (tmp_path / "projects").mkdir(parents=True, exist_ok=True)

    result = _run(
        run_delegated_mission(
            user_id=user_id,
            goal="Create a 1 page website for identity test brand",
            max_rounds=2,
        )
    )
    assert result.ok is True, result.to_dict()
    dossier = _run(get_agent_dossier(agent_id=result.agent_id, user_id=user_id))
    assert dossier["work_history"], "work history must persist"
    # Provenance human→nuha→agent somewhere
    chains = [
        (h.get("provenance") or {}).get("chain")
        for h in dossier["work_history"]
        if h.get("provenance")
    ]
    assert any(c == "human→nuha→agent" for c in chains), chains
    # Events include assignment and completion
    types = [e["event_type"] for e in dossier["events"]]
    assert "task_assigned" in types or any("task" in (t or "") for t in types)
    assert "task_completed" in types or "ponytail_validation" in types
    # Profile stats bumped
    assert int(dossier["profile"]["stats"].get("tasks_completed") or 0) >= 1


def test_dossier_separates_identity_layers(user_id):
    from brain.agent_identity import ensure_agent_identity, get_agent_dossier

    ident = _run(ensure_agent_identity(persona_key="writer", user_id=user_id))
    d = _run(get_agent_dossier(agent_id=ident["agent_id"], user_id=user_id))
    assert "identity" in d and "profile" in d and "soul" in d
    assert "work_history" in d and "events" in d
    assert d["identity"]["immutable"] is True
    # soul note present
    assert "validated" in (d["soul"].get("note") or "").lower()
