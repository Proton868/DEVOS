"""SSH host identity verification tests."""
from __future__ import annotations

import asyncio

import pytest


@pytest.fixture
async def db(tmp_path, monkeypatch):
    url = f"sqlite+aiosqlite:///{(tmp_path / 'ssh-hv.db').as_posix()}"
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
async def test_new_host_requires_approval_agent_cannot_auto(db):
    from governance.ssh_domain import create_connection
    from governance.ssh_host_verify import (
        verify_host_key,
        assert_connect_allowed,
        HostTrustState,
        HostKeyApprovalRequired,
        HostKeyUnknownError,
    )

    conn = await create_connection(
        owner_id="user-a", hostname="prime.example", username="u",
        auth_method="agent_forwarding",
    )
    r = await verify_host_key(
        owner_id="user-a",
        host_identity_id=conn["host_identity_id"],
        hostname="prime.example",
        port=22,
        presented_key_type="ssh-ed25519",
        presented_fingerprint="SHA256:NEWHOSTKEY111",
        actor="agent",
    )
    assert r.state == HostTrustState.NEW_HOST
    assert r.allowed is False
    assert r.requires_human_approval is True
    with pytest.raises(HostKeyApprovalRequired):
        assert_connect_allowed(r, actor="agent")
    with pytest.raises(HostKeyUnknownError):
        assert_connect_allowed(r, actor="user")


@pytest.mark.asyncio
async def test_trusted_host_allowed(db):
    from governance.ssh_domain import create_connection
    from governance.ssh_host_verify import (
        verify_host_key,
        approve_new_host,
        assert_connect_allowed,
        HostTrustState,
    )

    conn = await create_connection(
        owner_id="user-a", hostname="known.example", username="u",
        auth_method="agent_forwarding",
    )
    await approve_new_host(
        owner_id="user-a",
        tenant_id=None,
        host_identity_id=conn["host_identity_id"],
        key_type="ssh-ed25519",
        fingerprint_sha256="SHA256:KNOWNFINGERPRINT",
        trust_state="pinned",
    )
    r = await verify_host_key(
        owner_id="user-a",
        host_identity_id=conn["host_identity_id"],
        hostname="known.example",
        port=22,
        presented_key_type="ssh-ed25519",
        presented_fingerprint="SHA256:KNOWNFINGERPRINT",
        actor="agent",
    )
    assert r.state == HostTrustState.KNOWN_HOST
    assert r.allowed is True
    assert_connect_allowed(r, actor="agent")
    evidence = r.to_evidence()
    assert evidence["fingerprint_sha256"] == "SHA256:KNOWNFINGERPRINT"
    assert evidence["trust_state"] == "known_host"


@pytest.mark.asyncio
async def test_changed_fingerprint_hard_stop(db):
    from governance.ssh_domain import create_connection
    from governance.ssh_host_verify import (
        verify_host_key,
        approve_new_host,
        assert_connect_allowed,
        HostTrustState,
        HostKeyChangedError,
    )

    conn = await create_connection(
        owner_id="user-a", hostname="changed.example", username="u",
        auth_method="agent_forwarding",
    )
    await approve_new_host(
        owner_id="user-a",
        tenant_id=None,
        host_identity_id=conn["host_identity_id"],
        key_type="ssh-ed25519",
        fingerprint_sha256="SHA256:ORIGINAL",
        trust_state="pinned",
    )
    r = await verify_host_key(
        owner_id="user-a",
        host_identity_id=conn["host_identity_id"],
        hostname="changed.example",
        port=22,
        presented_key_type="ssh-ed25519",
        presented_fingerprint="SHA256:ATTACKERKEY",
        actor="agent",
    )
    assert r.state == HostTrustState.CHANGED_HOST
    assert r.hard_stop is True
    assert r.allowed is False
    with pytest.raises(HostKeyChangedError):
        assert_connect_allowed(r, actor="agent")
    with pytest.raises(HostKeyChangedError):
        assert_connect_allowed(r, actor="user")


