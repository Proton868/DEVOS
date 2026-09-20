"""SSRF network boundary, resource limits, and Git-over-SSH tests."""
from __future__ import annotations

import os

import pytest

from governance.ssh_network_policy import (
    validate_ssh_target,
    NetworkPolicyDecision,
    NetworkTargetClass,
)
from governance.ssh_resource_limits import (
    acquire_command_slot,
    release_command_slot,
    ResourceLimitExceeded,
    clamp_output,
    clamp_timeout,
    clear_resource_state_for_tests,
    DEFAULT_MAX_CONCURRENT_COMMANDS,
)
from governance.ssh_git import (
    parse_git_ssh_url,
    authorize_git_ssh,
    governed_git_ssh,
    GitSshRequest,
    GitSshOp,
    GitSshDenied,
)


def test_localhost_denied():
    r = validate_ssh_target("localhost", 22, actor="agent")
    assert not r.allowed
    assert r.target_class in (NetworkTargetClass.LOCALHOST, NetworkTargetClass.LOOPBACK)


def test_loopback_ip_denied():
    r = validate_ssh_target("127.0.0.1", 22, actor="user")
    assert not r.allowed


def test_metadata_ip_denied():
    r = validate_ssh_target("169.254.169.254", 80, actor="agent")
    assert not r.allowed
    assert r.target_class == NetworkTargetClass.METADATA


def test_metadata_hostname_denied():
    r = validate_ssh_target("metadata.google.internal", 80, actor="agent")
    assert not r.allowed


def test_unix_socket_denied():
    r = validate_ssh_target("/var/run/docker.sock", 0, actor="agent")
    assert not r.allowed


def test_rfc1918_denied_by_default():
    r = validate_ssh_target("10.0.0.5", 22, actor="agent")
    assert not r.allowed
    assert r.target_class == NetworkTargetClass.PRIVATE_RFC1918


def test_rfc1918_allowed_with_override(monkeypatch):
    monkeypatch.setenv("DEVOS_SSH_ALLOW_PRIVATE_NETWORKS", "1")
    r = validate_ssh_target("10.0.0.5", 22, actor="user")
    assert r.allowed


def test_link_local_denied():
    r = validate_ssh_target("169.254.1.1", 22, actor="agent")
    assert not r.allowed


def test_public_ip_allowed():
    r = validate_ssh_target("8.8.8.8", 22, actor="user")
    assert r.allowed
    assert r.target_class == NetworkTargetClass.PUBLIC


def test_agent_dns_fail_closed():
    r = validate_ssh_target("this-host-should-not-resolve-xyz.invalid", 22, actor="agent")
    assert not r.allowed


def test_resource_concurrent_limit():
    clear_resource_state_for_tests()
    owner = "limit-user"
    for _ in range(DEFAULT_MAX_CONCURRENT_COMMANDS):
        acquire_command_slot(owner)
    with pytest.raises(ResourceLimitExceeded):
        acquire_command_slot(owner)
    for _ in range(DEFAULT_MAX_CONCURRENT_COMMANDS):
        release_command_slot(owner)


def test_clamp_output_and_timeout():
    big = "x" * 1000
    out = clamp_output(big, limit=100)
    assert len(out) < 200
    assert "truncated" in out
    assert clamp_timeout(99999) <= 600
    assert clamp_timeout(0.1) >= 1.0


def test_parse_git_ssh_url():
    host, port, path, user = parse_git_ssh_url("git@github.com:org/repo.git")
    assert host == "github.com"
    assert path == "org/repo.git"
    assert user == "git"


def test_git_push_requires_confirmation():
    with pytest.raises(GitSshDenied):
        authorize_git_ssh(GitSshRequest(
            owner_id="u",
            op=GitSshOp.PUSH,
            remote_url="git@github.com:org/repo.git",
            actor="agent",
            user_confirmed=False,
            allowed_repos=["github.com/org/repo"],
        ))


def test_git_repo_allowlist():
    with pytest.raises(GitSshDenied):
        authorize_git_ssh(GitSshRequest(
            owner_id="u",
            op=GitSshOp.CLONE,
            remote_url="git@github.com:evil/repo.git",
            actor="user",
            allowed_repos=["github.com/org/repo"],
        ))


@pytest.mark.asyncio
async def test_governed_git_authorize_only():
    # Use public IP form to avoid DNS issues for network allow
    r = await governed_git_ssh(GitSshRequest(
        owner_id="u",
        op=GitSshOp.LS_REMOTE,
        remote_url="git@8.8.8.8:org/repo.git",
        actor="user",
        allowed_repos=["8.8.8.8/org/repo"],
    ))
    assert r.status in ("authorized", "denied")
    # 8.8.8.8 is public — should authorize
    assert r.status == "authorized"
    assert "PRIVATE KEY" not in str(r.to_public())


def test_git_metadata_remote_denied():
    with pytest.raises(GitSshDenied):
        authorize_git_ssh(GitSshRequest(
            owner_id="u",
            op=GitSshOp.FETCH,
            remote_url="git@169.254.169.254:repo.git",
            actor="agent",
        ))
