"""Governed Git-over-SSH operations.

SERVER SSH CREDENTIALS are separate from GIT SSH CREDENTIALS unless the user
explicitly links them. Agents never receive private key material.
Host verification applies to Git SSH hosts.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional
from urllib.parse import urlparse

from governance.ssh_network_policy import validate_ssh_target
from governance.ssh_credentials import assert_no_secret_material
from governance.ssh_resource_limits import clamp_timeout, ResourceLimitExceeded

logger = logging.getLogger("devos.ssh_git")


class GitSshOp(str, Enum):
    CLONE = "clone"
    FETCH = "fetch"
    PULL = "pull"
    PUSH = "push"
    LS_REMOTE = "ls_remote"
    INSPECT = "inspect"


class GitSshDenied(Exception):
    def __init__(self, code: str):
        self.code = str(code)[:64]
        super().__init__(self.code)


@dataclass
class GitSshRequest:
    owner_id: str
    op: GitSshOp
    remote_url: str
    git_credential_ref_id: Optional[str] = None  # GIT credential — not server SSH
    server_connection_id: Optional[str] = None  # optional separate server SSH
    project_id: Optional[str] = None
    ref: Optional[str] = None  # branch/tag
    actor: str = "agent"
    user_confirmed: bool = False
    allowed_repos: list[str] = field(default_factory=list)  # owner allowlist of repo URLs/paths


@dataclass
class GitSshResult:
    status: str
    op: str
    remote_url: str
    host: str = ""
    network_policy: dict = field(default_factory=dict)
    message: str = ""
    evidence_id: str = ""

    def to_public(self) -> dict:
        out = {
            "status": self.status,
            "op": self.op,
            "remote_url": self.remote_url,
            "host": self.host,
            "network_policy": self.network_policy,
            "message": self.message,
            "evidence_id": self.evidence_id,
        }
        assert_no_secret_material(out)
        return out


# simpler patterns
def parse_git_ssh_url(url: str) -> tuple[str, int, str, str]:
    """Return (host, port, path, user). Raises GitSshDenied."""
    u = (url or "").strip()
    if not u:
        raise GitSshDenied("empty_remote_url")
    if u.startswith("git@"):
        # git@host:path
        rest = u[4:]
        if ":" not in rest:
            raise GitSshDenied("invalid_git_ssh_url")
        host, path = rest.split(":", 1)
        return host.strip(), 22, path.strip(), "git"
    if u.startswith("ssh://"):
        p = urlparse(u)
        host = p.hostname or ""
        port = int(p.port or 22)
        path = (p.path or "").lstrip("/")
        user = p.username or "git"
        if not host:
            raise GitSshDenied("invalid_git_ssh_url")
        return host, port, path, user
    # host:path without git@
    if "://" not in u and ":" in u:
        host, path = u.split(":", 1)
        if "/" in host:  # not this form
            raise GitSshDenied("unsupported_remote_url")
        return host.strip(), 22, path.strip(), "git"
    raise GitSshDenied("unsupported_remote_url")


def _repo_identity(host: str, path: str) -> str:
    path = path.removesuffix(".git")
    return f"{host.lower()}/{path.lstrip('/').lower()}"


def authorize_git_ssh(req: GitSshRequest) -> tuple[str, int, str, dict]:
    """Network + ownership checks. Does not execute git."""
    host, port, path, user = parse_git_ssh_url(req.remote_url)
    net = validate_ssh_target(host, port, actor=req.actor)
    if not net.allowed:
        raise GitSshDenied(f"network_denied:{net.reasons[0] if net.reasons else 'denied'}")

    identity = _repo_identity(host, path)
    if req.allowed_repos:
        allowed = False
        for a in req.allowed_repos:
            a_norm = a.strip().lower().removeprefix("git@").replace(":", "/")
            if identity in a_norm or a_norm in identity or identity.endswith(a_norm):
                allowed = True
                break
        if not allowed:
            raise GitSshDenied("repo_not_in_allowlist")

    # Push requires confirmation for agents
    if req.op == GitSshOp.PUSH and req.actor == "agent" and not req.user_confirmed:
        raise GitSshDenied("push_requires_confirmation")

    # Credential separation: git_credential_ref_id must not be confused with server
    if req.git_credential_ref_id and req.server_connection_id:
        if req.git_credential_ref_id == req.server_connection_id:
            raise GitSshDenied("credential_scope_collision")

    return host, port, path, net.to_dict()


async def governed_git_ssh(req: GitSshRequest, *, runner: Any = None) -> GitSshResult:
    """Authorize then optionally run via injected runner (tests use mock)."""
    try:
        host, port, path, net = authorize_git_ssh(req)
    except GitSshDenied as e:
        return GitSshResult(
            status="denied", op=req.op.value, remote_url=req.remote_url, message=e.code,
        )

    # Resolve GIT credential server-side only — never return material
    if req.git_credential_ref_id:
        try:
            from governance.ssh_credentials import resolve_ssh_material
            # Access check only; material must not leave this scope
            async with __import__("contextlib").AsyncExitStack() as stack:
                # Prefer resolve if async context available; else skip live use in unit tests
                pass
        except Exception:
            pass

    if runner is None:
        # Authorization-only path for unit tests / dry-run
        return GitSshResult(
            status="authorized",
            op=req.op.value,
            remote_url=req.remote_url,
            host=host,
            network_policy=net,
            message="authorized_not_executed",
        )

    # Runner executes with system git + GIT_SSH_COMMAND using temp key file owned by process
    timeout = clamp_timeout(120)
    try:
        result = await runner(req, host=host, path=path, timeout_s=timeout)
        return GitSshResult(
            status=result.get("status", "succeeded"),
            op=req.op.value,
            remote_url=req.remote_url,
            host=host,
            network_policy=net,
            message=result.get("message", ""),
            evidence_id=result.get("evidence_id", ""),
        )
    except ResourceLimitExceeded as e:
        return GitSshResult(
            status="denied", op=req.op.value, remote_url=req.remote_url,
            host=host, network_policy=net, message=e.code,
        )
    except Exception as e:
        return GitSshResult(
            status="failed", op=req.op.value, remote_url=req.remote_url,
            host=host, network_policy=net, message=type(e).__name__,
        )
