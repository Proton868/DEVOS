"""Fail-closed OS execution isolation with explicit strength classification.

UCIP/authority decides *whether* a capability may run.
This module decides *how strongly* the process is isolated from the host.

Strength ladder (actual guarantees — do not inflate):

  strong       Docker with hardened flags (when DEVOS_USE_DOCKER_SANDBOX=1)
  restricted   bubblewrap or firejail (FS + net constraints, not a full container profile)
  network_only unshare --net only — NOT sufficient for untrusted code
  degraded     host process, stripped env (DEVOS_ALLOW_DEGRADED_ISOLATION=1 only)
  none         no backend available

Untrusted workflow code (policy=untrusted) requires strength in {strong, restricted}.
network_only and degraded are DENIED for untrusted code.
"""
from __future__ import annotations

import asyncio
import logging
import os
import shutil
import tempfile
import time
from dataclasses import dataclass, asdict
from enum import Enum
from typing import Optional

logger = logging.getLogger("devos.isolation")


class IsolationStrength(str, Enum):
    NONE = "none"
    DEGRADED = "degraded"
    NETWORK_ONLY = "network_only"
    RESTRICTED = "restricted"
    STRONG = "strong"


# Back-compat alias used by older callers
class IsolationLevel(str, Enum):
    ISOLATED = "isolated"
    DEGRADED = "degraded"
    UNSAFE = "unsafe"


# Policies for callers (canonical execution trust)
POLICY_TRUSTED = "trusted"       # developer/local/governed workspace — weaker isolation only if configured
POLICY_UNTRUSTED = "untrusted"   # AI-generated, uploaded, arbitrary repo, untrusted project cmds
POLICY_PRIVILEGED = "privileged" # host/system, credentials, unrestricted net, deploy, destructive

# Minimum strength for untrusted / privileged code execution
UNTRUSTED_MIN_STRENGTH = {IsolationStrength.STRONG, IsolationStrength.RESTRICTED}
PRIVILEGED_MIN_STRENGTH = {IsolationStrength.STRONG, IsolationStrength.RESTRICTED}


@dataclass
class IsolationResult:
    status: str
    stdout: str
    stderr: str
    exit_code: int
    duration_ms: int
    isolation: str
    isolation_level: str = IsolationLevel.DEGRADED.value
    strength: str = IsolationStrength.NONE.value
    policy: str = POLICY_UNTRUSTED
    policy_decision: str = ""
    policy_reason: str = ""

    @property
    def is_isolated(self) -> bool:
        return self.strength in (
            IsolationStrength.STRONG.value,
            IsolationStrength.RESTRICTED.value,
        )

    def to_evidence(self) -> dict:
        """Machine-readable isolation decision for audit/acceptance."""
        return {
            "trust_level": self.policy,
            "requested_isolation": "strong_or_restricted"
            if self.policy in (POLICY_UNTRUSTED, POLICY_PRIVILEGED)
            else "any_available",
            "actual_isolation": self.isolation,
            "strength": self.strength,
            "isolation_level": self.isolation_level,
            "policy_decision": self.policy_decision or (
                "denied" if self.status == "isolation_unavailable" else "allowed"
            ),
            "failure_reason": self.stderr if self.status == "isolation_unavailable" else "",
            "status": self.status,
            "exit_code": self.exit_code,
        }


def _which(*names: str) -> Optional[str]:
    for n in names:
        p = shutil.which(n)
        if p:
            return p
    return None


def _allow_degraded() -> bool:
    return os.environ.get("DEVOS_ALLOW_DEGRADED_ISOLATION", "").lower() in ("1", "true", "yes")


def _use_docker() -> bool:
    return bool(_which("docker")) and os.environ.get("DEVOS_USE_DOCKER_SANDBOX", "").lower() in (
        "1", "true", "yes",
    )


def _docker_image(lang: str) -> str:
    if lang == "python":
        return os.environ.get("DEVOS_SANDBOX_PYTHON_IMAGE", "python:3.13-slim")
    if lang == "bash":
        return os.environ.get("DEVOS_SANDBOX_BASH_IMAGE", "bash:5")
    return os.environ.get("DEVOS_SANDBOX_NODE_IMAGE", "node:22-slim")


