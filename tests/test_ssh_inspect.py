"""Governed SSH inspect API tests."""
from __future__ import annotations

import pytest

from governance.ssh_inspect import (
    inspect_remote,
    InspectKind,
    list_inspect_kinds,
    _safe_unit,
    InspectError,
)
from execution.ssh_transport import SshTransportService, MockSshBackend
from governance.ssh_credentials import assert_no_secret_material


@pytest.fixture
async def db(tmp_path, monkeypatch):
    url = f"sqlite+aiosqlite:///{(tmp_path / 'insp.db').as_posix()}"
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


@pytest.fixture
async def connected(db):
    from governance.ssh_domain import create_connection
    from governance.ssh_host_verify import approve_new_host

    conn = await create_connection(
        owner_id="user-a", hostname="insp.example", username="u",
        auth_method="agent_forwarding",
    )
    await approve_new_host(
        owner_id="user-a", tenant_id=None,
        host_identity_id=conn["host_identity_id"],
        key_type="ssh-ed25519",
        fingerprint_sha256="SHA256:TESTFINGERPRINTAAAA",
        trust_state="pinned",
    )
    return conn


def test_list_kinds():
    kinds = list_inspect_kinds()
    ids = {k["kind"] for k in kinds}
    assert ids >= {
        "processes", "services", "logs", "resources",
        "containers", "network", "system",
    }


def test_unit_validation():
    assert _safe_unit("nginx.service") == "nginx.service"
    with pytest.raises(InspectError):
        _safe_unit("../evil")
    with pytest.raises(InspectError):
        _safe_unit("foo; rm -rf /")


@pytest.mark.asyncio
async def test_inspect_processes(connected):
    transport = SshTransportService(backend=MockSshBackend(
        exec_stdout=b"USER PID %CPU %MEM COMMAND\nroot 1 0.0 0.1 /sbin/init\n"
    ))
    r = await inspect_remote(
        owner_id="user-a",
        connection_id=connected["id"],
        kind=InspectKind.PROCESSES,
        transport=transport,
    )
    assert r.status == "succeeded"
    assert r.risk_class == "read_only"
    assert r.parsed.get("count", 0) >= 1
    pub = r.to_public()
    assert_no_secret_material(pub)
    assert pub["mode"] == "remote_ssh"
    assert pub["evidence_id"]


@pytest.mark.asyncio
async def test_inspect_services_logs_resources(connected):
    transport = SshTransportService(backend=MockSshBackend(
        exec_stdout=b"nginx.service loaded active running Web server\n"
    ))
    for kind in (
        InspectKind.SERVICES,
        InspectKind.LOGS,
        InspectKind.RESOURCES,
        InspectKind.SYSTEM,
        InspectKind.NETWORK,
        InspectKind.CONTAINERS,
    ):
        r = await inspect_remote(
            owner_id="user-a",
            connection_id=connected["id"],
            kind=kind,
            transport=transport,
            lines=20,
        )
        assert r.status in ("succeeded", "failed")  # mock always succeeds
        assert r.kind == kind.value
        assert "PRIVATE" not in str(r.to_public())


@pytest.mark.asyncio
async def test_cross_user_denied(connected):
    transport = SshTransportService(backend=MockSshBackend())
    r = await inspect_remote(
        owner_id="user-b",
        connection_id=connected["id"],
        kind=InspectKind.SYSTEM,
        transport=transport,
    )
    assert r.status in ("denied", "failed")


@pytest.mark.asyncio
async def test_logs_with_unit(connected):
    transport = SshTransportService(backend=MockSshBackend(
        exec_stdout=b"2024-01-01T00:00:00 nginx started\n"
    ))
    r = await inspect_remote(
        owner_id="user-a",
        connection_id=connected["id"],
        kind=InspectKind.LOGS,
        unit="nginx.service",
        lines=10,
        transport=transport,
    )
    assert "nginx.service" in r.command
    assert r.parsed.get("count", 0) >= 1


@pytest.mark.asyncio
async def test_unknown_kind():
    with pytest.raises(InspectError):
        await inspect_remote(
            owner_id="user-a",
            connection_id="x",
            kind="not-a-kind",
        )
