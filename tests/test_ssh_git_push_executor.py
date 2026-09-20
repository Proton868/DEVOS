"""Git-over-SSH push executor tests.

Covers authorization gates + injected runner execution without key leakage.
"""
from __future__ import annotations

import pytest

from governance.ssh_git import (
    GitSshOp,
    GitSshRequest,
    GitSshDenied,
    authorize_git_ssh,
    governed_git_ssh,
    MockGitSshRunner,
)
from governance.ssh_credentials import assert_no_secret_material
from governance import ssh_cancel


REMOTE = "git@8.8.8.8:org/allowed-repo.git"
ALLOW = ["8.8.8.8/org/allowed-repo"]


def test_push_denied_without_user_confirmation():
    with pytest.raises(GitSshDenied) as ei:
        authorize_git_ssh(GitSshRequest(
            owner_id="user-a",
            op=GitSshOp.PUSH,
            remote_url=REMOTE,
            actor="agent",
            user_confirmed=False,
            allowed_repos=ALLOW,
        ))
    assert ei.value.code == "push_requires_confirmation"


def test_push_authorized_with_confirmation():
    host, port, path, net = authorize_git_ssh(GitSshRequest(
        owner_id="user-a",
        op=GitSshOp.PUSH,
        remote_url=REMOTE,
        actor="agent",
        user_confirmed=True,
        allowed_repos=ALLOW,
        git_credential_ref_id="git-cred-1",
    ))
    assert host == "8.8.8.8"
    assert "allowed-repo" in path
    assert net.get("host_verification", {}).get("required") is True


@pytest.mark.asyncio
async def test_push_executor_success_no_key_material():
    runner = MockGitSshRunner()
    result = await governed_git_ssh(
        GitSshRequest(
            owner_id="user-a",
            op=GitSshOp.PUSH,
            remote_url=REMOTE,
            actor="agent",
            user_confirmed=True,
            allowed_repos=ALLOW,
            git_credential_ref_id="git-cred-1",
            server_connection_id="server-conn-9",  # different scope
        ),
        runner=runner,
    )
    assert result.status == "succeeded"
    assert result.op == "push"
    assert "pushed_to" in result.message
    assert result.evidence_id == "ev-push-ok"
    pub = result.to_public()
    assert_no_secret_material(pub)
    assert "PRIVATE" not in str(pub)
    assert "BEGIN" not in str(pub)
    # Runner saw credential *ref id* only
    assert runner.calls[0]["git_credential_ref_id"] == "git-cred-1"
    assert runner.calls[0]["server_connection_id"] == "server-conn-9"
    assert "private_key" not in runner.calls[0]
    assert "pem" not in str(runner.calls[0]).lower()


@pytest.mark.asyncio
async def test_push_executor_remote_failure():
    runner = MockGitSshRunner(fail=True)
    result = await governed_git_ssh(
        GitSshRequest(
            owner_id="user-a",
            op=GitSshOp.PUSH,
            remote_url=REMOTE,
            actor="user",
            user_confirmed=True,
            allowed_repos=ALLOW,
        ),
        runner=runner,
    )
    assert result.status == "failed"
    assert result.message == "remote_rejected"


@pytest.mark.asyncio
async def test_push_unauthorized_repo_never_invokes_runner():
    runner = MockGitSshRunner()
    result = await governed_git_ssh(
        GitSshRequest(
            owner_id="user-a",
            op=GitSshOp.PUSH,
            remote_url="git@8.8.8.8:evil/other.git",
            actor="agent",
            user_confirmed=True,
            allowed_repos=ALLOW,
        ),
        runner=runner,
    )
    assert result.status == "denied"
    assert "allowlist" in result.message or result.message.startswith("repo_")
    assert runner.calls == []


@pytest.mark.asyncio
async def test_push_without_confirmation_never_invokes_runner():
    runner = MockGitSshRunner()
    result = await governed_git_ssh(
        GitSshRequest(
            owner_id="user-a",
            op=GitSshOp.PUSH,
            remote_url=REMOTE,
            actor="agent",
            user_confirmed=False,
            allowed_repos=ALLOW,
        ),
        runner=runner,
    )
    assert result.status == "denied"
    assert result.message == "push_requires_confirmation"
    assert runner.calls == []


@pytest.mark.asyncio
async def test_push_cancelled_before_executor_completes():
    ssh_cancel.clear_for_tests()
    job_id = "git-push-job-1"
    ssh_cancel.register_job(job_id, owner_id="user-a", kind="git_push")
    ssh_cancel.request_cancel(job_id)

    runner = MockGitSshRunner(delay_cancel_job_id=job_id)
    result = await governed_git_ssh(
        GitSshRequest(
            owner_id="user-a",
            op=GitSshOp.PUSH,
            remote_url=REMOTE,
            actor="user",
            user_confirmed=True,
            allowed_repos=ALLOW,
        ),
        runner=runner,
    )
    assert result.status == "cancelled"
    # cancellation is not success
    assert result.status != "succeeded"
    status = ssh_cancel.cancel_status_result(job_id=job_id)
    assert status["success"] is False


@pytest.mark.asyncio
async def test_push_metadata_host_denied_no_runner():
    runner = MockGitSshRunner()
    result = await governed_git_ssh(
        GitSshRequest(
            owner_id="user-a",
            op=GitSshOp.PUSH,
            remote_url="git@169.254.169.254:repo.git",
            actor="agent",
            user_confirmed=True,
            allowed_repos=["169.254.169.254/repo"],
        ),
        runner=runner,
    )
    assert result.status == "denied"
    assert runner.calls == []


@pytest.mark.asyncio
async def test_clone_fetch_pull_with_runner():
    runner = MockGitSshRunner()
    for op in (GitSshOp.CLONE, GitSshOp.FETCH, GitSshOp.PULL, GitSshOp.LS_REMOTE):
        r = await governed_git_ssh(
            GitSshRequest(
                owner_id="user-a",
                op=op,
                remote_url=REMOTE,
                actor="user",
                allowed_repos=ALLOW,
                git_credential_ref_id="git-cred-2",
            ),
            runner=runner,
        )
        assert r.status == "succeeded"
        assert r.op == op.value
    assert len(runner.calls) == 4
    for c in runner.calls:
        assert "private_key" not in c


def test_credential_scope_collision_blocks_push():
    with pytest.raises(GitSshDenied):
        authorize_git_ssh(GitSshRequest(
            owner_id="user-a",
            op=GitSshOp.PUSH,
            remote_url=REMOTE,
            actor="user",
            user_confirmed=True,
            allowed_repos=ALLOW,
            git_credential_ref_id="shared-id",
            server_connection_id="shared-id",
        ))