def _docker_flags(*, allow_network: bool) -> list[str]:
    """Hardened Docker flags. Never mount Docker socket or host root."""
    net = ["--network=bridge"] if allow_network else ["--network=none"]
    return [
        *net,
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        "--read-only",
        "--pids-limit=128",
        "--memory=512m",
        "--cpus=1",
        "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m",
        "--tmpfs", "/work:rw,exec,nosuid,size=256m",
        "--user", "65534:65534",
        "--init",
        # No --privileged, no docker.sock, no host /proc bind beyond image defaults
    ]


def detect_backends() -> dict:
    """Safe operator diagnostics — no paths that leak secrets."""
    docker = bool(_which("docker"))
    docker_enabled = _use_docker()
    bwrap = bool(_which("bwrap", "bubblewrap"))
    firejail = bool(_which("firejail"))
    unshare = bool(_which("unshare"))
    preferred, strength = select_backend(allow_network=False)
    return {
        "available": strength in (IsolationStrength.STRONG.value, IsolationStrength.RESTRICTED.value),
        "backend": preferred,
        "strength": strength,
        "network_isolation": strength in (
            IsolationStrength.STRONG.value,
            IsolationStrength.RESTRICTED.value,
            IsolationStrength.NETWORK_ONLY.value,
        ),
        "filesystem_restriction": strength in (
            IsolationStrength.STRONG.value,
            IsolationStrength.RESTRICTED.value,
        ),
        "degraded": strength in (IsolationStrength.DEGRADED.value, IsolationStrength.NONE.value),
        "docker_binary": docker,
        "docker_enabled": docker_enabled,
        "bubblewrap": bwrap,
        "firejail": firejail,
        "unshare": unshare,
        "allow_degraded_env": _allow_degraded(),
        "suitable_for_untrusted_code": strength in (
            IsolationStrength.STRONG.value,
            IsolationStrength.RESTRICTED.value,
        ),
    }


def select_backend(*, allow_network: bool = False) -> tuple[str, str]:
    """Return (backend_name, strength) without executing."""
    if _use_docker():
        return "docker", IsolationStrength.STRONG.value
    if _which("bwrap", "bubblewrap"):
        return "bwrap", IsolationStrength.RESTRICTED.value
    if _which("firejail"):
        return "firejail", IsolationStrength.RESTRICTED.value
    if _which("unshare"):
        return "unshare", IsolationStrength.NETWORK_ONLY.value
    if _allow_degraded():
        return "degraded_host", IsolationStrength.DEGRADED.value
    return "none", IsolationStrength.NONE.value


def strength_allows_untrusted(strength: str) -> bool:
    try:
        s = IsolationStrength(strength)
    except ValueError:
        return False
    return s in UNTRUSTED_MIN_STRENGTH


def strength_allows_privileged(strength: str) -> bool:
    try:
        s = IsolationStrength(strength)
    except ValueError:
        return False
    return s in PRIVILEGED_MIN_STRENGTH


def normalize_policy(policy: Optional[str]) -> str:
    """Map caller policy strings to canonical POLICY_* values."""
    p = (policy or POLICY_UNTRUSTED).strip().lower()
    if p in (POLICY_TRUSTED, "trusted", "developer", "local"):
        return POLICY_TRUSTED
    if p in (POLICY_PRIVILEGED, "privileged", "high_risk", "admin"):
        return POLICY_PRIVILEGED
    return POLICY_UNTRUSTED


def classify_execution_request(
    *,
    policy: Optional[str] = None,
    source: Optional[str] = None,
    explicit_untrusted: bool = False,
) -> str:
    """Classify an execution request into trusted | untrusted | privileged.

    Authoritative classification used at the subprocess boundary.
    Default is untrusted (fail closed for generated/uploaded/project code).

    Project-scoped sources (agent, bootstrap, check_runner, coding) are ALWAYS
    untrusted even if a caller passes policy=trusted (anti-spoof).
    """
    if explicit_untrusted:
        return POLICY_UNTRUSTED
    src = (source or "").strip().lower()
    # Project / AI / coding paths cannot be elevated to trusted via policy string.
    _FORCE_UNTRUSTED_SOURCES = (
        "agent_runtime", "agent_runtime_subprocess", "run_command_in_project",
        "project_bootstrap", "check_runner", "coding_loop", "coding",
        "sandbox", "workflow", "mission", "a2a", "uploaded", "repo",
        "flutter", "toolchain",
    )
    if any(src == s or src.startswith(s + "_") or src.endswith("_" + s) for s in _FORCE_UNTRUSTED_SOURCES):
        return POLICY_UNTRUSTED
    if src in ("system_admin", "deploy", "privileged_capability", "host"):
        return POLICY_PRIVILEGED
    if policy:
        return normalize_policy(policy)
    if src in ("developer", "local", "trusted_workspace", "self_hosted_dev", "terminal_human"):
        return POLICY_TRUSTED
    return POLICY_UNTRUSTED


