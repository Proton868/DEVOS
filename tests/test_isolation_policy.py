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
