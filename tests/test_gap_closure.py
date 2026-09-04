import time
import pytest
from execution.durable_store import (
    init_store, upsert_runtime, get_runtime, append_log, read_logs,
    save_share, get_share_db, save_deployment, get_deployment, new_id,
)
from execution.log_stream import publish_log, recent, subscribe
from execution.cloudflare_tunnel_mgr import cloudflared_available, TunnelError, start_tunnel
from execution.delivery_executor import execute_delivery_plan
from execution.isolation_runtime import isolation_available, wrap_command
from execution.shares import create_share, get_share, revoke_share
from execution.files import FileService
from execution.artifacts import write_bytes


def test_durable_runtime_and_logs():
    init_store()
    rid = new_id("rt_")
    upsert_runtime({
        "runtime_id": rid, "user_id": "u", "project_id": "p",
        "status": "READY", "pid": 1, "port": 3911, "cwd": "/tmp",
    })
    assert get_runtime(rid)["status"] == "READY"
    publish_log(rid, "stdout", "hello-stream")
    rows = recent(rid)
    assert any("hello-stream" in (r.get("line") or "") for r in rows)


@pytest.mark.asyncio
async def test_log_subscribe_receives_publish():
    rid = new_id("rt_")
    gen = subscribe(rid)
    # start generator
    publish_log(rid, "stdout", "live-line")
    # drain a few
    got = []
    for _ in range(5):
        item = await gen.__anext__()
        got.append(item)
        if item.get("line") == "live-line":
            break
    await gen.aclose()
    assert any(g.get("line") == "live-line" for g in got)


def test_durable_share_survives_lookup():
    fs = FileService("du", "dp")
    write_bytes(fs, "index.html", b"<html>x</html>")
    rec = create_share("du", "dp", "index.html", ttl_seconds=120)
    assert get_share_db(rec.id) is not None
    assert get_share(rec.id) is not None
    revoke_share(rec.id, "du")
    assert get_share(rec.id) is None


def test_cloudflared_info_and_missing_start():
    info = cloudflared_available()
    assert "available" in info
    # without binary/token start should raise
    import asyncio
    async def run():
        with pytest.raises(TunnelError):
            await start_tunnel(user_id="u", project_id="p", local_port=9999)
    asyncio.run(run())


def test_isolation_mode_honest():
    info = isolation_available()
    assert info["mode"] in ("docker", "bwrap", "unshare", "process")
    cmd = wrap_command(["echo", "x"], cwd="/tmp", net=False)
    assert isinstance(cmd, list)


@pytest.mark.asyncio
async def test_delivery_executor_inspect_path():
    fs = FileService("delu", "delp")
    write_bytes(fs, "index.html", b"<html>hi</html>")
    result = await execute_delivery_plan(user_id="delu", project_id="delp", goal="preview")
    assert result["status"] in ("completed", "failed", "ask_user", "cancelled")
    assert any(e.get("node") == "inspect" for e in result["evidence"])
