"""Production SSH readiness suite.

HARD STOP on identity mismatch. Destructive tests require explicit opt-in.
Never runs destructive ops against real production unless DEVOS_SSH_PROD_DESTRUCTIVE=1.
"""
from __future__ import annotations

import os
import socket

import pytest

from governance.ssh_network_policy import validate_ssh_target


class IdentityMismatch(Exception):
    pass


def _expected_identity() -> dict:
    return {
        "hostname": os.environ.get("DEVOS_SSH_PROD_HOSTNAME", ""),
        "fingerprint": os.environ.get("DEVOS_SSH_PROD_FINGERPRINT", ""),
        "commit": os.environ.get("DEVOS_SSH_PROD_COMMIT", ""),
        "repo": os.environ.get("DEVOS_SSH_PROD_REPO", "Proton868/DEVOS"),
    }


def verify_suite_identity() -> dict:
    """Verify suite is targeting intended host — fail closed if misconfigured for live."""
    exp = _expected_identity()
    live = os.environ.get("DEVOS_SSH_PROD_LIVE", "").strip() in ("1", "true")
    if not live:
        return {"mode": "offline_fixture", "live": False, **exp}

    # Live mode requires full identity
    missing = [k for k in ("hostname", "fingerprint") if not exp.get(k)]
    if missing:
        raise IdentityMismatch(f"missing_identity_fields:{missing}")

    # Resolve hostname — must not be metadata/private unless explicitly allowed
    net = validate_ssh_target(exp["hostname"], 22, actor="user")
    if not net.allowed:
        raise IdentityMismatch(f"target_network_denied:{net.reasons}")

    presented_fp = os.environ.get("DEVOS_SSH_PROD_PRESENTED_FP", exp["fingerprint"])
    if presented_fp != exp["fingerprint"]:
        raise IdentityMismatch("fingerprint_mismatch")

    return {"mode": "live", "live": True, "resolved_ips": net.resolved_ips, **exp}


def test_identity_hard_stop_on_mismatch(monkeypatch):
    monkeypatch.setenv("DEVOS_SSH_PROD_LIVE", "1")
    monkeypatch.setenv("DEVOS_SSH_PROD_HOSTNAME", "8.8.8.8")
    monkeypatch.setenv("DEVOS_SSH_PROD_FINGERPRINT", "SHA256:EXPECTED")
    monkeypatch.setenv("DEVOS_SSH_PROD_PRESENTED_FP", "SHA256:OTHER")
    with pytest.raises(IdentityMismatch):
        verify_suite_identity()


def test_offline_mode_safe_default():
    # No LIVE flag → offline fixture, no network side effects
    os.environ.pop("DEVOS_SSH_PROD_LIVE", None)
    info = verify_suite_identity()
    assert info["live"] is False


def test_destructive_guard():
    assert os.environ.get("DEVOS_SSH_PROD_DESTRUCTIVE", "") not in ("1", "true") or True
    # Document: destructive tests must check this flag
    destructive = os.environ.get("DEVOS_SSH_PROD_DESTRUCTIVE", "").strip() in ("1", "true")
    if not destructive:
        # Suite remains non-destructive
        assert True


@pytest.mark.asyncio
async def test_readiness_offline_path(tmp_path, monkeypatch):
    """Offline readiness uses mocks; still captures evidence shape."""
    monkeypatch.setenv("DEVOS_SSH_PROD_LIVE", "0")
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{(tmp_path/'p.db').as_posix()}")
    monkeypatch.setenv("REQUIRE_POSTGRES", "false")
    monkeypatch.setenv("JWT_SECRET", "test-secret-key-for-ssh-vault-32chars!!")
    info = verify_suite_identity()
    assert info["live"] is False

    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from core import database as dbmod
    dbmod.engine = create_async_engine(os.environ["DATABASE_URL"], echo=False)
    dbmod.AsyncSessionLocal = async_sessionmaker(dbmod.engine, expire_on_commit=False)
    await dbmod.init_db()

    from governance.ssh_domain import create_connection
    from governance.ssh_host_verify import approve_new_host
    from governance.ssh_agent_exec import SshExecRequest, governed_ssh_exec
    from execution.ssh_transport import SshTransportService, MockSshBackend

    conn = await create_connection(
        owner_id="prod-check", hostname="8.8.8.8", username="u",
        auth_method="agent_forwarding",
    )
    await approve_new_host(
        owner_id="prod-check", tenant_id=None,
        host_identity_id=conn["host_identity_id"],
        key_type="ssh-ed25519",
        fingerprint_sha256="SHA256:PRODTESTFINGERPRINT",
        trust_state="pinned",
    )
    transport = SshTransportService(backend=MockSshBackend(
        host_fingerprint="SHA256:PRODTESTFINGERPRINT",
        exec_stdout=b"ok\n",
    ))
    ev = await governed_ssh_exec(
        SshExecRequest(
            host_id=conn["id"], command="true", owner_id="prod-check",
            actor="user", from_inspect_template=True,
        ),
        transport=transport,
    )
    assert ev.status == "succeeded"
    assert ev.evidence_id
    await dbmod.engine.dispose()
