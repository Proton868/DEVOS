"""SSH execution resource governance — keep the control plane responsive.

Limits apply to agentic and user remote jobs. Remote host is untrusted.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger("devos.ssh_resource_limits")

# Defaults (overridable via env)
DEFAULT_COMMAND_TIMEOUT_S = float(os.environ.get("DEVOS_SSH_COMMAND_TIMEOUT_S", "120"))
DEFAULT_CONNECT_TIMEOUT_S = float(os.environ.get("DEVOS_SSH_CONNECT_TIMEOUT_S", "30"))
DEFAULT_MAX_STDOUT_BYTES = int(os.environ.get("DEVOS_SSH_MAX_STDOUT_BYTES", str(2 * 1024 * 1024)))
DEFAULT_MAX_STDERR_BYTES = int(os.environ.get("DEVOS_SSH_MAX_STDERR_BYTES", str(512 * 1024)))
DEFAULT_MAX_TRANSFER_BYTES = int(os.environ.get("DEVOS_SSH_MAX_TRANSFER_BYTES", str(50 * 1024 * 1024)))
DEFAULT_MAX_SESSIONS_PER_OWNER = int(os.environ.get("DEVOS_SSH_MAX_SESSIONS_PER_OWNER", "8"))
DEFAULT_MAX_CONCURRENT_COMMANDS = int(os.environ.get("DEVOS_SSH_MAX_CONCURRENT_CMDS", "4"))
DEFAULT_MAX_JOBS_PER_OWNER = int(os.environ.get("DEVOS_SSH_MAX_JOBS_PER_OWNER", "16"))
DEFAULT_RATE_PER_MINUTE = int(os.environ.get("DEVOS_SSH_RATE_PER_MINUTE", "30"))
DEFAULT_MAX_EVIDENCE_BYTES = int(os.environ.get("DEVOS_SSH_MAX_EVIDENCE_BYTES", str(256 * 1024)))


class ResourceLimitExceeded(Exception):
    def __init__(self, code: str):
        self.code = str(code)[:64]
        super().__init__(self.code)


@dataclass
class ResourceBudget:
    command_timeout_s: float = DEFAULT_COMMAND_TIMEOUT_S
    connect_timeout_s: float = DEFAULT_CONNECT_TIMEOUT_S
    max_stdout_bytes: int = DEFAULT_MAX_STDOUT_BYTES
    max_stderr_bytes: int = DEFAULT_MAX_STDERR_BYTES
    max_transfer_bytes: int = DEFAULT_MAX_TRANSFER_BYTES
    max_evidence_bytes: int = DEFAULT_MAX_EVIDENCE_BYTES


_lock = threading.Lock()
_sessions: dict[str, int] = {}  # owner -> count
_commands: dict[str, int] = {}  # owner -> concurrent cmds
_jobs: dict[str, int] = {}
_rate: dict[str, list[float]] = {}  # owner -> timestamps


def _owner_key(owner_id: str) -> str:
    return (owner_id or "anonymous").strip() or "anonymous"


def acquire_session_slot(owner_id: str) -> None:
    k = _owner_key(owner_id)
    with _lock:
        n = _sessions.get(k, 0)
        if n >= DEFAULT_MAX_SESSIONS_PER_OWNER:
            raise ResourceLimitExceeded("max_sessions")
        _sessions[k] = n + 1


def release_session_slot(owner_id: str) -> None:
    k = _owner_key(owner_id)
    with _lock:
        n = _sessions.get(k, 0)
        _sessions[k] = max(0, n - 1)


def acquire_command_slot(owner_id: str) -> None:
    k = _owner_key(owner_id)
    with _lock:
        # rate limit
        now = time.monotonic()
        window = _rate.setdefault(k, [])
        _rate[k] = [t for t in window if now - t < 60.0]
        if len(_rate[k]) >= DEFAULT_RATE_PER_MINUTE:
            raise ResourceLimitExceeded("rate_limit")
        n = _commands.get(k, 0)
        if n >= DEFAULT_MAX_CONCURRENT_COMMANDS:
            raise ResourceLimitExceeded("max_concurrent_commands")
        jobs = _jobs.get(k, 0)
        if jobs >= DEFAULT_MAX_JOBS_PER_OWNER:
            raise ResourceLimitExceeded("max_jobs")
        _commands[k] = n + 1
        _jobs[k] = jobs + 1
        _rate[k].append(now)


def release_command_slot(owner_id: str) -> None:
    k = _owner_key(owner_id)
    with _lock:
        _commands[k] = max(0, _commands.get(k, 0) - 1)
        _jobs[k] = max(0, _jobs.get(k, 0) - 1)


def clamp_output(data: bytes | str, *, limit: int) -> bytes | str:
    if isinstance(data, bytes):
        if len(data) <= limit:
            return data
        return data[:limit] + b"\n[truncated:output_limit]"
    if len(data) <= limit:
        return data
    return data[:limit] + "\n[truncated:output_limit]"


def clamp_timeout(requested: Optional[float], *, default: float = DEFAULT_COMMAND_TIMEOUT_S) -> float:
    t = float(requested if requested is not None else default)
    # hard ceiling 10 minutes
    return max(1.0, min(t, 600.0))


def clear_resource_state_for_tests() -> None:
    with _lock:
        _sessions.clear()
        _commands.clear()
        _jobs.clear()
        _rate.clear()
