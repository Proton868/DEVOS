import os
import pytest
from execution.app_runtime import filter_env, ApplicationRuntime, AppRuntimeSpec
from execution.files import FileService
from execution.shares import create_share, get_share, revoke_share, read_share_bytes
from execution.deploy import list_adapters, get_adapter
from execution.deploy.base import DeploymentStatus


def test_filter_env_strips_secrets():
    os.environ["OPENROUTER_API_KEY"] = "secret-should-not-leak"
    os.environ["VERCEL_TOKEN"] = "v-secret"
    env = filter_env({"OPENROUTER_API_KEY": "x", "SAFE_VAR": "ok", "MY_TOKEN": "nope"})
    assert "OPENROUTER_API_KEY" not in env
    assert "VERCEL_TOKEN" not in env
    assert "MY_TOKEN" not in env
    assert env.get("SAFE_VAR") == "ok"
    assert "PATH" in env


def test_share_lifecycle():
    fs = FileService("share-user", "share-ws")
    from execution.artifacts import write_bytes
    write_bytes(fs, "index.html", b"<html>hi</html>")
    rec = create_share("share-user", "share-ws", "index.html", ttl_seconds=60)
    assert get_share(rec.id) is not None
    r2, data = read_share_bytes(rec.id)
    assert b"hi" in data
    assert revoke_share(rec.id, "share-user")
    assert get_share(rec.id) is None


def test_share_blocks_env():
    fs = FileService("share-user2", "share-ws2")
    from execution.artifacts import write_bytes
    write_bytes(fs, ".env", b"SECRET=1")
    with pytest.raises(ValueError):
        create_share("share-user2", "share-ws2", ".env")


@pytest.mark.asyncio
async def test_deploy_fail_closed():
    assert "vercel" in list_adapters()
    ad = get_adapter("vercel")
    r = await ad.deploy(project_path="x", meta={}, credentials={})
    assert r.status == DeploymentStatus.FAILED
    assert r.error == "DEPLOYMENT_AUTH_REQUIRED"


def test_runtime_static_ready():
    import asyncio
    from execution.artifacts import write_bytes
    fs = FileService("rt-user", "rt-static")
    write_bytes(fs, "index.html", b"<html>s</html>")
    rt = ApplicationRuntime(AppRuntimeSpec(user_id="rt-user", project_id="rt-static"))
    st = asyncio.run(rt.start())
    assert st.state.value in ("READY", "UNSUPPORTED", "FAILED")


def test_start_source_has_no_direct_subprocess_or_legacy_wrap():
    """Static regression: start() must use spawn_isolated, not host/legacy paths."""
    import inspect
    from execution import app_runtime as ar
    src = inspect.getsource(ar.ApplicationRuntime.start)
    assert "spawn_isolated" in src
    assert "create_subprocess_exec" not in src
    assert "wrap_command" not in src
    assert "isolation_runtime" not in src
    run_src = inspect.getsource(ar.ApplicationRuntime._run_cmd)
    assert "run_isolated" in run_src
    assert "trust" not in run_src or "POLICY_UNTRUSTED" in run_src
    assert "create_subprocess_exec" not in run_src


@pytest.mark.asyncio
async def test_app_runtime_start_uses_spawn_isolated(monkeypatch):
    """ApplicationRuntime.start() must call canonical spawn_isolated."""
    from execution.isolation import SpawnResult, IsolationStrength, POLICY_UNTRUSTED

    called = {}

    async def fake_spawn(cmd, **kwargs):
        called["cmd"] = cmd
        called["kwargs"] = kwargs
        return SpawnResult(
            status="isolation_unavailable",
            process=None,
            isolation="unshare",
            strength=IsolationStrength.NETWORK_ONLY.value,
            policy=POLICY_UNTRUSTED,
            policy_decision="denied",
            policy_reason="Untrusted code requires strong/restricted isolation",
            source="app_runtime",
            exit_code=126,
        )

    monkeypatch.setattr("execution.app_runtime.spawn_isolated", fake_spawn)
    rt = ApplicationRuntime(
        AppRuntimeSpec(
            user_id="u-spawn",
            project_id="p-spawn",
            kind="NODE_APP",
            start_command=["node", "server.js"],
            allow_network=False,
        )
    )
    st = await rt.start(port=3999, timeout=1)
    assert st.state.value == "FAILED"
    assert called["kwargs"].get("policy") == POLICY_UNTRUSTED
    assert called["kwargs"].get("source") == "app_runtime"
    assert called["kwargs"].get("allow_network") is False
    assert called["kwargs"].get("language") == "node"
    iso = (st.evidence or {}).get("isolation") or {}
    assert iso.get("policy_decision") == "denied"
    assert iso.get("trust_level") == POLICY_UNTRUSTED


