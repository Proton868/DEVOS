"""Sandbox isolation strength policy tests."""
import asyncio
import os
from unittest import mock

from execution.isolation import (
    IsolationStrength,
    select_backend,
    policy_allows_execution,
    strength_allows_untrusted,
    detect_backends,
    run_isolated,
    POLICY_UNTRUSTED,
    POLICY_TRUSTED,
    _docker_flags,
)


def test_unshare_not_suitable_for_untrusted():
    assert not strength_allows_untrusted(IsolationStrength.NETWORK_ONLY.value)
    assert not strength_allows_untrusted(IsolationStrength.DEGRADED.value)
    assert not strength_allows_untrusted(IsolationStrength.NONE.value)
    assert strength_allows_untrusted(IsolationStrength.STRONG.value)
    assert strength_allows_untrusted(IsolationStrength.RESTRICTED.value)


def test_policy_denies_untrusted_on_network_only():
    ok, reason = policy_allows_execution(POLICY_UNTRUSTED, IsolationStrength.NETWORK_ONLY.value)
    assert ok is False
    assert "strong/restricted" in reason.lower() or "unshare" in reason.lower() or "isolation" in reason.lower()


def test_policy_allows_untrusted_on_strong():
    ok, _ = policy_allows_execution(POLICY_UNTRUSTED, IsolationStrength.STRONG.value)
    assert ok is True


def test_docker_flags_hardened_no_network():
    flags = _docker_flags(allow_network=False)
    assert "--network=none" in flags
    assert "--cap-drop=ALL" in flags
    joined = " ".join(flags)
    assert "no-new-privileges" in joined
    assert "--read-only" in flags
    assert "--user" in flags
    assert "65534:65534" in flags
    assert "--pids-limit=128" in flags
    assert "--memory=512m" in flags
    assert "--cpus=1" in flags
    assert "noexec" in joined
    assert "docker.sock" not in joined
    assert "/var/run/docker" not in joined
    assert "--privileged" not in flags


def test_docker_flags_network_when_allowed():
    flags = _docker_flags(allow_network=True)
    assert "--network=bridge" in flags
    assert "--network=none" not in flags


def test_detect_backends_shape():
    info = detect_backends()
    for k in ("available", "backend", "strength", "network_isolation",
              "filesystem_restriction", "degraded", "suitable_for_untrusted_code"):
        assert k in info


def test_untrusted_run_denied_without_restricted_backend():
    """On hosts with only unshare (or nothing), untrusted must deny."""
    async def _go():
        # Force no docker/bwrap/firejail by patching select
        with mock.patch("execution.isolation.select_backend", return_value=("unshare", IsolationStrength.NETWORK_ONLY.value)):
            r = await run_isolated(
                ["echo", "hi"], policy=POLICY_UNTRUSTED, require_isolation=True,
            )
            assert r.status == "isolation_unavailable"
            assert r.exit_code == 126
            assert r.strength == IsolationStrength.NETWORK_ONLY.value
    asyncio.run(_go())


def test_static_analysis_critical_eval():
    from governance.sandbox import SandboxedExecutor
    ex = SandboxedExecutor()
    v = ex._static_analysis("eval('1')", "python")
    assert any("CRITICAL" in x and "eval" in x for x in v)


def test_workflow_code_no_secret_injection_path():
    """Workflow executor must call sandbox with inject_secrets=None policy=untrusted."""
    import inspect
    from brain import workflow_executor as we
    src = inspect.getsource(we._run_capability_step)
    assert 'policy="untrusted"' in src or "policy='untrusted'" in src
    assert "inject_secrets=None" in src


def test_privileged_requires_strong():
    from execution.isolation import POLICY_PRIVILEGED, policy_allows_execution, IsolationStrength
    ok, reason = policy_allows_execution(POLICY_PRIVILEGED, IsolationStrength.NETWORK_ONLY.value)
    assert ok is False
    ok2, _ = policy_allows_execution(POLICY_PRIVILEGED, IsolationStrength.STRONG.value)
    assert ok2 is True


