"""Cluster 11: end-to-end architecture chain regression tests.

Exercises real modules on the intended call path (not production VPS).
Live Postgres / OmniRoute / systemd remain UNVERIFIED here.
"""
from __future__ import annotations

import ast
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parents[1]


# --- Static caller-path contracts ---

def test_chat_send_requires_get_current_user():
    src = (ROOT / "api" / "routes" / "chat.py").read_text()
    tree = ast.parse(src)
    found_send = False
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "send":
            found_send = True
            body = ast.unparse(node) if hasattr(ast, "unparse") else src
            assert "get_current_user" in body or any(
                isinstance(n, ast.Call)
                and getattr(getattr(n.func, "id", None), "lower", lambda: "")() == "get_current_user"
                for n in ast.walk(node)
            )
            # Identity comes from server session, not ChatReq.user_id
            assert "user_id" not in (ROOT / "api" / "routes" / "chat.py").read_text().split("class ChatReq")[1].split("@router")[0]
    assert found_send


def test_chat_does_not_call_execute_plan():
    src = (ROOT / "api" / "routes" / "chat.py").read_text()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = ""
            if isinstance(node.func, ast.Name):
                name = node.func.id
            elif isinstance(node.func, ast.Attribute):
                name = node.func.attr
            assert name != "execute_plan"


def test_chat_uses_delegation_and_acceptance():
    src = (ROOT / "api" / "routes" / "chat.py").read_text()
    assert "run_delegated_mission" in src
    assert "evaluate_mission_acceptance" in src
    assert "mission_truth" in src


# --- Governance chain ---

def test_provider_response_cannot_grant_acceptance():
    from brain.mission_acceptance import evaluate_mission_acceptance
    from brain.nuha_bridge import mission_truth

    # LLM-ish success with no gate/evidence
    acc = evaluate_mission_acceptance(
        execution_ok=True,
        status="succeeded",
        files_changed=[{"path": "index.html"}],
        ponytail=None,
        evidence_refs=[],
    )
    assert acc["ok"] is False
    t = mission_truth("succeeded", explicit_ok=True, acceptance=acc)
    assert t["ok"] is False


def test_explicit_ok_without_acceptance_insufficient():
    from brain.nuha_bridge import mission_truth
    t = mission_truth("running", explicit_ok=True)
    assert t["ok"] is False


def test_full_acceptance_chain_success():
    from brain.mission_acceptance import evaluate_mission_acceptance
    from brain.nuha_bridge import mission_truth

    acc = evaluate_mission_acceptance(
        execution_ok=True,
        status="accepted",
        files_changed=[{"path": "index.html"}],
        ponytail={"passed": True, "applicable": True, "evidence_id": "ev1"},
        evidence_refs=["ev1"],
        user_id="u1",
        expected_user_id="u1",
        mission_id="m1",
        expected_mission_id="m1",
    )
    assert acc["ok"] is True
    assert mission_truth("accepted", acceptance=acc)["ok"] is True


def test_cross_owner_acceptance_fails():
    from brain.mission_acceptance import evaluate_mission_acceptance
    acc = evaluate_mission_acceptance(
        execution_ok=True,
        status="accepted",
        files_changed=[{"path": "x.html"}],
        ponytail={"passed": True, "applicable": True, "evidence_id": "e"},
        evidence_refs=["e"],
        user_id="alice",
        expected_user_id="bob",
        mission_id="m1",
        expected_mission_id="m1",
    )
    assert acc["ok"] is False
    assert acc["reason"] == "owner_mismatch"


# --- AuthN ≠ AuthZ / path ownership ---

def test_workspace_scope_not_client_overridable(tmp_path, monkeypatch):
    import execution.files as fm
    from execution.files import FileService, PathViolation
    monkeypatch.setattr(fm, "PROJECTS_DIR", tmp_path)
    FileService("alice", "ws").write("a.txt", "a")
    FileService("bob", "ws").write("b.txt", "b")
    with pytest.raises(PathViolation):
        FileService("alice", "../bob/ws")


# --- Provider failure ≠ success ---

def test_provider_exhausted_is_failure_not_content(monkeypatch):
    import asyncio
    import httpx
    from brain.llm import BrainLLM, ProviderExhaustedError
    from core.config import settings
    monkeypatch.setattr(settings, "OMNIROUTE_BASE_URL", "http://127.0.0.1:3000/api/v1")
    monkeypatch.setattr(settings, "OMNIROUTE_DEFAULT_MODEL", "m")
    brain = BrainLLM(provider="omniroute", model="m")
    brain._http = MagicMock()
    brain._http.post = AsyncMock(side_effect=httpx.ConnectError("down"))
    brain._all_providers = lambda: ["omniroute"]

    async def _run():
        with pytest.raises(ProviderExhaustedError):
            await brain.stream_chat([{"role": "user", "content": "hi"}], allow_fallback=False)

    asyncio.run(_run())


# --- Secrets redaction before durable log-like text ---

def test_secrets_redacted():
    from core.secrets_redact import redact_text
    s = redact_text("Authorization: Bearer sk-abcdefghijklmnopqrstuvwxyz")
    assert "sk-abcdefghijklmnopqrstuvwxyz" not in s


# --- Recovery does not invent success ---

def test_reconcile_without_evidence_not_complete():
    from brain.mission_durability import reconcile_mission_state
    r = reconcile_mission_state(
        status="running",
        files_changed=[{"path": "index.html"}],
        ponytail={"passed": True},
        evidence_refs=[],
        execution_ok=True,
    )
    assert r["action"] != "complete"


# --- run_chat_orchestration uses acceptance ---

def test_run_chat_orchestration_wires_acceptance():
    from brain import nuha_bridge
    plan = MagicMock()
    plan.id = "plan-1"
    plan.status = "plan_ready"
    plan.to_dict.return_value = {"steps": [], "personas": []}
    dres = MagicMock()
    dres.ok = True
    dres.status = "accepted"
    dres.mission_id = "m1"
    dres.task_id = "t1"
    dres.a2a_message_ids = []
    dres.ponytail = {"passed": True, "applicable": True, "evidence_id": "e1"}
    dres.evidence_refs = ["e1"]
    dres.files_changed = [{"path": "index.html"}]
    dres.error = None

    import asyncio

    async def _run():
        with patch("brain.orchestration.create_plan", AsyncMock(return_value=plan)):
            with patch("brain.delegation.run_delegated_mission", AsyncMock(return_value=dres)):
                out = await nuha_bridge.run_chat_orchestration(
                    user_id="u1", goal="build a site", execute=True
                )
                assert out["execution_path"] == "A2A_DELEGATION"
                assert out["ok"] is True

    asyncio.run(_run())