@pytest.mark.asyncio
async def test_app_runtime_fail_closed_network_only(monkeypatch):
    """Untrusted project must fail closed when only network_only/unshare is available."""
    from unittest import mock
    from execution.isolation import IsolationStrength

    with mock.patch(
        "execution.isolation.select_backend",
        return_value=("unshare", IsolationStrength.NETWORK_ONLY.value),
    ):
        rt = ApplicationRuntime(
            AppRuntimeSpec(
                user_id="u-fc",
                project_id="p-fc",
                kind="NODE_APP",
                start_command=["node", "server.js"],
                allow_network=True,
            )
        )
        st = await rt.start(port=3998, timeout=1)
        assert st.state.value == "FAILED"
        assert rt._proc is None
        iso = (st.evidence or {}).get("isolation") or {}
        assert iso.get("policy_decision") == "denied"
        assert iso.get("trust_level") == "untrusted"
        assert iso.get("strength") == IsolationStrength.NETWORK_ONLY.value
        assert iso.get("actual_isolation") == "unshare"
        assert iso.get("failure_reason")
        assert iso.get("source") == "app_runtime"
        assert iso.get("status") == "isolation_unavailable"


@pytest.mark.asyncio
async def test_app_runtime_allow_network_reaches_spawn(monkeypatch):
    from execution.isolation import SpawnResult, IsolationStrength, POLICY_UNTRUSTED

    seen = {}

    async def fake_spawn(cmd, **kwargs):
        seen.update(kwargs)
        return SpawnResult(
            status="isolation_unavailable",
            process=None,
            isolation="none",
            strength=IsolationStrength.NONE.value,
            policy=POLICY_UNTRUSTED,
            policy_decision="denied",
            policy_reason="no backend",
            source="app_runtime",
            exit_code=126,
        )

    monkeypatch.setattr("execution.app_runtime.spawn_isolated", fake_spawn)
    rt = ApplicationRuntime(
        AppRuntimeSpec(
            user_id="u-net",
            project_id="p-net",
            kind="NODE_APP",
            start_command=["npm", "start"],
            allow_network=True,
        )
    )
    await rt.start(port=3997, timeout=1)
    assert seen.get("allow_network") is True


@pytest.mark.asyncio
async def test_app_runtime_isolation_evidence_contract_denied(monkeypatch):
    """Denied start must expose full isolation evidence contract fields."""
    from unittest import mock
    from execution.isolation import IsolationStrength

    required = {
        "trust_level", "source", "requested_isolation", "actual_isolation",
        "strength", "isolation_level", "policy_decision", "failure_reason",
        "status", "exit_code",
    }
    with mock.patch(
        "execution.isolation.select_backend",
        return_value=("unshare", IsolationStrength.NETWORK_ONLY.value),
    ):
        rt = ApplicationRuntime(
            AppRuntimeSpec(
                user_id="u-ev",
                project_id="p-ev",
                kind="PYTHON_APP",
                start_command=["python", "app.py"],
            )
        )
        st = await rt.start(port=3996, timeout=1)
        assert st.state.value == "FAILED"
        iso = (st.evidence or {}).get("isolation") or {}
        missing = required - set(iso.keys())
        assert not missing, f"missing evidence keys: {missing}"
        assert iso["policy_decision"] == "denied"
        assert iso["trust_level"] == "untrusted"
        assert iso["source"] == "app_runtime"
        assert iso["actual_isolation"] == "unshare"
        assert iso["strength"] == IsolationStrength.NETWORK_ONLY.value
        assert iso["failure_reason"]
        assert rt._proc is None
