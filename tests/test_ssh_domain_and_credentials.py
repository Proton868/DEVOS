"""SSH domain model + secure credential layer tests."""
from __future__ import annotations

import pytest

FAKE_KEY = """-----BEGIN OPENSSH PRIVATE KEY-----
b3BlbnNzaC1rZXktdjEAAAAABG5vbmUAAAAEbm9uZQAAAAAAAAABAAAAMwAAAAtzc2gtZW
QyNTUxOQAAACFakeKeyMaterialForTestsOnlyDoNotUseInProductionXXXX=
-----END OPENSSH PRIVATE KEY-----
"""


@pytest.fixture
async def db(tmp_path, monkeypatch):
    url = f"sqlite+aiosqlite:///{(tmp_path / 'ssh.db').as_posix()}"
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("REQUIRE_POSTGRES", "false")
    monkeypatch.setenv("JWT_SECRET", "test-secret-key-for-ssh-vault-32chars!!")
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from core import database as dbmod
    # Force settings reload-ish by patching if needed
    dbmod.engine = create_async_engine(url, echo=False)
    dbmod.AsyncSessionLocal = async_sessionmaker(dbmod.engine, expire_on_commit=False)
    await dbmod.init_db()
    yield dbmod
    await dbmod.engine.dispose()


@pytest.mark.asyncio
async def test_create_credential_ref_public_has_no_material(db):
    from governance.ssh_credentials import (
        create_ssh_credential_ref,
        assert_no_secret_material,
        get_credential_handle,
    )
    pub = await create_ssh_credential_ref(
        owner_id="user-a",
        tenant_id="t1",
        name="prod-key",
        auth_method="private_key",
        secret_plaintext=FAKE_KEY,
        passphrase_plaintext="super-secret-passphrase",
        public_metadata={"key_type": "ed25519"},
    )
    assert "id" in pub
    assert pub["auth_method"] == "private_key"
    assert pub["revoked"] is False
    assert "secret_id" not in pub
    assert "encrypted_value" not in pub
    assert FAKE_KEY not in str(pub)
    assert "super-secret-passphrase" not in str(pub)
    assert_no_secret_material(pub)

    handle = await get_credential_handle("user-a", pub["id"])
    assert handle.credential_ref_id == pub["id"]
    assert FAKE_KEY not in str(handle.to_public_dict())
    assert_no_secret_material(handle.to_public_dict())


@pytest.mark.asyncio
async def test_resolve_material_owner_only_and_clear(db):
    from governance.ssh_credentials import (
        create_ssh_credential_ref,
        resolve_ssh_material,
        SshCredentialDenied,
    )
    pub = await create_ssh_credential_ref(
        owner_id="user-a",
        tenant_id=None,
        name="k",
        auth_method="private_key",
        secret_plaintext=FAKE_KEY,
    )
    mat = await resolve_ssh_material(owner_id="user-a", credential_ref_id=pub["id"])
    assert mat.private_key_pem and "PRIVATE KEY" in mat.private_key_pem
    # repr must not dump key
    assert "BEGIN OPENSSH" not in repr(mat)
    mat.clear()
    assert mat.private_key_pem is None

    with pytest.raises(SshCredentialDenied):
        await resolve_ssh_material(owner_id="user-b", credential_ref_id=pub["id"])


@pytest.mark.asyncio
async def test_revoked_credential_cannot_resolve(db):
    from governance.ssh_credentials import (
        create_ssh_credential_ref,
        revoke_credential,
        resolve_ssh_material,
        SshCredentialRevoked,
    )
    pub = await create_ssh_credential_ref(
        owner_id="user-a", tenant_id=None, name="k",
        auth_method="private_key", secret_plaintext=FAKE_KEY,
    )
    await revoke_credential("user-a", pub["id"])
    with pytest.raises(SshCredentialRevoked):
        await resolve_ssh_material(owner_id="user-a", credential_ref_id=pub["id"])


@pytest.mark.asyncio
async def test_connection_ownership_isolation(db):
    from governance.ssh_domain import (
        create_connection,
        get_connection_for_owner,
        SshAccessDenied,
    )
    from governance.ssh_credentials import create_ssh_credential_ref

    cred = await create_ssh_credential_ref(
        owner_id="user-a", tenant_id="t1", name="k",
        auth_method="private_key", secret_plaintext=FAKE_KEY,
    )
    conn = await create_connection(
        owner_id="user-a", tenant_id="t1",
        hostname="prime.example", port=22, username="deploy",
        credential_ref_id=cred["id"],
        label="Prime",
    )
    assert conn["hostname"] == "prime.example"
    assert "PRIVATE KEY" not in str(conn)
    assert FAKE_KEY not in str(conn)

    row, host = await get_connection_for_owner("user-a", conn["id"])
    assert host.hostname == "prime.example"

    with pytest.raises(SshAccessDenied):
        await get_connection_for_owner("user-b", conn["id"])


