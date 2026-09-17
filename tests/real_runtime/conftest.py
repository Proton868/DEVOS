"""Real-runtime suite fixtures and environment gate.

This suite MUST fail if fake runtime is enabled. It never turns FAKE on.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _env_report() -> dict:
    try:
        from execution.isolation import detect_backends
        iso = detect_backends()
    except Exception as e:
        iso = {"error": str(e)}
    return {
        "mode": "real_runtime",
        "DEVOS_REAL_RUNTIME_TESTS": os.environ.get("DEVOS_REAL_RUNTIME_TESTS", ""),
        "DEVOS_ORCH_FAKE_RUNTIME": os.environ.get("DEVOS_ORCH_FAKE_RUNTIME", ""),
        "DEVOS_ALLOW_FAKE_RUNTIME": os.environ.get("DEVOS_ALLOW_FAKE_RUNTIME", ""),
        "DEVOS_REAL_RUNTIME_PROVIDER": os.environ.get(
            "DEVOS_REAL_RUNTIME_PROVIDER", "scripted"
        ),
        "DEVOS_USE_DOCKER_SANDBOX": os.environ.get("DEVOS_USE_DOCKER_SANDBOX", ""),
        "DEVOS_ALLOW_DEGRADED_ISOLATION": os.environ.get(
            "DEVOS_ALLOW_DEGRADED_ISOLATION", ""
        ),
        "isolation_backend": iso.get("backend"),
        "isolation_strength": iso.get("strength"),
        "suitable_for_untrusted": iso.get("suitable_for_untrusted_code"),
        "database": (os.environ.get("DATABASE_URL") or "(settings-default)")[:80],
        "fake_runtime_forbidden": True,
        "fake_runtime_status": "disabled",
    }


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "real_runtime: production-path tests that forbid DEVOS_ORCH_FAKE_RUNTIME",
    )
    # Override parent tests/conftest.py defaults that enable FAKE for unit tests.
    # Real-runtime suite must never inherit FAKE=1 from the shared conftest.
    os.environ["DEVOS_REAL_RUNTIME_TESTS"] = "1"
    os.environ.pop("DEVOS_ORCH_FAKE_RUNTIME", None)
    os.environ.pop("DEVOS_ALLOW_FAKE_RUNTIME", None)
    # Isolated SQLite so AgentRuntime operation ledger works without Postgres/asyncpg
    os.environ["REQUIRE_POSTGRES"] = "false"
    os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///./data/real_runtime.db"


def pytest_sessionstart(session):
    os.environ["DEVOS_REAL_RUNTIME_TESTS"] = "1"
    # Capture FAKE state BEFORE clearing — enabling FAKE for this suite is a hard fail.
    was_fake = os.environ.get("DEVOS_ORCH_FAKE_RUNTIME", "")
    was_allow = os.environ.get("DEVOS_ALLOW_FAKE_RUNTIME", "")
    os.environ.pop("DEVOS_ORCH_FAKE_RUNTIME", None)
    os.environ.pop("DEVOS_ALLOW_FAKE_RUNTIME", None)
    if was_fake == "1" or was_allow == "1":
        report = _env_report()
        report["was_DEVOS_ORCH_FAKE_RUNTIME"] = was_fake
        report["was_DEVOS_ALLOW_FAKE_RUNTIME"] = was_allow
        sys.stderr.write(
            "\n[real_runtime] FATAL: fake runtime was enabled at session start\n"
            + json.dumps(report, indent=2)
            + "\n"
        )
        pytest.exit(
            "real_runtime_gate_failed: DEVOS_ORCH_FAKE_RUNTIME or ALLOW_FAKE was set",
            returncode=2,
        )
    sys.stderr.write(
        "\n========== REAL-RUNTIME GATE ==========\n"
        + json.dumps(_env_report(), indent=2)
        + "\n=======================================\n"
    )


def pytest_sessionfinish(session, exitstatus):
    sys.stderr.write(
        f"\n[real_runtime] session finished exitstatus={exitstatus}\n"
        + json.dumps(_env_report(), indent=2)
        + "\n"
    )


@pytest.fixture(autouse=True)
def _assert_no_fake_runtime():
    """Hard fail if fake runtime env is present or AgentRuntime is monkeypatched away."""
    os.environ["DEVOS_REAL_RUNTIME_TESTS"] = "1"
    os.environ.pop("DEVOS_ORCH_FAKE_RUNTIME", None)
    os.environ.pop("DEVOS_ALLOW_FAKE_RUNTIME", None)
    if os.environ.get("DEVOS_ORCH_FAKE_RUNTIME") == "1":
        pytest.fail("real_runtime_gate_failed: DEVOS_ORCH_FAKE_RUNTIME=1 during test")
    yield
    if os.environ.get("DEVOS_ORCH_FAKE_RUNTIME") == "1":
        pytest.fail("DEVOS_ORCH_FAKE_RUNTIME was re-enabled during test")
    if os.environ.get("DEVOS_ALLOW_FAKE_RUNTIME") == "1":
        pytest.fail("DEVOS_ALLOW_FAKE_RUNTIME was re-enabled during test")


def assert_real_agent_runtime_loaded():
    """Prove AgentRuntime is the real module, not a MagicMock/fake substitute."""
    import inspect
    from brain import agent_runtime as ar_mod
    from brain.agent_runtime import AgentRuntime
    assert ar_mod.__name__ == "brain.agent_runtime"
    assert inspect.getmodule(AgentRuntime) is ar_mod
    assert not getattr(AgentRuntime, "_is_fake_runtime", False)
    src = inspect.getsource(AgentRuntime)
    assert "class AgentRuntime" in src
    # UCIP must resolve
    from governance import ucip as ucip_mod
    assert ucip_mod.__name__ == "governance.ucip"
    from execution.files import FileService
    assert inspect.getmodule(FileService).__name__ == "execution.files"


@pytest.fixture
def real_user_ids():
    return {
        "user_a": "rr_user_a",
        "user_b": "rr_user_b",
        "project_a": "rr_proj_a",
        "project_b": "rr_proj_b",
    }


@pytest.fixture
def workspace(real_user_ids):
    from execution.files import PROJECTS_DIR, FileService

    uid = real_user_ids["user_a"]
    pid = real_user_ids["project_a"]
    root = PROJECTS_DIR / uid / pid
    root.mkdir(parents=True, exist_ok=True)
    fs = FileService(uid, pid)
    yield {"user_id": uid, "project_id": pid, "fs": fs, "root": root}


@pytest.fixture
def scripted_provider(monkeypatch):
    """Transport-boundary double only (default). AgentRuntime stays real."""
    mode = os.environ.get("DEVOS_REAL_RUNTIME_PROVIDER", "scripted").strip().lower()
    if mode in ("free", "live", "openrouter", "omniroute"):
        return {"mode": mode, "scripted": False}

    calls = {"n": 0}

    async def _scripted_stream(self, messages, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return json.dumps({
                "thought": "Create the requested file with exact contents",
                "action": "create_file",
                "action_input": {
                    "path": "hello_real_runtime.txt",
                    "content": "real-runtime-ok\n",
                },
            })
        # Second turn: natural-language completion (no tool JSON)
        return (
            "Completed. Created hello_real_runtime.txt with exact contents "
            "real-runtime-ok. Verification: file exists on disk."
        )

    import brain.llm as llm_mod
    monkeypatch.setattr(llm_mod.BrainLLM, "stream_chat", _scripted_stream, raising=True)
    # Operation ledger may require Postgres; allow local consequential tools offline
    async def _reserve(*a, **k):
        return "rr-op-1"
    async def _mark(*a, **k):
        return True
    async def _complete(*a, **k):
        return True
    try:
        import governance.execution_operations as eop
        monkeypatch.setattr(eop, "reserve_operation", _reserve, raising=False)
        monkeypatch.setattr(eop, "mark_running", _mark, raising=False)
        monkeypatch.setattr(eop, "complete_operation", _complete, raising=False)
    except Exception:
        pass
    return {"mode": "scripted", "scripted": True, "calls": calls}


def require_agent_runtime_deps():
    """Fail clearly if AgentRuntime dependencies are missing (not silent skip)."""
    missing = []
    for mod in ("sqlalchemy", "aiosqlite", "pydantic_settings"):
        try:
            __import__(mod)
        except ImportError:
            missing.append(mod)
    if missing:
        pytest.fail(
            "real_runtime_prereq_missing: "
            + ",".join(missing)
            + " — install requirements.txt before real-runtime AgentRuntime tests"
        )
    assert_real_agent_runtime_loaded()
