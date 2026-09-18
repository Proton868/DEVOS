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


@dataclass
class SpawnResult:
    """Result of spawn_isolated — long-running process handle + isolation evidence.

    process is None when isolation is denied or spawn failed.
    Does not wait for process exit.
    """
    status: str  # "spawned" | "isolation_unavailable" | "error"
    process: Optional[asyncio.subprocess.Process]
    isolation: str
    strength: str
    policy: str
    policy_decision: str
    policy_reason: str
    isolation_level: str = IsolationLevel.DEGRADED.value
    stderr: str = ""
    source: str = ""
    exit_code: Optional[int] = None

    @property
    def is_isolated(self) -> bool:
        return self.strength in (
            IsolationStrength.STRONG.value,
            IsolationStrength.RESTRICTED.value,
        )

    def to_evidence(self) -> dict:
        """Machine-readable isolation decision for audit/runtime persistence.

        Contract fields for ApplicationRuntime evidence["isolation"]:
          trust_level, source, requested_isolation, actual_isolation, strength,
          isolation_level, policy_decision, failure_reason, status, exit_code.
        """
        return {
            "trust_level": self.policy,
            "source": self.source,
            "requested_isolation": "strong_or_restricted"
            if self.policy in (POLICY_UNTRUSTED, POLICY_PRIVILEGED)
            else "any_available",
            "actual_isolation": self.isolation,
            "backend": self.isolation,
            "strength": self.strength,
            "isolation_level": self.isolation_level,
            "policy_decision": self.policy_decision or (
                "denied" if self.status == "isolation_unavailable" else "allowed"
            ),
            "failure_reason": self.policy_reason or (
                self.stderr if self.status == "isolation_unavailable" else ""
            ),
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


def _bwrap_operational() -> bool:
    """Verify bubblewrap can actually create a sandbox (not just that the binary exists).

    Synchronous so it is safe to call from select_backend() whether or not an
    asyncio event loop is already running. Must not use asyncio.run().
    """
    bwrap = _which("bwrap", "bubblewrap")
    if not bwrap:
        return False
    try:
        import subprocess
        r = subprocess.run(
            [
                bwrap,
                "--unshare-net",
                "--die-with-parent",
                "--ro-bind", "/usr", "/usr",
                "--ro-bind", "/bin", "/bin",
                "--ro-bind-try", "/lib", "/lib",
                "--ro-bind-try", "/lib64", "/lib64",
                "--proc", "/proc",
                "--dev", "/dev",
                "--tmpfs", "/tmp",
                "--",
                "true",
            ],
            capture_output=True,
            timeout=5,
            check=False,
        )
        return r.returncode == 0
    except Exception as e:
        logger.debug("bwrap operational probe failed: %s", e)
        return False


def select_backend(*, allow_network: bool = False) -> tuple[str, str]:
    """Return (backend_name, strength) without executing.

    bwrap is only selected when an operational probe confirms sandbox creation
    works (binary presence alone is insufficient).
    """
    if _use_docker():
        return "docker", IsolationStrength.STRONG.value
    if _which("bwrap", "bubblewrap") and _bwrap_operational():
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
        "app_runtime", "application_runtime",
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


def _sanitize_env(env: Optional[dict], policy: str) -> dict:
    """Canonical env sanitization shared by run_isolated and spawn_isolated."""
    safe_keys = (
        "PATH", "HOME", "LANG", "LC_ALL", "TERM",
        "PYTHONDONTWRITEBYTECODE", "PYTHONUNBUFFERED", "NODE_ENV", "TMPDIR",
        "FLUTTER_ROOT", "PUB_CACHE", "DART_SDK", "JAVA_HOME",
        "PORT", "HOST", "HOSTNAME", "CI",
        "npm_config_yes", "npm_config_fund", "npm_config_audit", "npm_config_registry",
        "NEXT_TELEMETRY_DISABLED",
    )
    base_env = env or {}
    pol = normalize_policy(policy)
    if pol == POLICY_UNTRUSTED:
        out = {k: v for k, v in base_env.items() if k in safe_keys}
    else:
        out = {
            k: v for k, v in base_env.items()
            if k in safe_keys or k.startswith("SECRET_") or k == "PYTHONPATH"
        }
    out.setdefault("PATH", "/usr/bin:/bin")
    return out


def _build_isolated_argv(
    cmd,
    *,
    cwd,
    allow_network: bool,
    language: str,
    backend: str,
) -> tuple[list[str], Optional[str]]:
    """Build full argv + effective cwd for an isolation backend.

    Returns (full_argv, cwd_for_subprocess). cwd may be None when the
    sandbox already chdir's into the work dir (docker/bwrap).
    """
    work = cwd or tempfile.mkdtemp(prefix="devos-iso-")
    if backend == "docker":
        docker = _which("docker")
        full = [
            docker, "run", "--rm",
            *_docker_flags(allow_network=allow_network),
            "-v", f"{work}:/work:rw",
            "-w", "/work",
            _docker_image(language),
            *cmd,
        ]
        return full, None
    if backend == "bwrap":
        bwrap = _which("bwrap", "bubblewrap")
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
        return full, None
    if backend == "firejail":
        firejail = _which("firejail")
        net = [] if allow_network else ["--net=none"]
        return [firejail, *net, "--private", "--quiet", "--", *cmd], cwd
    if backend == "unshare":
        unshare = _which("unshare")
        return [unshare, "--net", "--", *cmd], cwd
    if backend == "degraded_host":
        return list(cmd), cwd
    raise ValueError(f"unknown isolation backend: {backend}")


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
    source: str = "",
) -> IsolationResult:
    """Run command under the best available isolation backend.

    For policy=untrusted, refuses network_only / degraded / none.
    allow_network only affects Docker/bwrap network mode when strength is
    strong/restricted; it never means 'run bare on the host'.
    """
    t0 = time.monotonic()
    pol = normalize_policy(policy)
    if source:
        pol = classify_execution_request(policy=pol, source=source)
    env = _sanitize_env(env, pol)
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

    try:
        full, run_cwd = _build_isolated_argv(
            cmd, cwd=cwd, allow_network=allow_network, language=language, backend=backend,
        )
    except Exception as e:
        return IsolationResult(
            status="error",
            stdout="",
            stderr=str(e),
            exit_code=1,
            duration_ms=int((time.monotonic() - t0) * 1000),
            isolation=backend,
            isolation_level=IsolationLevel.UNSAFE.value,
            strength=strength,
            policy=pol,
            policy_decision="denied",
            policy_reason=str(e),
        )

    if backend == "degraded_host":
        logger.warning("degraded host isolation in use (dev only)")

    return await _run(full, run_cwd, env, timeout_s, backend, strength, t0, policy=pol)