@pytest.mark.asyncio
async def test_revoked_connection_not_usable(db):
    from governance.ssh_domain import (
        create_connection,
        revoke_connection,
        assert_connection_usable,
        SshConnectionRevoked,
    )
    conn = await create_connection(
        owner_id="user-a", hostname="h.example", username="u",
        auth_method="agent_forwarding", agent_forwarding=True,
    )
    await revoke_connection("user-a", conn["id"])
    with pytest.raises(SshConnectionRevoked):
        await assert_connection_usable("user-a", conn["id"])


@pytest.mark.asyncio
async def test_session_and_execution_have_no_secrets(db):
    from governance.ssh_domain import (
        create_connection,
        create_session,
        create_execution_record,
    )
    from governance.ssh_credentials import assert_no_secret_material

    conn = await create_connection(
        owner_id="user-a", hostname="h", username="u",
        auth_method="agent_forwarding",
    )
    sess = await create_session(owner_id="user-a", tenant_id=None, connection_id=conn["id"])
    assert sess["connection_id"] == conn["id"]
    assert_no_secret_material(sess)

    ex = await create_execution_record(
        owner_id="user-a", tenant_id=None, connection_id=conn["id"],
        command="systemctl status nginx",
        session_id=sess["id"],
        agent_id="nuha",
    )
    assert ex["command"] == "systemctl status nginx"
    assert ex["status"] == "queued"
    assert_no_secret_material(ex)


@pytest.mark.asyncio
async def test_execution_scrubs_key_material_in_command(db):
    from governance.ssh_domain import create_connection, create_execution_record

    conn = await create_connection(
        owner_id="user-a", hostname="h", username="u",
        auth_method="agent_forwarding",
    )
    ex = await create_execution_record(
        owner_id="user-a", tenant_id=None, connection_id=conn["id"],
        command=f"echo '{FAKE_KEY}'",
    )
    assert "BEGIN OPENSSH PRIVATE KEY" not in ex["command"]
    assert "REDACTED" in ex["command"]


@pytest.mark.asyncio
async def test_known_host_pin_owner_scoped(db):
    from governance.ssh_domain import (
        create_connection,
        pin_known_host,
        SshAccessDenied,
    )
    conn = await create_connection(
        owner_id="user-a", hostname="secure.example", username="root",
        auth_method="agent_forwarding",
    )
    pin = await pin_known_host(
        owner_id="user-a", tenant_id=None,
        host_identity_id=conn["host_identity_id"],
        key_type="ssh-ed25519",
        fingerprint_sha256="SHA256:AAAA",
    )
    assert pin["fingerprint_sha256"] == "SHA256:AAAA"
    with pytest.raises(SshAccessDenied):
        await pin_known_host(
            owner_id="user-b", tenant_id=None,
            host_identity_id=conn["host_identity_id"],
            key_type="ssh-ed25519",
            fingerprint_sha256="SHA256:BBBB",
        )


@pytest.mark.asyncio
async def test_secret_table_not_exposed_via_connection_dict(db):
    from governance.ssh_credentials import create_ssh_credential_ref
    from governance.ssh_domain import create_connection
    from core.database import Secret
    from sqlalchemy import select

    cred = await create_ssh_credential_ref(
        owner_id="user-a", tenant_id=None, name="k",
        auth_method="private_key", secret_plaintext=FAKE_KEY,
    )
    conn = await create_connection(
        owner_id="user-a", hostname="h", username="u",
        credential_ref_id=cred["id"],
    )
    # DB still has encrypted secret for owner
    async with db.AsyncSessionLocal() as session:
        r = await session.execute(select(Secret).where(Secret.owner_id == "user-a"))
        secrets = list(r.scalars().all())
        assert secrets
        for s in secrets:
            assert FAKE_KEY not in s.encrypted_value
            assert "BEGIN" not in s.encrypted_value
    assert FAKE_KEY not in str(conn)


def test_scrub_and_assert_helpers():
    from governance.ssh_credentials import scrub_ssh_secrets_from_text, assert_no_secret_material
    scrubbed = scrub_ssh_secrets_from_text(FAKE_KEY)
    assert "BEGIN OPENSSH" not in scrubbed
    assert_no_secret_material({"auth_method": "password", "ok": True})
    with pytest.raises(AssertionError):
        assert_no_secret_material({"key": FAKE_KEY})
