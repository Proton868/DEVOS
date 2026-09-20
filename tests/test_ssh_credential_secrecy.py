"""SSH credential secrecy — leak-path coverage for the threat model."""
from __future__ import annotations

import logging

import pytest

FAKE_KEY = """-----BEGIN OPENSSH PRIVATE KEY-----
b3BlbnNzaC1rZXktdjEAAAAABG5vbmUAAAAEbm9uZQAAAAAAAAABAAAAMwAAAAtzc2gtZW
QyNTUxOQAAACFakeKeyMaterialForTestsOnlyDoNotUseInProductionXXXX=
-----END OPENSSH PRIVATE KEY-----
"""
FAKE_PASS = "test-passphrase-should-never-leak"
FAKE_PASSWORD = "hunter2-ssh-password"


@pytest.fixture
async def db(tmp_path, monkeypatch):
    url = f"sqlite+aiosqlite:///{(tmp_path / 'ssh-sec.db').as_posix()}"
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("REQUIRE_POSTGRES", "false")
    monkeypatch.setenv("JWT_SECRET", "test-secret-key-for-ssh-vault-32chars!!")
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from core import database as dbmod

    dbmod.engine = create_async_engine(url, echo=False)
    dbmod.AsyncSessionLocal = async_sessionmaker(dbmod.engine, expire_on_commit=False)
    await dbmod.init_db()
    from governance.ssh_credentials import clear_access_audit_for_tests

    clear_access_audit_for_tests()
    yield dbmod
    await dbmod.engine.dispose()


@pytest.mark.asyncio
async def test_api_response_shape_has_no_secrets(db):
    from governance.ssh_credentials import (
        create_ssh_credential_ref,
        assert_no_secret_material,
    )

    pub = await create_ssh_credential_ref(
        owner_id="user-a",
        tenant_id="t1",
        name="prod",
        auth_method="private_key",
        secret_plaintext=FAKE_KEY,
        passphrase_plaintext=FAKE_PASS,
    )
    assert_no_secret_material(pub)
    assert FAKE_KEY not in str(pub)
    assert FAKE_PASS not in str(pub)
    assert "secret_id" not in pub
    assert "encrypted_value" not in pub


@pytest.mark.asyncio
async def test_agent_handle_and_llm_context_have_no_secrets(db):
    from governance.ssh_credentials import (
        create_ssh_credential_ref,
        get_credential_handle,
        agent_capability_handle,
        assert_no_secret_material,
    )

    pub = await create_ssh_credential_ref(
        owner_id="user-a",
        tenant_id=None,
        name="k",
        auth_method="private_key",
        secret_plaintext=FAKE_KEY,
    )
    handle = await get_credential_handle("user-a", pub["id"])
    agent = agent_capability_handle(
        credential_ref_id=handle.credential_ref_id,
        auth_method=handle.auth_method,
        revoked=handle.revoked,
        connection_id="conn-1",
    )
    agent_dict = agent.to_agent_dict()
    llm = agent.to_llm_context()
    assert_no_secret_material(agent_dict)
    assert_no_secret_material(llm)
    assert FAKE_KEY not in llm
    assert "PRIVATE KEY" not in llm
    assert agent_dict["capability"] == "ucip:ssh.credential.use"
    assert "secret_id" not in agent_dict


@pytest.mark.asyncio
async def test_logs_do_not_contain_key_material(db, caplog):
    from governance.ssh_credentials import create_ssh_credential_ref, resolve_ssh_material

    with caplog.at_level(logging.INFO, logger="devos.ssh_credentials"):
        pub = await create_ssh_credential_ref(
            owner_id="user-a",
            tenant_id=None,
            name="k",
            auth_method="private_key",
            secret_plaintext=FAKE_KEY,
            passphrase_plaintext=FAKE_PASS,
        )
        mat = await resolve_ssh_material(owner_id="user-a", credential_ref_id=pub["id"])
        mat.clear()
    text = " ".join(r.getMessage() for r in caplog.records)
    assert FAKE_KEY not in text
    assert "BEGIN OPENSSH" not in text
    assert FAKE_PASS not in text


@pytest.mark.asyncio
async def test_exception_messages_are_safe_codes(db):
    from governance.ssh_credentials import (
        create_ssh_credential_ref,
        resolve_ssh_material,
        revoke_credential,
        SshCredentialDenied,
        SshCredentialRevoked,
        safe_error_dict,
    )

    pub = await create_ssh_credential_ref(
        owner_id="user-a",
        tenant_id=None,
        name="k",
        auth_method="private_key",
        secret_plaintext=FAKE_KEY,
    )
    with pytest.raises(SshCredentialDenied) as ei:
        await resolve_ssh_material(owner_id="user-b", credential_ref_id=pub["id"])
    assert FAKE_KEY not in str(ei.value)
    assert ei.value.code == "credential_not_found"
    assert FAKE_KEY not in str(safe_error_dict(ei.value))

    await revoke_credential("user-a", pub["id"])
    with pytest.raises(SshCredentialRevoked) as ei2:
        await resolve_ssh_material(owner_id="user-a", credential_ref_id=pub["id"])
    assert ei2.value.code == "credential_revoked"


