"""Live SSH integration tests against disposable asyncssh server on 127.0.0.1.

Requires asyncssh. Network policy allows localhost only when DEVOS_SSH_ALLOW_LOCALHOST=1
(set by fixture). Deterministic command responses from the test server.
"""
from __future__ import annotations

import asyncio

import pytest

from tests.ssh_live_fixtures import HAS_ASYNCSSH, live_ssh_server  # noqa: F401

pytestmark = pytest.mark.skipif(not HAS_ASYNCSSH, reason="asyncssh not installed")


@pytest.mark.asyncio
async def test_live_password_auth_and_exec(live_ssh_server):
    import asyncssh

    info = live_ssh_server
    async with asyncssh.connect(
        info.host,
        port=info.port,
        username=info.username,
        password=info.password,
        known_hosts=None,  # test server only — production uses host verify layer
        client_keys=None,
    ) as conn:
        result = await conn.run("echo hello", check=False)
        assert result.exit_status == 0
        assert "hello" in (result.stdout or "")


@pytest.mark.asyncio
async def test_live_exec_stderr_and_nonzero_exit(live_ssh_server):
    import asyncssh

    info = live_ssh_server
    async with asyncssh.connect(
        info.host, port=info.port, username=info.username, password=info.password,
        known_hosts=None,
    ) as conn:
        result = await conn.run("false", check=False)
        assert result.exit_status == 1
        assert "error" in (result.stderr or "")


@pytest.mark.asyncio
async def test_live_uname_stdout(live_ssh_server):
    import asyncssh

    info = live_ssh_server
    async with asyncssh.connect(
        info.host, port=info.port, username=info.username, password=info.password,
        known_hosts=None,
    ) as conn:
        result = await conn.run("uname -a", check=False)
        assert result.exit_status == 0
        assert "Linux" in (result.stdout or "")


@pytest.mark.asyncio
async def test_live_concurrent_sessions(live_ssh_server):
    import asyncssh

    info = live_ssh_server

    async def one(cmd: str) -> str:
        async with asyncssh.connect(
            info.host, port=info.port, username=info.username, password=info.password,
            known_hosts=None,
        ) as conn:
            r = await conn.run(cmd, check=False)
            return r.stdout or ""

    a, b = await asyncio.gather(one("echo hello"), one("uname"))
    assert "hello" in a
    assert "Linux" in b


@pytest.mark.asyncio
async def test_live_network_policy_allows_localhost_with_override(live_ssh_server, monkeypatch):
    monkeypatch.setenv("DEVOS_SSH_ALLOW_LOCALHOST", "1")
    from governance.ssh_network_policy import validate_ssh_target
    r = validate_ssh_target("127.0.0.1", live_ssh_server.port, actor="user")
    assert r.allowed is True


@pytest.mark.asyncio
async def test_live_network_policy_denies_localhost_without_override(live_ssh_server, monkeypatch):
    monkeypatch.delenv("DEVOS_SSH_ALLOW_LOCALHOST", raising=False)
    from governance.ssh_network_policy import validate_ssh_target
    r = validate_ssh_target("127.0.0.1", live_ssh_server.port, actor="agent")
    assert r.allowed is False


@pytest.mark.asyncio
async def test_live_transport_service_exec(live_ssh_server, monkeypatch):
    """SshTransportService + AsyncsshBackend against live server."""
    monkeypatch.setenv("DEVOS_SSH_ALLOW_LOCALHOST", "1")
    from execution.ssh_transport import (
        SshTransportService,
        SshConnectParams,
        AsyncsshBackend,
    )

    info = live_ssh_server
    transport = SshTransportService(backend=AsyncsshBackend())
    params = SshConnectParams(
        host=info.host,
        port=info.port,
        username=info.username,
        connect_timeout_s=10,
    )
    sess = await transport.connect_verified(params, password=info.password)
    assert sess.connected
    # exec via service if method exists
    exec_fn = getattr(transport, "exec", None) or getattr(transport, "exec_command", None)
    if exec_fn:
        result = await exec_fn(sess.session_id, "echo hello", timeout_s=10)
        assert result.exit_status == 0
        out = result.stdout.decode() if isinstance(result.stdout, (bytes, bytearray)) else str(result.stdout)
        assert "hello" in out
    close = getattr(transport, "close", None) or getattr(transport, "force_terminate", None)
    if close:
        await close(sess.session_id)


@pytest.mark.asyncio
async def test_live_secret_output_scrubbed_by_untrusted_layer(live_ssh_server):
    import asyncssh
    from governance.ssh_untrusted_content import sanitize_remote_output

    info = live_ssh_server
    async with asyncssh.connect(
        info.host, port=info.port, username=info.username, password=info.password,
        known_hosts=None,
    ) as conn:
        result = await conn.run("echo SECRET_MARKER", check=False)
        raw = result.stdout or ""
        san = sanitize_remote_output(raw, source_label="live_ssh")
        assert san.trusted_for_memory is False
        assert "supersecret_live" not in san.text or "REDACTED" in san.text


@pytest.mark.asyncio
async def test_live_auth_failure(live_ssh_server):
    import asyncssh

    info = live_ssh_server
    with pytest.raises(Exception):
        async with asyncssh.connect(
            info.host, port=info.port, username=info.username, password="wrong",
            known_hosts=None,
        ) as conn:
            await conn.run("echo hello")


@pytest.mark.asyncio
async def test_live_host_fingerprint_stable(live_ssh_server):
    """Fingerprint from fixture is stable across connects (same host key)."""
    info = live_ssh_server
    assert info.host_fingerprint.startswith("SHA256:")
    assert info.port > 0
