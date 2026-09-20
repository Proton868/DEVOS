"""Structured diagnostics, remote workflow, and SSH cancellation tests."""
from __future__ import annotations

import pytest

from governance.ssh_diagnostics import (
    DiagnosticCapability,
    run_diagnostic,
    list_diagnostic_capabilities,
)
from governance.ssh_remote_workflow import run_remote_diagnostic_workflow, WorkflowPhase
from governance import ssh_cancel
from execution.ssh_transport import SshTransportService, MockSshBackend
from governance.ssh_credentials import assert_no_secret_material


@pytest.fixture
async def db(tmp_path, monkeypatch):
    url = f"sqlite+aiosqlite:///{(tmp_path / 'diag.db').as_posix()}"
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("REQUIRE_POSTGRES", "false")
    monkeypatch.setenv("JWT_SECRET", "test-secret-key-for-ssh-vault-32chars!!")
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from core import database as dbmod

    dbmod.engine = create_async_engine(url, echo=False)
    dbmod.AsyncSessionLocal = async_sessionmaker(dbmod.engine, expire_on_commit=False)
    await dbmod.init_db()
    ssh_cancel.clear_for_tests()
    yield dbmod
    await dbmod.engine.dispose()


@pytest.fixture
async def conn(db):
    from governance.ssh_domain import create_connection
    from governance.ssh_host_verify import approve_new_host

    c = await create_connection(
        owner_id="user-a", hostname="diag.example", username="u",
        auth_method="agent_forwarding",
    )
    await approve_new_host(
        owner_id="user-a", tenant_id=None,
        host_identity_id=c["host_identity_id"],
        key_type="ssh-ed25519",
        fingerprint_sha256="SHA256:TESTFINGERPRINTAAAA",
        trust_state="pinned",
    )
    return c


def test_list_capabilities():
    caps = list_diagnostic_capabilities()
    assert "GetSystemInfo" in caps
    assert "GetDiskUsage" in caps
    assert "ListProcesses" in caps
    assert "GetListeningPorts" in caps


@pytest.mark.asyncio
async def test_structured_disk_and_memory(conn):
    transport = SshTransportService(backend=MockSshBackend(
        exec_stdout=(
            b"Filesystem Size Used Avail Use% Mounted\n"
            b"/dev/sda1 100G 95G 5G 95% /\n"
        )
    ))
    r = await run_diagnostic(
        owner_id="user-a", connection_id=conn["id"],
        capability=DiagnosticCapability.GET_DISK_USAGE,
        transport=transport,
    )
    assert r.status == "succeeded"
    assert r.structured.get("disks")
    assert_no_secret_material(r.to_public())


@pytest.mark.asyncio
async def test_workflow_observe_plan_report_no_blind_execute(conn):
    transport = SshTransportService(backend=MockSshBackend(
        exec_stdout=b"HEALTH_BEGIN\n up 1 day\n---\nMem 1000 500 500\n---\n/\n---\nrunning\nHEALTH_END\n"
    ))
    report = await run_remote_diagnostic_workflow(
        owner_id="user-a",
        connection_id=conn["id"],
        objective="Why is my DevOS server unhealthy?",
        transport=transport,
        # no approved remediation → observe/plan/report only
        approved_remediation_commands=None,
    )
    pub = report.to_public()
    assert pub["success"] is True
    assert pub["status"] == "completed"
    assert len(pub["observations"]) >= 3
    assert pub["plan"] is not None
    assert pub["executions"] == []  # no OBSERVE → arbitrary EXECUTE
    assert_no_secret_material(pub)


@pytest.mark.asyncio
async def test_workflow_stops_without_authorization(conn):
    transport = SshTransportService(backend=MockSshBackend())
    report = await run_remote_diagnostic_workflow(
        owner_id="user-a",
        connection_id=conn["id"],
        objective="Restart nginx on my server",
        transport=transport,
        approved_remediation_commands=["sudo systemctl restart nginx"],
        user_confirmed=False,  # not authorized
    )
    assert report.status == "failed"
    assert report.success is False
    assert "authorization" in report.outcome


@pytest.mark.asyncio
async def test_cancel_during_observe(conn):
    transport = SshTransportService(backend=MockSshBackend(exec_stdout=b"ok\n"))
    job_id = "job-cancel-observe"
    ssh_cancel.register_job(job_id, owner_id="user-a")
    ssh_cancel.request_cancel(job_id, reason="user_cancelled")

    report = await run_remote_diagnostic_workflow(
        owner_id="user-a",
        connection_id=conn["id"],
        objective="diagnose",
        transport=transport,
        job_id=job_id,
    )
    assert report.status == "cancelled"
    assert report.success is False
    assert report.phase == WorkflowPhase.CANCELLED.value
    # never success
    assert report.to_public()["success"] is False


@pytest.mark.asyncio
async def test_cancel_during_diagnostic_exec(conn):
    transport = SshTransportService(backend=MockSshBackend(exec_stdout=b"data\n"))
    job_id = "job-cancel-diag"
    ssh_cancel.register_job(job_id)
    ssh_cancel.request_cancel(job_id)

    r = await run_diagnostic(
        owner_id="user-a", connection_id=conn["id"],
        capability=DiagnosticCapability.GET_UPTIME,
        transport=transport,
        job_id=job_id,
    )
    assert r.status == "cancelled"
    assert r.status != "succeeded"


@pytest.mark.asyncio
async def test_file_transfer_cancel_still_works(db):
    from governance.ssh_domain import create_connection
    from governance.ssh_file_transfer import (
        governed_transfer, TransferRequest, TransferOp, request_cancel, clear_transfer_state_for_tests,
    )
    clear_transfer_state_for_tests()
    c = await create_connection(
        owner_id="user-a", hostname="t.example", username="u",
        auth_method="agent_forwarding",
    )
    tid = "xfer-cancel-1"
    request_cancel(tid)
    # list still works without cancel path mid-op; upload resume path tested elsewhere
    r = await governed_transfer(TransferRequest(
        owner_id="user-a", connection_id=c["id"],
        op=TransferOp.LIST, remote_path="/",
    ))
    assert r.status == "succeeded"


@pytest.mark.asyncio
async def test_cancel_status_never_success():
    ssh_cancel.register_job("j1")
    ssh_cancel.request_cancel("j1")
    out = ssh_cancel.cancel_status_result(job_id="j1", evidence_id="e1")
    assert out["status"] == "cancelled"
    assert out["success"] is False