def policy_allows_execution(policy: str, strength: str) -> tuple[bool, str]:
    """Decide whether code may run under this policy + backend strength.

    Authoritative: untrusted and privileged MUST NOT accept network_only or degraded.
    """
    pol = normalize_policy(policy)
    if pol == POLICY_TRUSTED:
        if strength == IsolationStrength.NONE.value and not _allow_degraded():
            return False, "No isolation backend and degraded mode disabled"
        return True, "trusted policy"
    if pol == POLICY_PRIVILEGED:
        if not strength_allows_privileged(strength):
            return False, (
                f"Privileged execution requires strong/restricted isolation; "
                f"available strength={strength}. Enable Docker (DEVOS_USE_DOCKER_SANDBOX=1) "
                f"or install bubblewrap/firejail."
            )
        return True, "privileged policy satisfied"
    # untrusted (default)
    if not strength_allows_untrusted(strength):
        return False, (
            f"Untrusted code requires strong/restricted isolation; "
            f"available strength={strength}. Enable Docker (DEVOS_USE_DOCKER_SANDBOX=1) "
            f"or install bubblewrap/firejail. unshare-only and degraded host are insufficient."
        )
    return True, "untrusted policy satisfied"


def evaluate_isolation_decision(
    policy: str,
    *,
    allow_network: bool = False,
) -> dict:
    """Pre-flight isolation decision without executing.

    Returns structured evidence: trust_level, backend, strength, allowed, reason.
    """
    pol = normalize_policy(policy)
    backend, strength = select_backend(allow_network=allow_network)
    ok, reason = policy_allows_execution(pol, strength)
    return {
        "trust_level": pol,
        "requested_isolation": "strong_or_restricted"
        if pol in (POLICY_UNTRUSTED, POLICY_PRIVILEGED)
        else "any_available",
        "backend": backend,
        "actual_isolation": backend,
        "strength": strength,
        "policy_decision": "allowed" if ok else "denied",
        "failure_reason": "" if ok else reason,
        "allowed": ok,
        "reason": reason,
        "suitable_for_untrusted_code": strength_allows_untrusted(strength),
    }


