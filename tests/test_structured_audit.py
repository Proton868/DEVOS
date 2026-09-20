"""Structured audit trail tests."""
from __future__ import annotations

import json
import logging

import pytest

from governance.structured_audit import (
    emit_audit_event,
    audit_ssh_exec,
    audit_ssh_credential_access,
    audit_ssh_cancel,
    audit_ssh_diagnostic,
    audit_ssh_transfer,
    audit_ssh_host_verify,
    _scrub,
    set_audit_context,
    clear_audit_context,
)
from governance.audit import AuditEventType


def test_scrub_redacts_secrets():
    payload = {
        "command": "ls",
        "password": "s3cret",
        "nested": {"private_key": "BEGIN PRIVATE KEY\nabc\n"},
        "ok": "visible",
    }
    out = _scrub(payload)
    assert out["password"] == "[REDACTED]"
    assert out["nested"]["private_key"] == "[REDACTED]"
    assert out["ok"] == "visible"
    assert "BEGIN PRIVATE" not in json.dumps(out)


def test_emit_audit_event_structure(caplog):
    clear_audit_context()
    set_audit_context(trace_id="trace-1", actor_id="user-a")
    with caplog.at_level(logging.INFO, logger="devos.structured_audit"):
        rec = emit_audit_event(
            action="test.action",
            result="succeeded",
            event_type=AuditEventType.SYSTEM,
            resource="test",
            resource_id="r1",
            details={"password": "nope", "count": 3},
            persist=False,  # avoid DB dependency
        )
    assert rec["event_id"]
    assert rec["action"] == "test.action"
    assert rec["result"] == "succeeded"
    assert rec["actor_id"] == "user-a"
    assert rec["trace_id"] == "trace-1"
    assert rec["details"]["password"] == "[REDACTED]"
    assert rec["details"]["count"] == 3
    # JSON line in logs
    assert any("audit_event" in r.message for r in caplog.records)
    clear_audit_context()


def test_ssh_emitters_no_secrets():
    r = audit_ssh_exec(
        actor_id="u1",
        connection_id="c1",
        command="echo hello",
        status="succeeded",
        risk_class="read_only",
        evidence_id="e1",
        host_identity={"fingerprint_sha256": "SHA256:ABC", "key_type": "ssh-ed25519"},
        duration_ms=12,
    )
    assert r["action"] == "ssh.exec"
    assert "PRIVATE" not in json.dumps(r)

    r2 = audit_ssh_credential_access(
        actor_id="u1", credential_ref_id="cred-1", result="resolved",
    )
    assert r2["action"] == "ssh.credential.resolve"
    assert "private_key" not in json.dumps(r2)

    r3 = audit_ssh_cancel(actor_id="u1", job_id="j1", reason="user_cancelled")
    assert r3["result"] == "cancelled"
    assert r3["details"].get("success") is False

    r4 = audit_ssh_diagnostic(
        actor_id="u1", connection_id="c1", capability="GetDiskUsage", status="succeeded",
    )
    assert r4["action"] == "ssh.diagnostic"

    r5 = audit_ssh_transfer(
        actor_id="u1", connection_id="c1", op="upload", status="succeeded",
        remote_path="/tmp/a", bytes_transferred=10,
    )
    assert r5["action"] == "ssh.transfer"

    r6 = audit_ssh_host_verify(
        actor_id="u1", host_identity_id="h1", state="KNOWN_HOST", result="allowed",
        fingerprint_sha256="SHA256:XYZ",
    )
    assert r6["action"] == "ssh.host_verify"


@pytest.mark.asyncio
async def test_exec_emits_audit(tmp_path, monkeypatch, caplog):
    url = f"sqlite+aiosqlite:///{(tmp_path / 'aud.db').as_posix()}"
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("REQUIRE_POSTGRES", "false")
    monkeypatch.setenv("JWT_SECRET", "test-secret-key-for-ssh-vault-32chars!!")
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from core import database as dbmod

    dbmod.engine = create_async_engine(url, echo=False)
    dbmod.AsyncSessionLocal = async_sessionmaker(dbmod.engine, expire_on_commit=False)
    await dbmod.init_db()

    from governance.ssh_domain import create_connection
    from governance.ssh_host_verify import approve_new_host
    from governance.ssh_agent_exec import SshExecRequest, governed_ssh_exec
    from execution.ssh_transport import SshTransportService, MockSshBackend

    c = await create_connection(
        owner_id="user-a", hostname="a.example", username="u",
        auth_method="agent_forwarding",
    )
    await approve_new_host(
        owner_id="user-a", tenant_id=None,
        host_identity_id=c["host_identity_id"],
        key_type="ssh-ed25519",
        fingerprint_sha256="SHA256:TESTFINGERPRINTAAAA",
        trust_state="pinned",
    )
    transport = SshTransportService(backend=MockSshBackend(exec_stdout=b"ok\n"))
    with caplog.at_level(logging.INFO, logger="devos.structured_audit"):
        ev = await governed_ssh_exec(
            SshExecRequest(
                host_id=c["id"],
                command="uname -a",
                owner_id="user-a",
                actor="agent",
                from_inspect_template=True,
            ),
            transport=transport,
        )
    assert ev.status == "succeeded"
    assert any("ssh.exec" in r.message for r in caplog.records)
    await dbmod.engine.dispose()