async def spawn_isolated(
    cmd,
    *,
    cwd=None,
    env=None,
    language: str = "python",
    allow_network: bool = False,
    policy: str = POLICY_UNTRUSTED,
    source: str = "",
    merge_stderr: bool = False,
) -> SpawnResult:
    """Spawn a long-running process under the best available isolation backend.

    Same sanitization, policy normalization, backend selection, strength
    validation, and fail-closed behavior as run_isolated(). Does NOT wait for
    the process to finish.

    Returns SpawnResult with the asyncio.subprocess.Process (when spawned) plus
    the actual backend/strength/policy decision for audit persistence.

    For policy=untrusted, network_only / degraded / none are denied — no host
    process fallback.
    """
    pol = normalize_policy(policy)
    if source:
        pol = classify_execution_request(policy=pol, source=source)
    env = _sanitize_env(env, pol)
    backend, strength = select_backend(allow_network=allow_network)
    ok, reason = policy_allows_execution(pol, strength)

    def _denied(backend_name: str, strength_v: str, why: str) -> SpawnResult:
        logger.warning(
            "spawn_isolated denied policy=%s backend=%s strength=%s reason=%s source=%s",
            pol, backend_name, strength_v, why, source,
        )
        return SpawnResult(
            status="isolation_unavailable",
            process=None,
            isolation=backend_name,
            strength=strength_v,
            policy=pol,
            policy_decision="denied",
            policy_reason=why,
            isolation_level=IsolationLevel.UNSAFE.value,
            stderr=why,
            source=source,
            exit_code=126,
        )

    if not ok:
        return _denied(backend, strength, reason)
    if strength == IsolationStrength.NONE.value:
        return _denied("none", IsolationStrength.NONE.value, "No isolation backend available")

    try:
        full, spawn_cwd = _build_isolated_argv(
            cmd, cwd=cwd, allow_network=allow_network, language=language, backend=backend,
        )
    except Exception as e:
        return SpawnResult(
            status="error",
            process=None,
            isolation=backend,
            strength=strength,
            policy=pol,
            policy_decision="denied",
            policy_reason=str(e),
            isolation_level=IsolationLevel.UNSAFE.value,
            stderr=str(e),
            source=source,
            exit_code=1,
        )

    strength_v = strength.value if isinstance(strength, IsolationStrength) else strength
    level = (
        IsolationLevel.ISOLATED.value
        if strength_v in (IsolationStrength.STRONG.value, IsolationStrength.RESTRICTED.value)
        else IsolationLevel.DEGRADED.value
        if strength_v == IsolationStrength.DEGRADED.value
        else IsolationLevel.UNSAFE.value
    )
    stderr_dest = (
        asyncio.subprocess.STDOUT if merge_stderr else asyncio.subprocess.PIPE
    )
    try:
        proc = await asyncio.create_subprocess_exec(
            *full,
            cwd=spawn_cwd,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=stderr_dest,
            start_new_session=True,
        )
    except Exception as e:
        return SpawnResult(
            status="error",
            process=None,
            isolation=backend,
            strength=strength_v,
            policy=pol,
            policy_decision="denied",
            policy_reason=str(e),
            isolation_level=level,
            stderr=str(e),
            source=source,
            exit_code=1,
        )
    return SpawnResult(
        status="spawned",
        process=proc,
        isolation=backend,
        strength=strength_v,
        policy=pol,
        policy_decision="allowed",
        policy_reason="",
        isolation_level=level,
        source=source,
        exit_code=None,
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
