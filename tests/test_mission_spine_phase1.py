"""Phase 1: single Nuha mission execution spine."""
from __future__ import annotations

import ast
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_chat_does_not_call_execute_plan():
    src = (ROOT / "api" / "routes" / "chat.py").read_text()
    tree = ast.parse(src)
    calls = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            name = ""
            if isinstance(fn, ast.Name):
                name = fn.id
            elif isinstance(fn, ast.Attribute):
                name = fn.attr
            if name == "execute_plan":
                calls.append(node.lineno)
    assert not calls, f"execute_plan called from chat.py at lines {calls}"


def test_chat_uses_run_delegated_mission():
    src = (ROOT / "api" / "routes" / "chat.py").read_text()
    assert "run_delegated_mission" in src


def test_nuha_bridge_uses_delegation_not_execute_plan():
    src = (ROOT / "brain" / "nuha_bridge.py").read_text()
    assert "run_delegated_mission" in src
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            name = fn.id if isinstance(fn, ast.Name) else getattr(fn, "attr", "")
            if name == "execute_plan":
                pytest.fail("execute_plan still called in nuha_bridge")


def test_scaffold_disabled_by_default(monkeypatch):
    monkeypatch.delenv("DEVOS_ALLOW_WEBSITE_SCAFFOLD_FALLBACK", raising=False)

    class _S:
        DEBUG = False

    async def _run():
        import brain.artifact_scaffold as mod
        # inject lightweight settings stand-in before call
        import types, sys
        fake = types.ModuleType("core.config")
        fake.settings = _S()
        monkeypatch.setitem(sys.modules, "core.config", fake)
        return await mod.scaffold_website_artifacts(
            user_id="u1", project_id="default", goal="build a shoe website"
        )

    res = asyncio.run(_run())
    assert res.get("ok") is False
    assert res.get("execution_path") == "SCAFFOLD_DISABLED"


def test_mission_truth_not_success_on_failure_status():
    from brain.nuha_bridge import mission_truth

    t = mission_truth("failed", explicit_ok=True)
    assert t["ok"] is False
    t2 = mission_truth("accepted", explicit_ok=True)
    assert t2["ok"] is True


def test_run_chat_orchestration_single_path():
    from brain import nuha_bridge

    plan = MagicMock()
    plan.id = "plan-1"
    plan.status = "plan_ready"
    plan.to_dict.return_value = {"steps": [], "personas": ["web"]}

    dres = MagicMock()
    dres.ok = True
    dres.status = "accepted"
    dres.mission_id = "m1"
    dres.task_id = "t1"
    dres.a2a_message_ids = ["a1"]
    dres.ponytail = {"passed": True, "applicable": True}
    dres.evidence_refs = ["e1"]
    dres.files_changed = [{"path": "index.html"}]
    dres.error = None

    async def _run():
        with patch("brain.orchestration.create_plan", AsyncMock(return_value=plan)) as cp:
            with patch("brain.delegation.run_delegated_mission", AsyncMock(return_value=dres)) as rd:
                with patch("brain.orchestration.execute_plan", AsyncMock()) as ep:
                    out = await nuha_bridge.run_chat_orchestration(
                        user_id="u1", goal="build a shoe website", execute=True
                    )
                    assert cp.await_count == 1
                    assert rd.await_count == 1
                    assert ep.await_count == 0
                    assert out["execution_path"] == "A2A_DELEGATION"
                    assert out["ok"] is True
                    return out

    asyncio.run(_run())
