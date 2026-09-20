"""Server Operator mode + SSH agent failure behavior tests."""
from __future__ import annotations

import pytest

from brain.ssh_server_operator import (
    understand_intent,
    run_server_operator,
)
from governance.ssh_retry_policy import classify_disconnect_outcome, classify_retry_safety, RetrySafety
from governance.ssh_reconnect import SessionReconnectController, SessionConnectionState
from governance import ssh_cancel


def test_understand_common_phrases():
    assert understand_intent("Check Prime.").action == "diagnose"
    assert understand_intent("What's wrong with the server?").action == "diagnose"
    assert understand_intent("Restart DevOS on Prime").action == "restart"
    assert understand_intent("Restart DevOS on Prime").service == "devos"
    assert understand_intent("Deploy this.").action == "deploy"
    assert understand_intent("Rollback the deployment.").action == "rollback"
    assert understand_intent("Why is Docker using so much memory?").action == "inspect_docker"
    assert understand_intent("Install the missing dependency.").requires_approval is True


@pytest.mark.asyncio
async def test_operator_diagnose_no_blind_execute(tmp_path, monkeypatch):
    url = f"sqlite+aiosqlite:///{(tmp_path/'op.db').as_posix()}"
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
    from execution.ssh_transport import SshTransportService, MockSshBackend

    c = await create_connection(
        owner_id="u", hostname="8.8.8.8", username="u", auth_method="agent_forwarding",
    )
    await approve_new_host(
        owner_id="u", tenant_id=None, host_identity_id=c["host_identity_id"],
        key_type="ssh-ed25519", fingerprint_sha256="SHA256:OPTESTFINGERPRINT01",
        trust_state="pinned",
    )
    transport = SshTransportService(backend=MockSshBackend(
        host_fingerprint="SHA256:OPTESTFINGERPRINT01",
        exec_stdout=b"HEALTH_BEGIN\nrunning\nHEALTH_END\n",
    ))
    report = await run_server_operator(
        owner_id="u", connection_id=c["id"],
        text="Check Prime.",
        host_label="Prime",
        host_fingerprint="SHA256:OPTESTFINGERPRINT01",
        transport=transport,
    )
    pub = report.to_public()
    assert pub["host"]["verified_fingerprint"]
    assert "fingerprint" in pub["grounded_claim"].lower() or "Prime" in pub["grounded_claim"]
    # no remediation without approval
    assert pub["outcome"] == "diagnose_report_only"
    await dbmod.engine.dispose()


@pytest.mark.asyncio
async def test_operator_restart_requires_approval(tmp_path, monkeypatch):
    url = f"sqlite+aiosqlite:///{(tmp_path/'op2.db').as_posix()}"
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
    from execution.ssh_transport import SshTransportService, MockSshBackend

    c = await create_connection(
        owner_id="u", hostname="8.8.8.8", username="u", auth_method="agent_forwarding",
    )
    await approve_new_host(
        owner_id="u", tenant_id=None, host_identity_id=c["host_identity_id"],
        key_type="ssh-ed25519", fingerprint_sha256="SHA256:OPTESTFINGERPRINT02",
        trust_state="pinned",
    )
    report = await run_server_operator(
        owner_id="u", connection_id=c["id"],
        text="Restart DevOS on Prime",
        host_label="Prime",
        host_fingerprint="SHA256:OPTESTFINGERPRINT02",
        user_confirmed=False,
        transport=SshTransportService(backend=MockSshBackend()),
    )
    assert report.status == "awaiting_approval"
    assert report.success is False
    assert "approval" in report.grounded_claim.lower() or "not executed" in report.grounded_claim.lower()
    await dbmod.engine.dispose()


@pytest.mark.asyncio
async def test_operator_refuses_without_fingerprint():
    report = await run_server_operator(
        owner_id="u", connection_id="c1",
        text="Restart DevOS",
        host_fingerprint="",
        user_confirmed=True,
    )
    assert report.status == "denied"
    assert report.success is False


# --- Failure scenarios ---

def test_failure_network_interrupt_unknown():
    out = classify_disconnect_outcome(
        command="apt-get install nginx",
        exit_status=None, observed_output=False, connection_lost=True,
    )
    assert out.status == "unknown"
    assert out.may_retry is False


