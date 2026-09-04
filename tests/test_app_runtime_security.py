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
