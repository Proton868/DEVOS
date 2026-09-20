"""SSH transport service tests (mock backend)."""
from __future__ import annotations

import pytest

from execution.ssh_transport import (
    SshTransportService,
    MockSshBackend,
    SshConnectParams,
    SshAuthError,
    SshTimeoutError,
    SshTransportError,
)


@pytest.mark.asyncio
async def test_successful_auth_and_exec():
    svc = SshTransportService(backend=MockSshBackend(exec_stdout=b"hello\n"))
    params = SshConnectParams(host="h", port=22, username="u")
    sess = await svc.connect_verified(params, private_key_pem="fake")
    assert sess.connected
    assert sess.host_fingerprint.startswith("SHA256:")
    result = await svc.exec(sess.session_id, "echo hello")
    assert result.exit_status == 0
    assert b"hello" in result.stdout
    await svc.close(sess.session_id)
    assert not sess.connected


@pytest.mark.asyncio
async def test_auth_failure():
    svc = SshTransportService(backend=MockSshBackend(fail_auth=True))
    with pytest.raises(SshAuthError):
        await svc.connect_verified(SshConnectParams(host="h", username="u"))


@pytest.mark.asyncio
async def test_host_verify_failure_closes_connection():
    backend = MockSshBackend()
    svc = SshTransportService(backend=backend)

    async def reject(params):
        raise SshTransportError("host_verification_failed")

    with pytest.raises(SshTransportError):
        await svc.connect_verified(
            SshConnectParams(host="h", username="u"),
            host_verify=reject,
        )
    assert backend.closed is True


@pytest.mark.asyncio
async def test_timeout():
    svc = SshTransportService(backend=MockSshBackend(hang=True))
    with pytest.raises(SshTimeoutError):
        await svc.connect_verified(
            SshConnectParams(host="h", username="u", connect_timeout_s=0.05)
        )


@pytest.mark.asyncio
async def test_connection_drop_on_exec():
    svc = SshTransportService(backend=MockSshBackend(drop_after_connect=True))
    sess = await svc.connect_verified(SshConnectParams(host="h", username="u"))
    with pytest.raises(SshTransportError):
        await svc.exec(sess.session_id, "true")


@pytest.mark.asyncio
async def test_remote_command_failure_exit_code():
    svc = SshTransportService(backend=MockSshBackend(exec_exit=1, exec_stderr=b"fail\n"))
    sess = await svc.connect_verified(SshConnectParams(host="h", username="u"))
    result = await svc.exec(sess.session_id, "false")
    assert result.exit_status == 1
    assert b"fail" in result.stderr


@pytest.mark.asyncio
async def test_concurrent_sessions():
    svc = SshTransportService(backend=MockSshBackend())
    s1 = await svc.connect_verified(SshConnectParams(host="h1", username="u"))
    s2 = await svc.connect_verified(SshConnectParams(host="h2", username="u"))
    assert s1.session_id != s2.session_id
    await svc.open_pty(s1.session_id)
    await svc.open_pty(s2.session_id)
    await svc.force_terminate(s1.session_id)
    await svc.close(s2.session_id)


@pytest.mark.asyncio
async def test_capability_connect_with_host_verify(db_ssh):
    from governance.ssh_domain import create_connection
    from governance.ssh_host_verify import approve_new_host
    from execution.ssh_capabilities import SSHConnectionCapability
    from execution.ssh_transport import SshTransportService, MockSshBackend

    conn = await create_connection(
        owner_id="user-a", hostname="cap.example", username="deploy",
        auth_method="agent_forwarding",
    )
    await approve_new_host(
        owner_id="user-a",
        tenant_id=None,
        host_identity_id=conn["host_identity_id"],
        key_type="ssh-ed25519",
        fingerprint_sha256="SHA256:TESTFINGERPRINTAAAA",
        trust_state="pinned",
    )
    # Mock presents SHA256:TESTFINGERPRINTAAAA by default
    svc = SshTransportService(backend=MockSshBackend())
    cap = SSHConnectionCapability(owner_id="user-a", transport=svc)
    session, verify = await cap.connect(connection_id=conn["id"], actor="agent")
    assert session.connected
    assert verify.allowed
    assert verify.to_evidence()["hostname"] == "cap.example"
    pub = session.to_public()
    assert pub["mode"] == "remote_ssh"
    assert "PRIVATE" not in str(pub)


@pytest.fixture
async def db_ssh(tmp_path, monkeypatch):
    url = f"sqlite+aiosqlite:///{(tmp_path / 'ssh-tr.db').as_posix()}"
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("REQUIRE_POSTGRES", "false")
    monkeypatch.setenv("JWT_SECRET", "test-secret-key-for-ssh-vault-32chars!!")
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from core import database as dbmod

    dbmod.engine = create_async_engine(url, echo=False)
    dbmod.AsyncSessionLocal = async_sessionmaker(dbmod.engine, expire_on_commit=False)
    await dbmod.init_db()
    yield dbmod
    await dbmod.engine.dispose()


@pytest.mark.asyncio
async def test_changed_host_blocks_capability_connect(db_ssh):
    from governance.ssh_domain import create_connection
    from governance.ssh_host_verify import approve_new_host, HostKeyChangedError
    from execution.ssh_capabilities import SSHConnectionCapability
    from execution.ssh_transport import SshTransportService, MockSshBackend

    conn = await create_connection(
        owner_id="user-a", hostname="bad.example", username="u",
        auth_method="agent_forwarding",
    )
    await approve_new_host(
        owner_id="user-a",
        tenant_id=None,
        host_identity_id=conn["host_identity_id"],
        key_type="ssh-ed25519",
        fingerprint_sha256="SHA256:LEGIT",
        trust_state="pinned",
    )
    svc = SshTransportService(
        backend=MockSshBackend(host_fingerprint="SHA256:EVIL")
    )
    cap = SSHConnectionCapability(owner_id="user-a", transport=svc)
    with pytest.raises(HostKeyChangedError):
        await cap.connect(connection_id=conn["id"], actor="agent")