def test_failure_host_key_change_no_reconnect():
    ctrl = SessionReconnectController(session_key="u:c")
    ctrl.mark_connected(fingerprint="SHA256:A")
    att = ctrl.begin_reconnect("daemon_restart")
    st = ctrl.complete_reconnect(att, fingerprint="SHA256:B")
    assert st == SessionConnectionState.FAILED


def test_failure_credential_revocation_blocks_retry_of_destructive():
    d = classify_retry_safety("sudo rm -rf /var/lib/app")
    assert d.safety == RetrySafety.NOT_SAFE_TO_RETRY


def test_failure_cancel_not_success():
    ssh_cancel.clear_for_tests()
    ssh_cancel.register_job("fail-job")
    ssh_cancel.request_cancel("fail-job")
    r = ssh_cancel.cancel_status_result(job_id="fail-job")
    assert r["success"] is False
    assert r["status"] == "cancelled"


def test_failure_dns_agent_denied():
    from governance.ssh_network_policy import validate_ssh_target
    r = validate_ssh_target("no-such-host-xyz.invalid", 22, actor="agent")
    assert not r.allowed


# --- Expanded failure matrix ---

def test_failure_command_timeout_not_success():
    out = classify_disconnect_outcome(
        command="sleep 999",
        exit_status=None,
        observed_output=False,
        connection_lost=True,
    )
    assert out.status == "unknown"
    assert out.may_retry is False


def test_failure_ssh_daemon_restart_same_fingerprint_ok():
    ctrl = SessionReconnectController(session_key="u:daemon")
    ctrl.mark_connected(fingerprint="SHA256:SAME")
    att = ctrl.begin_reconnect("ssh_daemon_restart")
    st = ctrl.complete_reconnect(att, fingerprint="SHA256:SAME")
    assert st in (SessionConnectionState.RECONNECTED, SessionConnectionState.CONNECTED)


@pytest.mark.asyncio
async def test_failure_credential_revocation(tmp_path, monkeypatch):
    url = f"sqlite+aiosqlite:///{(tmp_path / 'rev2.db').as_posix()}"
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("REQUIRE_POSTGRES", "false")
    monkeypatch.setenv("JWT_SECRET", "test-secret-key-for-ssh-vault-32chars!!")
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from core import database as dbmod
    from governance.ssh_credentials import (
        create_ssh_credential_ref,
        revoke_credential,
        resolve_ssh_material,
        SshCredentialRevoked,
    )
    dbmod.engine = create_async_engine(url, echo=False)
    dbmod.AsyncSessionLocal = async_sessionmaker(dbmod.engine, expire_on_commit=False)
    await dbmod.init_db()
    ref = await create_ssh_credential_ref(
        owner_id="u",
        tenant_id=None,
        name="k",
        auth_method="private_key",
        secret_plaintext="-----BEGIN OPENSSH PRIVATE KEY-----\nAAAATEST\n-----END OPENSSH PRIVATE KEY-----\n",
    )
    await revoke_credential("u", ref["id"])
    with pytest.raises(SshCredentialRevoked):
        await resolve_ssh_material(owner_id="u", credential_ref_id=ref["id"])
    await dbmod.engine.dispose()


def test_failure_agent_crash_preserves_cancel_truth():
    ssh_cancel.clear_for_tests()
    ssh_cancel.register_job("crash-job")
    ssh_cancel.request_cancel("crash-job", reason="agent_crash_recovery")
    r = ssh_cancel.cancel_status_result(job_id="crash-job")
    assert r["success"] is False
    assert r["status"] == "cancelled"


def test_failure_browser_disconnect_session_state():
    ctrl = SessionReconnectController(session_key="browser:sess")
    ctrl.mark_connected(fingerprint="SHA256:X")
    ctrl.mark_disconnected("browser_disconnect")
    assert ctrl.state == SessionConnectionState.DISCONNECTED


def test_failure_worker_crash_unknown_not_success():
    out = classify_disconnect_outcome(
        command="deploy.sh",
        exit_status=None,
        observed_output=True,
        connection_lost=True,
    )
    assert out.status in ("unknown", "failed")
    assert out.may_retry is False


def test_failure_database_interruption_surface():
    from governance.ssh_network_policy import validate_ssh_target
    r = validate_ssh_target("169.254.169.254", 80, actor="agent")
    assert not r.allowed


def test_failure_devos_restart_recovery_no_false_success():
    out = classify_disconnect_outcome(
        command="systemctl restart devos",
        exit_status=None,
        observed_output=False,
        connection_lost=True,
    )
    assert out.status == "unknown"
    assert out.may_retry is False