async def run_isolated(
    cmd,
    *,
    cwd=None,
    env=None,
    timeout_s=60,
    language="python",
    require_isolation=True,
    allow_network: bool = False,
    policy: str = POLICY_UNTRUSTED,
) -> IsolationResult:
    """Run command under the best available isolation backend.

    For policy=untrusted, refuses network_only / degraded / none.
    allow_network only affects Docker network mode when strength is strong;
    it never means 'run bare on the host'.
    """
    t0 = time.monotonic()
    safe_keys = ("PATH", "HOME", "LANG", "LC_ALL", "TERM", "PYTHONPATH",
                 "PYTHONDONTWRITEBYTECODE", "PYTHONUNBUFFERED", "NODE_ENV", "TMPDIR")
    base_env = env or {}
    env = {
        k: v for k, v in base_env.items()
        if k in safe_keys or k.startswith("SECRET_")  # only explicitly injected secrets
    }
    env.setdefault("PATH", "/usr/bin:/bin")

    pol = normalize_policy(policy)
    backend, strength = select_backend(allow_network=allow_network)
    ok, reason = policy_allows_execution(pol, strength)
    if not ok:
        logger.warning(
            "isolation_denied policy=%s backend=%s strength=%s reason=%s",
            pol, backend, strength, reason,
        )
        return IsolationResult(
            status="isolation_unavailable",
            stdout="",
            stderr=reason,
            exit_code=126,
            duration_ms=int((time.monotonic() - t0) * 1000),
            isolation=backend,
            isolation_level=IsolationLevel.UNSAFE.value,
            strength=strength,
            policy=pol,
            policy_decision="denied",
            policy_reason=reason,
        )

    if strength == IsolationStrength.NONE.value:
        return IsolationResult(
            status="isolation_unavailable",
            stdout="",
            stderr="No isolation backend available",
            exit_code=126,
            duration_ms=int((time.monotonic() - t0) * 1000),
            isolation="none",
            isolation_level=IsolationLevel.UNSAFE.value,
            strength=IsolationStrength.NONE.value,
            policy=pol,
            policy_decision="denied",
            policy_reason="No isolation backend available",
        )

    # Docker (strong)
    if backend == "docker":
        docker = _which("docker")
        work = cwd or tempfile.mkdtemp(prefix="devos-iso-")
        # Mount only the work directory — never repo root, .env, docker.sock
        full = [
            docker, "run", "--rm",
            *_docker_flags(allow_network=allow_network),
            "-v", f"{work}:/work:rw",
            "-w", "/work",
            _docker_image(language),
            *cmd,
        ]
        return await _run(
            full, None, env, timeout_s, "docker", strength, t0, policy=pol
        )

    # bubblewrap (restricted)
    if backend == "bwrap":
        bwrap = _which("bwrap", "bubblewrap")
        work = cwd or tempfile.mkdtemp(prefix="devos-iso-")
        net_args = [] if allow_network else ["--unshare-net"]
        full = [
            bwrap, *net_args, "--die-with-parent",
            "--ro-bind", "/usr", "/usr",
            "--ro-bind", "/bin", "/bin",
            "--ro-bind", "/lib", "/lib",
            "--ro-bind-try", "/lib64", "/lib64",
            "--proc", "/proc",
            "--dev", "/dev",
            "--tmpfs", "/tmp",
            "--bind", work, "/work",
            "--chdir", "/work",
            "--", *cmd,
        ]
        return await _run(full, None, env, timeout_s, "bwrap", strength, t0, policy=pol)

    # firejail (restricted)
    if backend == "firejail":
        firejail = _which("firejail")
        net = [] if allow_network else ["--net=none"]
        return await _run(
            [firejail, *net, "--private", "--quiet", "--", *cmd],
            cwd, env, timeout_s, "firejail", strength, t0, policy=pol,
        )

    # unshare network_only — only reachable for trusted policy
    if backend == "unshare":
        unshare = _which("unshare")
        return await _run(
            [unshare, "--net", "--", *cmd],
            cwd, env, timeout_s, "unshare", strength, t0, policy=pol,
        )

    # degraded host — trusted + DEVOS_ALLOW_DEGRADED_ISOLATION only
    if backend == "degraded_host":
        logger.warning("degraded host isolation in use (dev only)")
        return await _run(cmd, cwd, env, timeout_s, "degraded_host", strength, t0, policy=pol)

    return IsolationResult(
        status="isolation_unavailable",
        stdout="",
        stderr="No suitable isolation backend",
        exit_code=126,
        duration_ms=int((time.monotonic() - t0) * 1000),
        isolation="none",
        isolation_level=IsolationLevel.UNSAFE.value,
        strength=IsolationStrength.NONE.value,
        policy=pol,
        policy_decision="denied",
        policy_reason="No suitable isolation backend",
    )


async def _run(cmd, cwd, env, timeout_s, isolation, strength, t0, policy: str = POLICY_UNTRUSTED) -> IsolationResult:
    strength_v = strength.value if isinstance(strength, IsolationStrength) else strength
    pol = normalize_policy(policy)
    level = (
        IsolationLevel.ISOLATED.value
        if strength_v in (IsolationStrength.STRONG.value, IsolationStrength.RESTRICTED.value)
        else IsolationLevel.DEGRADED.value
        if strength_v == IsolationStrength.DEGRADED.value
        else IsolationLevel.UNSAFE.value
    )
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, cwd=cwd, env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except Exception:
                pass
            return IsolationResult(
                status="timeout",
                stdout="",
                stderr="timeout",
                exit_code=124,
                duration_ms=int((time.monotonic() - t0) * 1000),
                isolation=isolation,
                isolation_level=level,
                strength=strength_v,
                policy=pol,
                policy_decision="allowed",
                policy_reason="",
            )
        return IsolationResult(
            status="ok" if proc.returncode == 0 else "error",
            stdout=out.decode("utf-8", "replace")[:200000],
            stderr=err.decode("utf-8", "replace")[:50000],
            exit_code=proc.returncode or 0,
            duration_ms=int((time.monotonic() - t0) * 1000),
            isolation=isolation,
            isolation_level=level,
            strength=strength_v,
            policy=pol,
            policy_decision="allowed",
            policy_reason="",
        )
    except Exception as e:
        return IsolationResult(
            status="error",
            stdout="",
            stderr=str(e),
            exit_code=1,
            duration_ms=int((time.monotonic() - t0) * 1000),
            isolation=isolation,
            isolation_level=level,
            strength=strength_v,
            policy=pol,
            policy_decision="allowed",
            policy_reason="",
        )
