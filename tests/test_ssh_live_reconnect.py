"""Live TCP reconnect implementation tests."""
from __future__ import annotations

import pytest

from execution.ssh_transport import (
    SshTransportService,
    MockSshBackend,
    SshConnectParams,
    SshTransportError,
    SshHostVerifyFailed,
)
from governance.ssh_reconnect import (
    clear_controllers_for_tests,
    get_controller,
    SessionConnectionState,
)
from execution.ssh_remote_session import RemoteSshSession


def setup_function():
    clear_controllers_for_tests()


@pytest.mark.asyncio
async def test_reconnect_after_tcp_drop():
    backend = MockSshBackend(fail_connect_times=0)
    transport = SshTransportService(backend=backend)
    params = SshConnectParams(host="h.example", username="u")
    sess = await transport.connect_verified(params)
    assert sess.connected

    # Simulate TCP reset
    transport.mark_dropped(sess.session_id, reason="tcp_reset")
    assert not transport.is_alive(sess.session_id)

    sess2 = await transport.reconnect(
        sess.session_id,
        params,
        session_key="user-a:conn-1",
        reason="tcp_reset",
        max_attempts=3,
        backoff_s=0.01,
    )
    assert sess2.connected
    assert sess2.session_id == sess.session_id
    assert transport.is_alive(sess.session_id)
    ctrl = get_controller("user-a:conn-1")
    assert ctrl.state == SessionConnectionState.CONNECTED
    assert any(a.state == "RECONNECTED" for a in ctrl.attempts)


@pytest.mark.asyncio
async def test_reconnect_retries_then_succeeds():
    backend = MockSshBackend(fail_connect_times=0)
    transport = SshTransportService(backend=backend)
    params = SshConnectParams(host="h.example", username="u")
    sess = await transport.connect_verified(params)
    transport.mark_dropped(sess.session_id, reason="network_loss")
    # Fail next two TCP attempts, then succeed
    backend.fail_connect_times = 2
    backend._connect_attempts = 0

    sess2 = await transport.reconnect(
        sess.session_id,
        params,
        session_key="u:c2",
        reason="network_loss",
        max_attempts=5,
        backoff_s=0.01,
    )
    assert sess2.connected
    assert backend._connect_attempts >= 3  # failed+failed+success


@pytest.mark.asyncio
async def test_reconnect_refuses_changed_host_key():
    backend = MockSshBackend(
        host_fingerprint="SHA256:ORIGINAL",
        reconnect_fingerprint="SHA256:CHANGED",
    )
    transport = SshTransportService(backend=backend)
    params = SshConnectParams(host="h.example", username="u")
    sess = await transport.connect_verified(params)
    assert sess.host_fingerprint == "SHA256:ORIGINAL"
    transport.mark_dropped(sess.session_id, reason="daemon_restart")

    with pytest.raises(SshHostVerifyFailed):
        await transport.reconnect(
            sess.session_id,
            params,
            session_key="u:c3",
            reason="ssh_daemon_restart",
            max_attempts=2,
            backoff_s=0.01,
        )
    ctrl = get_controller("u:c3")
    assert ctrl.state == SessionConnectionState.FAILED


@pytest.mark.asyncio
async def test_reconnect_exhausted_attempts():
    backend = MockSshBackend(fail_connect_times=100)
    transport = SshTransportService(backend=backend)
    params = SshConnectParams(host="h.example", username="u")
    # First connect would fail — allow one success then always fail
    backend.fail_connect_times = 0
    sess = await transport.connect_verified(params)
    backend.fail_connect_times = 100
    backend._connect_attempts = 0
    transport.mark_dropped(sess.session_id)

    with pytest.raises(SshTransportError):
        await transport.reconnect(
            sess.session_id,
            params,
            session_key="u:c4",
            max_attempts=2,
            backoff_s=0.01,
        )


@pytest.mark.asyncio
async def test_remote_session_reconnect_notifies_states():
    backend = MockSshBackend()
    transport = SshTransportService(backend=backend)
    params = SshConnectParams(host="h.example", username="u")
    ts = await transport.connect_verified(params)

    states = []

    class FakeWS:
        async def send_json(self, payload):
            if payload.get("type") == "connection_state":
                states.append(payload.get("connection_state"))

    sess = RemoteSshSession(
        user_id="user-a",
        connection_id="conn-1",
        transport_session=ts,
        host_evidence={"fingerprint_sha256": ts.host_fingerprint},
        transport=transport,
    )
    sess.attach(FakeWS())

    await sess.handle_disconnect("network_loss")
    assert "DISCONNECTED" in states

    await sess.reconnect(reason="network_loss", max_attempts=3, backoff_s=0.01)
    assert "RECONNECTING" in states
    assert "RECONNECTED" in states
    assert "CONNECTED" in states
    assert sess.transport_session.connected


@pytest.mark.asyncio
async def test_keepalive_probe_marks_drop():
    backend = MockSshBackend()
    transport = SshTransportService(backend=backend)
    params = SshConnectParams(host="h.example", username="u")
    sess = await transport.connect_verified(params)
    backend.force_drop(sess._backend)
    alive = await transport.keepalive_probe(sess.session_id)
    assert alive is False
    assert sess.connected is False