def test_classify_defaults_untrusted():
    from execution.isolation import classify_execution_request, POLICY_UNTRUSTED, POLICY_TRUSTED, POLICY_PRIVILEGED
    assert classify_execution_request() == POLICY_UNTRUSTED
    assert classify_execution_request(source="agent_runtime") == POLICY_UNTRUSTED
    assert classify_execution_request(policy="trusted") == POLICY_TRUSTED
    assert classify_execution_request(source="deploy") == POLICY_PRIVILEGED
    assert classify_execution_request(explicit_untrusted=True, policy="trusted") == POLICY_UNTRUSTED


def test_evaluate_isolation_decision_denies_network_only():
    from execution.isolation import evaluate_isolation_decision, IsolationStrength, POLICY_UNTRUSTED
    with mock.patch("execution.isolation.select_backend", return_value=("unshare", IsolationStrength.NETWORK_ONLY.value)):
        d = evaluate_isolation_decision(POLICY_UNTRUSTED)
        assert d["allowed"] is False
        assert d["policy_decision"] == "denied"
        assert d["trust_level"] == POLICY_UNTRUSTED
        assert "strong" in d["failure_reason"].lower() or "restricted" in d["failure_reason"].lower()


def test_evaluate_isolation_decision_allows_restricted():
    from execution.isolation import evaluate_isolation_decision, IsolationStrength, POLICY_UNTRUSTED
    with mock.patch("execution.isolation.select_backend", return_value=("bwrap", IsolationStrength.RESTRICTED.value)):
        d = evaluate_isolation_decision(POLICY_UNTRUSTED)
        assert d["allowed"] is True
        assert d["policy_decision"] == "allowed"


def test_degraded_rejected_for_untrusted():
    from execution.isolation import policy_allows_execution, IsolationStrength, POLICY_UNTRUSTED
    ok, _ = policy_allows_execution(POLICY_UNTRUSTED, IsolationStrength.DEGRADED.value)
    assert ok is False


def test_trusted_may_use_degraded_when_allowed():
    from execution.isolation import policy_allows_execution, IsolationStrength, POLICY_TRUSTED
    ok, _ = policy_allows_execution(POLICY_TRUSTED, IsolationStrength.DEGRADED.value)
    assert ok is True


def test_run_command_in_project_denies_untrusted_without_strong_backend():
    """Authoritative boundary: project runner must refuse untrusted when only network_only."""
    async def _go():
        from execution.runner import run_command_in_project
        from execution.isolation import IsolationStrength
        with mock.patch(
            "execution.isolation.select_backend",
            return_value=("unshare", IsolationStrength.NETWORK_ONLY.value),
        ):
            r = await run_command_in_project(
                "testuser", "testproj", "echo hi", policy="untrusted", timeout_s=5,
            )
            assert r["ok"] is False
            assert r["status"] == "isolation_unavailable"
            assert r["exit_code"] == 126
            evidence = r.get("isolation_evidence") or {}
            assert evidence.get("trust_level") == "untrusted"
            assert evidence.get("policy_decision") == "denied"
            assert evidence.get("failure_reason")
    asyncio.run(_go())


def test_run_command_in_project_denies_degraded_for_untrusted():
    async def _go():
        from execution.runner import run_command_in_project
        from execution.isolation import IsolationStrength
        with mock.patch(
            "execution.isolation.select_backend",
            return_value=("degraded_host", IsolationStrength.DEGRADED.value),
        ):
            r = await run_command_in_project(
                "u", "p", "echo hi", policy="untrusted",
            )
            assert r["status"] == "isolation_unavailable"
            assert r["ok"] is False
    asyncio.run(_go())


def test_run_command_in_project_path_escape_still_blocked():
    async def _go():
        from execution.runner import run_command_in_project
        r = await run_command_in_project("../evil", "proj", "echo hi")
        assert r["ok"] is False
        assert r["exit_code"] == 2
    asyncio.run(_go())


