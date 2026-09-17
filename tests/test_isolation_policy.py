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
    assert "--security-opt=no-new-privileges" in flags
    assert "--read-only" in flags
    assert "--user" in flags
    assert "65534:65534" in flags
    assert "--pids-limit=128" in flags
    joined = " ".join(flags)
    assert "docker.sock" not in joined
    assert "/var/run/docker" not in joined


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
    with mock.patch("execution.isolation._use_docker", return_value=False):
        def which(*names):
            if "bwrap" in names or "bubblewrap" in names:
                return "/usr/bin/bwrap"
            return None
        with mock.patch("execution.isolation._which", side_effect=which):
            backend, strength = select_backend()
            assert backend == "bwrap"
            assert strength == IsolationStrength.RESTRICTED.value


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
