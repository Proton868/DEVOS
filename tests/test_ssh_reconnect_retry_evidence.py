"""SSH reconnect, retry safety, and EvidenceChain integration tests."""
from __future__ import annotations

import pytest

from governance.ssh_reconnect import (
    SessionReconnectController,
    SessionConnectionState,
    DisconnectReason,
    clear_controllers_for_tests,
)
from governance.ssh_retry_policy import (
    classify_retry_safety,
    classify_disconnect_outcome,
    authorize_retry_after_inspect,
    RetrySafety,
)
from governance.ssh_evidence import (
    record_ssh_operation_evidence,
    clear_chains_for_tests,
    get_or_create_chain,
)
from governance.ssh_credentials import assert_no_secret_material


def setup_function():
    clear_controllers_for_tests()
    clear_chains_for_tests()


def test_reconnect_states_and_host_change_block():
    ctrl = SessionReconnectController(session_key="u:c1")
    ctrl.mark_connected(fingerprint="SHA256:AAA", key_type="ssh-ed25519")
    assert ctrl.state == SessionConnectionState.CONNECTED

    ctrl.mark_disconnected(DisconnectReason.NETWORK_LOSS)
    assert ctrl.state == SessionConnectionState.DISCONNECTED

    att = ctrl.begin_reconnect(DisconnectReason.NETWORK_LOSS)
    assert ctrl.state == SessionConnectionState.RECONNECTING

    # Same fingerprint → RECONNECTED
    st = ctrl.complete_reconnect(att, fingerprint="SHA256:AAA", key_type="ssh-ed25519")
    assert st == SessionConnectionState.RECONNECTED
    assert ctrl.state == SessionConnectionState.CONNECTED

    # Changed host → FAILED, never silent reconnect
    ctrl.mark_disconnected(DisconnectReason.TCP_RESET)
    att2 = ctrl.begin_reconnect(DisconnectReason.TCP_RESET)
    st2 = ctrl.complete_reconnect(att2, fingerprint="SHA256:EVIL", key_type="ssh-ed25519")
    assert st2 == SessionConnectionState.FAILED
    assert att2.error == "host_identity_changed"
    assert att2.host_identity_ok is False


def test_retry_safety_classes():
    assert classify_retry_safety("ls -la").safety == RetrySafety.SAFE_TO_RETRY
    assert classify_retry_safety("apt-get install nginx").safety == RetrySafety.CONDITIONALLY_SAFE
    assert classify_retry_safety("rm -rf /").safety == RetrySafety.NOT_SAFE_TO_RETRY
    assert classify_retry_safety("systemctl restart nginx").safety == RetrySafety.CONDITIONALLY_SAFE
    d = classify_retry_safety("mystery-binary --do-stuff")
    assert d.safety == RetrySafety.NOT_SAFE_TO_RETRY
    assert d.allow_retry is False


def test_disconnect_unknown_outcome_no_blind_retry():
    out = classify_disconnect_outcome(
        command="apt-get install nginx",
        exit_status=None,
        observed_output=False,
        connection_lost=True,
    )
    assert out.status == "unknown"
    assert out.may_retry is False
    assert out.retry_decision.safety == RetrySafety.CONDITIONALLY_SAFE


@pytest.mark.asyncio
async def test_conditional_retry_after_inspect():
    d = classify_retry_safety("systemctl restart nginx")
    assert d.allow_retry is False
    d2 = await authorize_retry_after_inspect(
        d, remote_state_matches_desired=True, already_completed=True,
    )
    assert d2.allow_retry is False
    d3 = classify_retry_safety("systemctl restart nginx")
    d3 = await authorize_retry_after_inspect(
        d3, remote_state_matches_desired=False, already_completed=False,
    )
    assert d3.allow_retry is True


def test_evidence_chain_record_and_grounding():
    summary = record_ssh_operation_evidence(
        actor_id="user-a",
        user_id="user-a",
        agent="nuha",
        host="prod.example",
        connection_id="c1",
        verified_fingerprint="SHA256:ABC",
        credential_ref_id="cred-1",
        capability="ssh.exec",
        command="systemctl status nginx",
        duration_ms=40,
        exit_status=0,
        stdout="active (running)",
        stderr="",
        policy_decision="allowed",
        verification_result="passed",
        resulting_state="service_active",
        status="succeeded",
    )
    assert_no_secret_material(summary)
    assert "PRIVATE" not in str(summary)
    assert summary["evidence"]["host_fingerprint_verified"] is True
    assert "verification passed" in summary["grounded_claim"].lower() or "completed" in summary["grounded_claim"].lower()
    chain = get_or_create_chain(summary["chain_id"])
    assert len(chain.nodes) >= 1


def test_evidence_unknown_not_fabricated_success():
    summary = record_ssh_operation_evidence(
        actor_id="user-a",
        command="apt-get install x",
        status="unknown",
        exit_status=None,
        verified_fingerprint="SHA256:ABC",
    )
    claim = summary["grounded_claim"].lower()
    assert "unknown" in claim or "do not assume" in claim
    assert "succeeded" not in claim


@pytest.mark.asyncio
async def test_exec_attaches_evidence_chain(tmp_path, monkeypatch):
    url = f"sqlite+aiosqlite:///{(tmp_path / 'ev.db').as_posix()}"
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("REQUIRE_POSTGRES", "false")
    monkeypatch.setenv("JWT_SECRET", "test-secret-key-for-ssh-vault-32chars!!")
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from core import database as dbmod

    dbmod.engine = create_async_engine(url, echo=False)
    dbmod.AsyncSessionLocal = async_sessionmaker(dbmod.engine, expire_on_commit=False)
    await dbmod.init_db()
    clear_chains_for_tests()

    from governance.ssh_domain import create_connection
    from governance.ssh_host_verify import approve_new_host
    from governance.ssh_agent_exec import SshExecRequest, governed_ssh_exec
    from execution.ssh_transport import SshTransportService, MockSshBackend

    c = await create_connection(
        owner_id="user-a", hostname="e.example", username="u",
        auth_method="agent_forwarding",
    )
    await approve_new_host(
        owner_id="user-a", tenant_id=None,
        host_identity_id=c["host_identity_id"],
        key_type="ssh-ed25519",
        fingerprint_sha256="SHA256:TESTFINGERPRINTAAAA",
        trust_state="pinned",
    )
    ev = await governed_ssh_exec(
        SshExecRequest(
            host_id=c["id"], command="uname -a", owner_id="user-a",
            actor="agent", from_inspect_template=True,
        ),
        transport=SshTransportService(backend=MockSshBackend(exec_stdout=b"Linux\n")),
    )
    assert ev.status == "succeeded"
    chain_meta = (ev.policy or {}).get("evidence_chain") or {}
    assert chain_meta.get("chain_id")
    assert chain_meta.get("grounded_claim")
    await dbmod.engine.dispose()