def test_isolation_evidence_on_result():
    async def _go():
        from execution.isolation import IsolationResult, IsolationStrength, POLICY_UNTRUSTED
        r = IsolationResult(
            status="isolation_unavailable",
            stdout="",
            stderr="need strong",
            exit_code=126,
            duration_ms=1,
            isolation="unshare",
            strength=IsolationStrength.NETWORK_ONLY.value,
            policy=POLICY_UNTRUSTED,
            policy_decision="denied",
            policy_reason="need strong",
        )
        ev = r.to_evidence()
        assert ev["trust_level"] == POLICY_UNTRUSTED
        assert ev["policy_decision"] == "denied"
        assert ev["failure_reason"] == "need strong"
        assert ev["strength"] == IsolationStrength.NETWORK_ONLY.value
    asyncio.run(_go())


def test_check_runner_respects_isolation():
    """check_runner default path must surface isolation_unavailable."""
    async def _go():
        from brain.check_runner import _default_runner
        from execution.isolation import IsolationStrength
        with mock.patch(
            "execution.isolation.select_backend",
            return_value=("unshare", IsolationStrength.NETWORK_ONLY.value),
        ):
            r = await _default_runner("echo hi", {"user_id": "u", "project_id": "p"})
            assert r["ok"] is False
            assert r.get("status") == "isolation_unavailable" or r["exit_code"] == 126
    asyncio.run(_go())


def test_bootstrap_runner_respects_isolation():
    async def _go():
        from brain.project_bootstrap import _default_runner
        from execution.isolation import IsolationStrength
        with mock.patch(
            "execution.isolation.select_backend",
            return_value=("none", IsolationStrength.NONE.value),
        ):
            r = await _default_runner("flutter pub get", {"user_id": "u", "project_id": "p"})
            assert r["ok"] is False
            assert r["exit_code"] == 126 or r.get("status") == "isolation_unavailable"
    asyncio.run(_go())


def test_select_backend_prefers_docker_when_enabled():
    with mock.patch("execution.isolation._use_docker", return_value=True):
        with mock.patch("execution.isolation._which", side_effect=lambda *n: "/usr/bin/docker" if "docker" in n else None):
            backend, strength = select_backend()
            assert backend == "docker"
            assert strength == IsolationStrength.STRONG.value


def test_select_backend_bwrap_restricted():
    """bwrap is selected only when the operational probe succeeds (not mere binary presence)."""
    with mock.patch("execution.isolation._use_docker", return_value=False):
        def which(*names):
            if "bwrap" in names or "bubblewrap" in names:
                return "/usr/bin/bwrap"
            return None
        with mock.patch("execution.isolation._which", side_effect=which):
            with mock.patch("execution.isolation._bwrap_operational", return_value=True):
                backend, strength = select_backend(allow_network=False)
                assert backend == "bwrap"
                assert strength == IsolationStrength.RESTRICTED.value


def test_select_backend_skips_non_operational_bwrap():
    """Installed but non-operational bwrap must never be selected; fall through."""
    with mock.patch("execution.isolation._use_docker", return_value=False):
        def which(*names):
            if "bwrap" in names or "bubblewrap" in names:
                return "/usr/bin/bwrap"
            if "unshare" in names:
                return "/usr/bin/unshare"
            return None
        with mock.patch("execution.isolation._which", side_effect=which):
            with mock.patch("execution.isolation._bwrap_operational", return_value=False):
                backend, strength = select_backend(allow_network=False)
                assert backend != "bwrap"
                assert backend == "unshare"
                assert strength == IsolationStrength.NETWORK_ONLY.value


def test_mission_acceptance_isolation_not_success():
    """Isolation refusal must not be interpretable as mission success."""
    from brain.mission_acceptance import evaluate_mission_acceptance
    # execution_ok=False when isolation denied
    decision = evaluate_mission_acceptance(
        execution_ok=False,
        status="isolation_unavailable",
        files_changed=[],
    )
    assert decision.get("ok") is False


def test_agent_runtime_no_bare_subprocess_fallback():
    """AgentRuntime._run_command must not fall back to bare host subprocess."""
    import inspect
    from brain import agent_runtime as ar
    src = inspect.getsource(ar.AgentRuntime._run_command)
    assert 'policy="untrusted"' in src or "policy='untrusted'" in src
    assert "source=\"agent_runtime\"" in src or "source='agent_runtime'" in src
    # Should not prefer bare _subprocess as primary path for project cmds
    assert "run_command_in_project" in src


