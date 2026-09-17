"""Governed subprocess boundary tests."""
import asyncio
import os
from pathlib import Path
from unittest import mock

import pytest

from execution.governed_exec import (
    scrub_env,
    needs_shell,
    try_split_argv,
    run_governed,
    scrub_output,
    MAX_STDOUT,
)
from execution.isolation import IsolationStrength, POLICY_UNTRUSTED, POLICY_TRUSTED


def test_scrub_env_drops_secrets():
    dirty = {
        "PATH": "/usr/bin",
        "OPENAI_API_KEY": "sk-secret",
        "GITHUB_TOKEN": "ghp_x",
        "DATABASE_URL": "postgres://x",
        "AWS_SECRET_ACCESS_KEY": "abc",
        "MY_PASSWORD": "nope",
        "HOME": "/tmp",
        "LANG": "C",
        "NORMAL_FLAG": "should_drop",
    }
    clean = scrub_env(dirty)
    assert "OPENAI_API_KEY" not in clean
    assert "GITHUB_TOKEN" not in clean
    assert "DATABASE_URL" not in clean
    assert "AWS_SECRET_ACCESS_KEY" not in clean
    assert "MY_PASSWORD" not in clean
    assert "NORMAL_FLAG" not in clean
    assert clean["PATH"] == "/usr/bin"
    assert clean["HOME"] == "/tmp"


def test_scrub_env_allows_explicit_secret_prefix():
    clean = scrub_env(extra={"SECRET_FOO": "bar", "OPENAI_API_KEY": "no"}, allow_secret_prefix=True)
    assert clean.get("SECRET_FOO") == "bar"
    assert "OPENAI_API_KEY" not in clean


def test_needs_shell_detects_metacharacters():
    assert needs_shell("npm install && npm test") is True
    assert needs_shell("echo hi | tee out") is True
    assert needs_shell("cat < in > out") is True
    assert needs_shell("echo $(whoami)") is True
    assert needs_shell("npm install") is False
    assert needs_shell("flutter test") is False
    assert needs_shell("python -m pytest -q") is False


def test_try_split_argv_simple():
    assert try_split_argv("npm run build") == ["npm", "run", "build"]
    assert try_split_argv("flutter pub get") == ["flutter", "pub", "get"]
    assert try_split_argv("echo a && echo b") is None


def test_scrub_output_redacts():
    s = scrub_output("api_key=sk-abcdefghijklmnop password: hunter2password")
    assert "sk-abcdefghijklmnop" not in s or "***" in s


def test_untrusted_governed_denies_network_only():
    async def _go():
        with mock.patch(
            "execution.isolation.select_backend",
            return_value=("unshare", IsolationStrength.NETWORK_ONLY.value),
        ):
            r = await run_governed(
                shell_command="echo hi",
                policy=POLICY_UNTRUSTED,
                timeout_s=5,
            )
            assert r.status == "isolation_unavailable"
            assert r.exit_code == 126
            assert r.ok is False
            assert r.isolation_evidence.get("policy_decision") == "denied"
    asyncio.run(_go())


def test_untrusted_argv_denies_degraded():
    async def _go():
        with mock.patch(
            "execution.isolation.select_backend",
            return_value=("degraded_host", IsolationStrength.DEGRADED.value),
        ):
            r = await run_governed(
                argv=["echo", "hi"],
                policy=POLICY_UNTRUSTED,
            )
            assert r.status == "isolation_unavailable"
    asyncio.run(_go())


def test_trusted_may_run_under_weaker_backend():
    async def _go():
        with mock.patch(
            "execution.isolation.select_backend",
            return_value=("unshare", IsolationStrength.NETWORK_ONLY.value),
        ):
            # Will attempt to run; may fail if unshare missing — but policy allows
            from execution.isolation import policy_allows_execution
            ok, _ = policy_allows_execution(POLICY_TRUSTED, IsolationStrength.NETWORK_ONLY.value)
            assert ok is True
    asyncio.run(_go())


def test_run_command_in_project_uses_governed_and_mode():
    async def _go():
        from execution.runner import run_command_in_project
        with mock.patch(
            "execution.isolation.select_backend",
            return_value=("none", IsolationStrength.NONE.value),
        ):
            r = await run_command_in_project(
                "u", "p", "npm install", policy="untrusted",
            )
            assert r["status"] == "isolation_unavailable"
            assert r["ok"] is False
            assert "isolation_evidence" in r
    asyncio.run(_go())


def test_path_traversal_blocked():
    async def _go():
        from execution.runner import run_command_in_project
        r = await run_command_in_project("../x", "p", "echo hi")
        assert r["ok"] is False
        assert r["exit_code"] == 2
    asyncio.run(_go())


def test_agent_runtime_subprocess_no_bare_shell():
    import inspect
    from brain.agent_runtime import AgentRuntime
    src = inspect.getsource(AgentRuntime._subprocess)
    assert "run_command_in_project" in src
    assert "create_subprocess_shell" not in src


def test_flutter_commands_classified_untrusted():
    from execution.isolation import classify_execution_request, POLICY_UNTRUSTED
    assert classify_execution_request(source="project_bootstrap") == POLICY_UNTRUSTED
    assert classify_execution_request(policy="untrusted") == POLICY_UNTRUSTED


def test_simple_commands_prefer_argv_mode():
    async def _go():
        with mock.patch(
            "execution.isolation.select_backend",
            return_value=("none", IsolationStrength.NONE.value),
        ):
            r = await run_governed(shell_command="pytest -q", policy="untrusted")
            # Denied by isolation, but mode should be argv for simple command
            assert r.mode == "argv"
            r2 = await run_governed(shell_command="echo a && echo b", policy="untrusted")
            assert r2.mode == "shell"
    asyncio.run(_go())


def test_static_guard_no_new_bare_project_shell():
    """Fail if project-scoped runners reintroduce bare create_subprocess_shell."""
    root = Path(__file__).resolve().parents[1]
    offenders = []
    # Paths that MUST route through isolation/governed for project cmds
    critical = [
        root / "execution" / "runner.py",
        root / "brain" / "check_runner.py",
        root / "brain" / "project_bootstrap.py",
    ]
    for path in critical:
        src = path.read_text(encoding="utf-8")
        if "create_subprocess_shell" in src:
            # Allow only if clearly not in run_command path — still flag
            offenders.append(str(path.relative_to(root)))
    # agent_runtime _subprocess must not use shell
    ar = (root / "brain" / "agent_runtime.py").read_text(encoding="utf-8")
    # crude: after "async def _subprocess" until next def, no create_subprocess_shell
    idx = ar.find("async def _subprocess")
    assert idx >= 0
    chunk = ar[idx: idx + 800]
    assert "create_subprocess_shell" not in chunk
    assert not offenders, f"bare shell in governed paths: {offenders}"