@pytest.mark.asyncio
async def test_resolve_context_manager_clears_memory(db):
    from governance.ssh_credentials import create_ssh_credential_ref, resolve_and_use

    pub = await create_ssh_credential_ref(
        owner_id="user-a",
        tenant_id=None,
        name="k",
        auth_method="private_key",
        secret_plaintext=FAKE_KEY,
    )
    cm = await resolve_and_use(owner_id="user-a", credential_ref_id=pub["id"])
    async with cm as mat:
        assert mat.private_key_pem and "PRIVATE KEY" in mat.private_key_pem
    assert mat.private_key_pem is None
    assert mat._cleared is True


@pytest.mark.asyncio
async def test_cross_user_cannot_resolve_or_handle(db):
    from governance.ssh_credentials import (
        create_ssh_credential_ref,
        get_credential_handle,
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
    with pytest.raises(SshCredentialDenied):
        await get_credential_handle("attacker", pub["id"])
    with pytest.raises(SshCredentialDenied):
        await resolve_ssh_material(owner_id="attacker", credential_ref_id=pub["id"])


@pytest.mark.asyncio
async def test_password_auth_policy_gate(db, monkeypatch):
    from governance.ssh_credentials import (
        create_ssh_credential_ref,
        SshCredentialPolicyDenied,
    )

    monkeypatch.setattr(
        "governance.ssh_credentials._policy_allows_auth_method",
        lambda m: False if m == "password" else True,
    )
    with pytest.raises(SshCredentialPolicyDenied):
        await create_ssh_credential_ref(
            owner_id="user-a",
            tenant_id=None,
            name="pw",
            auth_method="password",
            secret_plaintext=FAKE_PASSWORD,
        )


@pytest.mark.asyncio
async def test_agent_forwarding_requires_explicit_method(db):
    from governance.ssh_credentials import create_ssh_credential_ref, resolve_ssh_material

    pub = await create_ssh_credential_ref(
        owner_id="user-a",
        tenant_id=None,
        name="agent",
        auth_method="agent_forwarding",
        secret_plaintext="",
    )
    mat = await resolve_ssh_material(owner_id="user-a", credential_ref_id=pub["id"])
    assert mat.agent_forwarding is True
    assert mat.private_key_pem is None
    assert mat.password is None
    mat.clear()


@pytest.mark.asyncio
async def test_audit_trail_records_access_without_secrets(db):
    from governance.ssh_credentials import (
        create_ssh_credential_ref,
        resolve_ssh_material,
        revoke_credential,
        get_access_audit,
        assert_no_secret_material,
    )

    pub = await create_ssh_credential_ref(
        owner_id="user-a",
        tenant_id=None,
        name="k",
        auth_method="private_key",
        secret_plaintext=FAKE_KEY,
    )
    mat = await resolve_ssh_material(
        owner_id="user-a", credential_ref_id=pub["id"], purpose="test"
    )
    mat.clear()
    await revoke_credential("user-a", pub["id"])
    audit = get_access_audit()
    events = {e["event"] for e in audit}
    assert "credential_created" in events
    assert "credential_resolved" in events
    assert "credential_revoked" in events
    assert_no_secret_material(audit)
    assert FAKE_KEY not in str(audit)


@pytest.mark.asyncio
async def test_sse_like_event_payload_safe(db):
    """Simulate SSE/analytics payload built from public handles only."""
    from governance.ssh_credentials import (
        create_ssh_credential_ref,
        get_credential_handle,
        agent_capability_handle,
        assert_no_secret_material,
    )

    pub = await create_ssh_credential_ref(
        owner_id="user-a",
        tenant_id=None,
        name="k",
        auth_method="private_key",
        secret_plaintext=FAKE_KEY,
    )
    handle = await get_credential_handle("user-a", pub["id"])
    event = {
        "type": "ssh.credential.bound",
        "data": agent_capability_handle(
            credential_ref_id=handle.credential_ref_id,
            auth_method=handle.auth_method,
        ).to_agent_dict(),
    }
    assert_no_secret_material(event)
    assert FAKE_KEY not in str(event)


@pytest.mark.asyncio
async def test_repr_and_str_of_resolved_material_safe(db):
    from governance.ssh_credentials import create_ssh_credential_ref, resolve_ssh_material

    pub = await create_ssh_credential_ref(
        owner_id="user-a",
        tenant_id=None,
        name="k",
        auth_method="private_key",
        secret_plaintext=FAKE_KEY,
        passphrase_plaintext=FAKE_PASS,
    )
    mat = await resolve_ssh_material(owner_id="user-a", credential_ref_id=pub["id"])
    for s in (repr(mat), str(mat)):
        assert FAKE_KEY not in s
        assert FAKE_PASS not in s
        assert "BEGIN" not in s
    mat.clear()


def test_scrub_authorization_headers():
    from governance.ssh_credentials import scrub_ssh_secrets_from_text

    raw = "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.aaa.bbb password=secret123"
    out = scrub_ssh_secrets_from_text(raw)
    assert "eyJhbGciOiJIUzI1NiJ9" not in out
    assert "secret123" not in out