def test_privileged_requires_restricted():
    ok, _ = policy_allows_execution("privileged", IsolationStrength.RESTRICTED.value)
    assert ok is True
    ok2, _ = policy_allows_execution("privileged", IsolationStrength.DEGRADED.value)
    assert ok2 is False


def test_evaluate_decision_untrusted_denied_on_unshare():
    from execution.isolation import evaluate_isolation_decision
    with mock.patch("execution.isolation.select_backend", return_value=("unshare", IsolationStrength.NETWORK_ONLY.value)):
        d = evaluate_isolation_decision("untrusted")
        assert d["allowed"] is False
        assert d["policy_decision"] == "denied"
        assert d["suitable_for_untrusted_code"] is False


def test_app_runtime_source_force_untrusted():
    """source=app_runtime must force untrusted even if policy=trusted (anti-spoof)."""
    from execution.isolation import classify_execution_request, POLICY_UNTRUSTED
    assert classify_execution_request(policy="trusted", source="app_runtime") == POLICY_UNTRUSTED
    assert classify_execution_request(policy="trusted", source="application_runtime") == POLICY_UNTRUSTED
    assert classify_execution_request(policy="privileged", source="app_runtime") == POLICY_UNTRUSTED


def test_bwrap_operational_safe_from_running_event_loop():
    """_bwrap_operational must not call asyncio.run() (nested loop would fail)."""
    from execution.isolation import _bwrap_operational, select_backend

    async def _go():
        ok = _bwrap_operational()
        assert isinstance(ok, bool)
        backend, strength = select_backend(allow_network=False)
        assert isinstance(backend, str)
        assert isinstance(strength, str)
        return ok, backend, strength

    result = asyncio.run(_go())
    assert result is not None


def test_bwrap_operational_not_mere_binary_check():
    """When binary is present but sandbox creation fails, probe returns False."""
    from execution.isolation import _bwrap_operational
    with mock.patch("execution.isolation._which", return_value="/usr/bin/bwrap"):
        with mock.patch("subprocess.run") as run_mock:
            run_mock.return_value = mock.Mock(returncode=1)
            assert _bwrap_operational() is False
            run_mock.assert_called()
            args = run_mock.call_args[0][0]
            joined = " ".join(str(a) for a in args)
            assert "--unshare-net" in joined or "true" in joined


def test_spawn_isolated_denied_for_untrusted_on_unshare():
    from execution.isolation import spawn_isolated, IsolationStrength, POLICY_UNTRUSTED

    async def _go():
        with mock.patch(
            "execution.isolation.select_backend",
            return_value=("unshare", IsolationStrength.NETWORK_ONLY.value),
        ):
            r = await spawn_isolated(
                ["sleep", "60"],
                policy=POLICY_UNTRUSTED,
                source="app_runtime",
                merge_stderr=True,
            )
            assert r.status == "isolation_unavailable"
            assert r.process is None
            assert r.policy_decision == "denied"
            ev = r.to_evidence()
            assert ev["trust_level"] == POLICY_UNTRUSTED
            assert ev["source"] == "app_runtime"
            assert ev["policy_decision"] == "denied"
            assert ev["strength"] == IsolationStrength.NETWORK_ONLY.value
            assert ev["actual_isolation"] == "unshare"
            assert ev["failure_reason"]
            assert ev["status"] == "isolation_unavailable"
            assert ev.get("exit_code") == 126

    asyncio.run(_go())


def test_spawn_isolated_anti_spoof_trusted_policy():
    """Even policy=trusted cannot elevate app_runtime source."""
    from execution.isolation import spawn_isolated, IsolationStrength, POLICY_UNTRUSTED

    async def _go():
        with mock.patch(
            "execution.isolation.select_backend",
            return_value=("unshare", IsolationStrength.NETWORK_ONLY.value),
        ):
            r = await spawn_isolated(
                ["echo", "hi"],
                policy="trusted",
                source="app_runtime",
            )
            assert r.policy == POLICY_UNTRUSTED
            assert r.policy_decision == "denied"
            assert r.process is None

    asyncio.run(_go())