@pytest.mark.asyncio
async def test_revoked_host_rejected(db):
    from governance.ssh_domain import create_connection
    from governance.ssh_host_verify import (
        verify_host_key,
        approve_new_host,
        revoke_known_host,
        assert_connect_allowed,
        HostTrustState,
        HostKeyRevokedError,
    )

    conn = await create_connection(
        owner_id="user-a", hostname="rev.example", username="u",
        auth_method="agent_forwarding",
    )
    pinned = await approve_new_host(
        owner_id="user-a",
        tenant_id=None,
        host_identity_id=conn["host_identity_id"],
        key_type="ssh-ed25519",
        fingerprint_sha256="SHA256:REVME",
        trust_state="pinned",
    )
    await revoke_known_host(owner_id="user-a", known_host_id=pinned["id"])
    r = await verify_host_key(
        owner_id="user-a",
        host_identity_id=conn["host_identity_id"],
        hostname="rev.example",
        port=22,
        presented_key_type="ssh-ed25519",
        presented_fingerprint="SHA256:REVME",
        actor="user",
    )
    assert r.state == HostTrustState.REVOKED_HOST
    with pytest.raises(HostKeyRevokedError):
        assert_connect_allowed(r, actor="user")


@pytest.mark.asyncio
async def test_user_tofu_approve_new(db):
    from governance.ssh_domain import create_connection
    from governance.ssh_host_verify import verify_host_key, HostTrustState

    conn = await create_connection(
        owner_id="user-a", hostname="tofu.example", username="u",
        auth_method="agent_forwarding",
    )
    r = await verify_host_key(
        owner_id="user-a",
        host_identity_id=conn["host_identity_id"],
        hostname="tofu.example",
        port=22,
        presented_key_type="ssh-ed25519",
        presented_fingerprint="SHA256:TOFUKEY",
        actor="user",
        auto_approve_new=True,
    )
    assert r.state == HostTrustState.NEW_HOST
    assert r.allowed is True


@pytest.mark.asyncio
async def test_malicious_replacement_requires_ack(db):
    from governance.ssh_domain import create_connection
    from governance.ssh_host_verify import (
        approve_new_host,
        replace_changed_host_key,
        HostVerifyError,
        verify_host_key,
        HostTrustState,
    )

    conn = await create_connection(
        owner_id="user-a", hostname="mitm.example", username="u",
        auth_method="agent_forwarding",
    )
    await approve_new_host(
        owner_id="user-a",
        tenant_id=None,
        host_identity_id=conn["host_identity_id"],
        key_type="ssh-ed25519",
        fingerprint_sha256="SHA256:OLD",
        trust_state="pinned",
    )
    with pytest.raises(HostVerifyError):
        await replace_changed_host_key(
            owner_id="user-a",
            tenant_id=None,
            host_identity_id=conn["host_identity_id"],
            key_type="ssh-ed25519",
            fingerprint_sha256="SHA256:NEW",
            acknowledge_mitm_risk=False,
        )
    await replace_changed_host_key(
        owner_id="user-a",
        tenant_id=None,
        host_identity_id=conn["host_identity_id"],
        key_type="ssh-ed25519",
        fingerprint_sha256="SHA256:NEW",
        acknowledge_mitm_risk=True,
    )
    r = await verify_host_key(
        owner_id="user-a",
        host_identity_id=conn["host_identity_id"],
        hostname="mitm.example",
        port=22,
        presented_key_type="ssh-ed25519",
        presented_fingerprint="SHA256:NEW",
        actor="user",
    )
    assert r.state == HostTrustState.KNOWN_HOST


@pytest.mark.asyncio
async def test_concurrent_verify_stable(db):
    from governance.ssh_domain import create_connection
    from governance.ssh_host_verify import verify_host_key, approve_new_host

    conn = await create_connection(
        owner_id="user-a", hostname="conc.example", username="u",
        auth_method="agent_forwarding",
    )
    await approve_new_host(
        owner_id="user-a",
        tenant_id=None,
        host_identity_id=conn["host_identity_id"],
        key_type="ssh-ed25519",
        fingerprint_sha256="SHA256:CONC",
        trust_state="pinned",
    )

    async def one():
        return await verify_host_key(
            owner_id="user-a",
            host_identity_id=conn["host_identity_id"],
            hostname="conc.example",
            port=22,
            presented_key_type="ssh-ed25519",
            presented_fingerprint="SHA256:CONC",
            actor="agent",
        )

    results = await asyncio.gather(*[one() for _ in range(10)])
    assert all(r.allowed for r in results)