def test_run_isolated_docker_path_invokes_docker():
    """run_isolated with Docker backend must build a docker run argv (not host cmd)."""
    from execution.isolation import run_isolated, IsolationStrength, POLICY_UNTRUSTED

    captured = {}

    async def fake_run(cmd, cwd, env, timeout_s, isolation, strength, t0, policy=POLICY_UNTRUSTED):
        captured["cmd"] = list(cmd)
        captured["isolation"] = isolation
        captured["strength"] = strength
        from execution.isolation import IsolationResult, IsolationLevel
        return IsolationResult(
            status="ok",
            stdout="uid=65534",
            stderr="",
            exit_code=0,
            duration_ms=1,
            isolation=isolation,
            isolation_level=IsolationLevel.ISOLATED.value,
            strength=strength if isinstance(strength, str) else strength.value,
            policy=POLICY_UNTRUSTED,
            policy_decision="allowed",
        )

    async def _go():
        with mock.patch("execution.isolation.select_backend", return_value=("docker", IsolationStrength.STRONG.value)):
            with mock.patch("execution.isolation._which", return_value="/usr/bin/docker"):
                with mock.patch("execution.isolation._run", side_effect=fake_run):
                    r = await run_isolated(
                        ["id"],
                        policy=POLICY_UNTRUSTED,
                        source="app_runtime",
                        allow_network=False,
                        language="bash",
                    )
                    assert r.status == "ok"
                    assert r.isolation == "docker"
                    assert r.strength == IsolationStrength.STRONG.value
                    assert r.policy_decision == "allowed"
                    cmd = captured["cmd"]
                    assert cmd[0] == "/usr/bin/docker"
                    assert "run" in cmd
                    assert "--network=none" in cmd
                    assert "--cap-drop=ALL" in cmd
                    assert "65534:65534" in cmd

    asyncio.run(_go())


def test_spawn_isolated_docker_path_invokes_docker():
    """spawn_isolated with Docker must spawn docker run, not a bare host process."""
    from execution.isolation import spawn_isolated, IsolationStrength, POLICY_UNTRUSTED

    captured = {}

    async def fake_create(*args, **kwargs):
        captured["args"] = list(args)
        captured["kwargs"] = kwargs

        class FakeProc:
            pid = 4242
            returncode = None

        return FakeProc()

    async def _go():
        with mock.patch("execution.isolation.select_backend", return_value=("docker", IsolationStrength.STRONG.value)):
            with mock.patch("execution.isolation._which", return_value="/usr/bin/docker"):
                with mock.patch("asyncio.create_subprocess_exec", side_effect=fake_create):
                    r = await spawn_isolated(
                        ["sleep", "30"],
                        policy=POLICY_UNTRUSTED,
                        source="app_runtime",
                        allow_network=True,
                        language="bash",
                        merge_stderr=True,
                    )
                    assert r.status == "spawned"
                    assert r.process is not None
                    assert r.isolation == "docker"
                    assert r.strength == IsolationStrength.STRONG.value
                    assert r.policy_decision == "allowed"
                    ev = r.to_evidence()
                    assert ev["actual_isolation"] == "docker"
                    assert ev["strength"] == IsolationStrength.STRONG.value
                    assert ev["trust_level"] == POLICY_UNTRUSTED
                    args = captured["args"]
                    assert args[0] == "/usr/bin/docker"
                    assert "--network=bridge" in args
                    assert "--cap-drop=ALL" in args

    asyncio.run(_go())


def test_docker_env_args_injected_into_argv():
    from execution.isolation import _build_isolated_argv, IsolationStrength
    with mock.patch("execution.isolation._which", return_value="/usr/bin/docker"):
        full, cwd = _build_isolated_argv(
            ["id"],
            cwd="/tmp/work",
            allow_network=False,
            language="bash",
            backend="docker",
            env={"PORT": "3911", "HOST": "127.0.0.1"},
        )
        assert full[0] == "/usr/bin/docker"
        joined = " ".join(full)
        assert "-e" in full
        assert "PORT=3911" in joined
        assert "HOST=127.0.0.1" in joined
        assert "--network=none" in full


def test_select_backend_docker_strong_when_enabled():
    with mock.patch("execution.isolation._use_docker", return_value=True):
        backend, strength = select_backend(allow_network=False)
        assert backend == "docker"
        assert strength == IsolationStrength.STRONG.value
