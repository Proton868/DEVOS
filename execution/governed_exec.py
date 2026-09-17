"""Governed subprocess execution boundary.

All project-scoped and untrusted command execution should enter through this
module (or run_command_in_project, which delegates here). Isolation policy from
execution.isolation is authoritative for untrusted/privileged work.

Modes:
  argv   — preferred: create_subprocess_exec with explicit argument list
  shell  — explicit shell semantics when required (pipes, redirects, glue);
           still runs under isolation for untrusted; never pretends
           metacharacters are safe without isolation.

This is not a command-string denylist. npm/make/flutter/python scripts can
run arbitrary code; isolation is the primary boundary.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import signal
import time
from dataclasses import dataclass, field
from typing import Optional, Sequence

logger = logging.getLogger("devos.governed_exec")

# Host secrets / credentials that must never leak into subprocess env
_SECRET_ENV_DENY = re.compile(
    r"(?i)^(.*(SECRET|TOKEN|PASSWORD|PASSWD|API_?KEY|PRIVATE_?KEY|CREDENTIAL|"
    r"AUTH|AWS_|AZURE_|GCP_|OPENAI_|ANTHROPIC_|SUPABASE_|DATABASE_URL|"
    r"REDIS_URL|SMTP_|JWT_|SESSION_|COOKIE|SSH_|GPG_|SERVICE_ROLE|"
    r"DEVOS_|REQUIRE_POSTGRES).*)|"
    r"^(GITHUB_TOKEN|GH_TOKEN|NPM_TOKEN|HF_TOKEN|HUGGINGFACE_HUB_TOKEN|"
    r"DATABASE_URL|SUPABASE_SERVICE_KEY|SUPABASE_SERVICE_ROLE_KEY)$"
)

# Loader / shell injection vectors — never pass through for untrusted work
_DANGEROUS_ENV = frozenset({
    "LD_PRELOAD", "LD_LIBRARY_PATH", "DYLD_INSERT_LIBRARIES", "DYLD_LIBRARY_PATH",
    "PYTHONSTARTUP", "BASH_ENV", "ENV", "IFS",
    "PROMPT_COMMAND", "SHELLOPTS",
})

_SAFE_ENV_ALLOW = frozenset({
    "PATH", "HOME", "LANG", "LC_ALL", "LC_CTYPE", "TERM", "TMPDIR", "TMP", "TEMP",
    "USER", "LOGNAME", "SHELL", "PWD",
    "PYTHONDONTWRITEBYTECODE", "PYTHONUNBUFFERED", "PYTHONIOENCODING",
    # PYTHONPATH only via explicit extra for trusted/privileged toolchains
    "NODE_ENV", "NODE_OPTIONS", "NPM_CONFIG_CACHE",
    "CI", "DEBIAN_FRONTEND",
    "FLUTTER_ROOT", "FLUTTER_STORAGE_BASE_URL", "PUB_CACHE", "DART_SDK",
    "JAVA_HOME", "ANDROID_HOME", "ANDROID_SDK_ROOT",
})

MAX_STDOUT = 200_000
MAX_STDERR = 50_000


@dataclass
class GovernedResult:
    ok: bool
    exit_code: int
    stdout: str
    stderr: str
    status: str  # success | failed | timeout | cancelled | isolation_unavailable | denied
    duration_ms: int
    mode: str  # argv | shell
    command_repr: str
    isolation: str = ""
    isolation_strength: str = ""
    isolation_evidence: dict = field(default_factory=dict)
    cancelled: bool = False

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "exit_code": self.exit_code,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "status": self.status,
            "duration_ms": self.duration_ms,
            "mode": self.mode,
            "command": self.command_repr,
            "isolation": self.isolation,
            "isolation_strength": self.isolation_strength,
            "isolation_evidence": dict(self.isolation_evidence or {}),
            "cancelled": self.cancelled,
        }


def scrub_env(
    base: Optional[dict] = None,
    *,
    extra: Optional[dict] = None,
    allow_secret_prefix: bool = False,
    allow_pythonpath: bool = False,
) -> dict:
    """Build a subprocess environment without host secrets/credentials.

    Fail-closed: only allowlisted keys + optional explicit SECRET_* pass.
    Dangerous loader vars (LD_PRELOAD, BASH_ENV, …) are always dropped.
    PYTHONPATH is omitted unless allow_pythonpath=True (trusted toolchains).
    """
    src = dict(base or {})
    out: dict[str, str] = {}
    for k, v in src.items():
        if v is None:
            continue
        key = str(k)
        if key in _DANGEROUS_ENV:
            continue
        if key == "PYTHONPATH" and not allow_pythonpath:
            continue
        if key in _SAFE_ENV_ALLOW:
            out[key] = str(v)
            continue
        if allow_secret_prefix and key.startswith("SECRET_"):
            out[key] = str(v)
            continue
        if _SECRET_ENV_DENY.match(key):
            continue
        # Drop everything else by default (fail closed on unknown secrets)
    # Minimal defaults — never inherit host secrets via os.environ.copy()
    out.setdefault("PATH", os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"))
    out.setdefault("HOME", os.environ.get("HOME", "/tmp"))
    out.setdefault("LANG", os.environ.get("LANG", "C.UTF-8"))
    out.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    out.setdefault("PYTHONUNBUFFERED", "1")
    out.setdefault("TMPDIR", "/tmp")
    if extra:
        for k, v in extra.items():
            if v is None:
                continue
            key = str(k)
            if key in _DANGEROUS_ENV:
                logger.warning("scrub_env refused dangerous key: %s", key)
                continue
            if key == "PYTHONPATH" and not allow_pythonpath:
                continue
            if _SECRET_ENV_DENY.match(key) and not (
                allow_secret_prefix and key.startswith("SECRET_")
            ):
                logger.warning("scrub_env refused secret-like key: %s", key)
                continue
            out[key] = str(v)
    return out


def needs_shell(command: str) -> bool:
    """Heuristic: true when shell metacharacters/operators are present.

    Not a security control — only selects argv vs shell representation.
    Isolation remains mandatory for untrusted regardless of mode.
    """
    if not command:
        return False
    # Common shell features that cannot be expressed as plain argv
    markers = (
        "|", "||", "&&", ";", ">", ">>", "<", "<<", "$(", "`",
        "\n", "\r", "~",
    )
    # Avoid false positives on simple quoted paths; still treat operators as shell
    for m in markers:
        if m in command:
            return True
    # Variable expansion
    if re.search(r"(?<!\\)\$[A-Za-z_{(]", command):
        return True
    return False


def try_split_argv(command: str) -> Optional[list[str]]:
    """Split a simple command into argv, or None if shell mode is required."""
    if needs_shell(command):
        return None
    try:
        import shlex
        parts = shlex.split(command, posix=True)
    except ValueError:
        return None
    if not parts:
        return None
    return parts


async def _kill_process_tree(proc: asyncio.subprocess.Process) -> None:
    """Best-effort terminate process group then process."""
    try:
        if proc.pid:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError, OSError):
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
        else:
            proc.kill()
    except ProcessLookupError:
        pass
    try:
        await asyncio.wait_for(proc.communicate(), timeout=2.0)
    except Exception:
        pass


async def run_governed(
    *,
    argv: Optional[Sequence[str]] = None,
    shell_command: Optional[str] = None,
    cwd: Optional[str] = None,
    env: Optional[dict] = None,
    timeout_s: int = 120,
    policy: str = "untrusted",
    source: Optional[str] = None,
    allow_network: bool = False,
    language: str = "bash",
    cancel_check=None,
) -> GovernedResult:
    """Run a command under isolation policy.

    Provide either argv (preferred) or shell_command (explicit shell mode).
    Untrusted/privileged always require strong/restricted isolation.
    """
    from execution.isolation import (
        classify_execution_request,
        evaluate_isolation_decision,
        run_isolated,
        normalize_policy,
    )

    t0 = time.monotonic()
    timeout = max(1, min(int(timeout_s or 120), 600))
    trust = classify_execution_request(policy=policy, source=source)
    mode = "argv"
    cmd_list: list[str]
    command_repr: str

    if argv is not None:
        cmd_list = [str(x) for x in argv]
        if not cmd_list:
            return GovernedResult(
                ok=False, exit_code=2, stdout="", stderr="empty argv",
                status="denied", duration_ms=0, mode="argv", command_repr="",
                isolation_evidence={"trust_level": trust, "policy_decision": "denied",
                                    "failure_reason": "empty argv"},
            )
        command_repr = " ".join(cmd_list)
        mode = "argv"
    elif shell_command is not None:
        sc = str(shell_command).strip()
        if not sc or "\x00" in sc:
            return GovernedResult(
                ok=False, exit_code=2, stdout="", stderr="invalid shell command",
                status="denied", duration_ms=0, mode="shell", command_repr=sc or "",
                isolation_evidence={"trust_level": trust, "policy_decision": "denied",
                                    "failure_reason": "invalid shell command"},
            )
        # Prefer argv when the string is a simple command
        split = try_split_argv(sc)
        if split is not None:
            cmd_list = split
            command_repr = sc
            mode = "argv"
        else:
            cmd_list = ["/bin/sh", "-c", sc]
            command_repr = sc
            mode = "shell"
    else:
        return GovernedResult(
            ok=False, exit_code=2, stdout="", stderr="no command",
            status="denied", duration_ms=0, mode="argv", command_repr="",
        )

    decision = evaluate_isolation_decision(trust, allow_network=allow_network)
    if not decision.get("allowed"):
        reason = decision.get("failure_reason") or decision.get("reason") or "isolation_unavailable"
        logger.warning(
            "governed_exec isolation_refused trust=%s strength=%s mode=%s reason=%s",
            trust, decision.get("strength"), mode, reason,
        )
        return GovernedResult(
            ok=False,
            exit_code=126,
            stdout="",
            stderr=reason,
            status="isolation_unavailable",
            duration_ms=int((time.monotonic() - t0) * 1000),
            mode=mode,
            command_repr=command_repr,
            isolation=str(decision.get("backend") or "none"),
            isolation_strength=str(decision.get("strength") or ""),
            isolation_evidence={
                "trust_level": trust,
                "requested_isolation": decision.get("requested_isolation"),
                "actual_isolation": decision.get("actual_isolation"),
                "strength": decision.get("strength"),
                "policy_decision": "denied",
                "failure_reason": reason,
                "mode": mode,
            },
        )

    safe_env = scrub_env(env)

    # Cancellation poll before launch
    if cancel_check is not None:
        try:
            if cancel_check():
                return GovernedResult(
                    ok=False, exit_code=-1, stdout="", stderr="cancelled",
                    status="cancelled", duration_ms=0, mode=mode,
                    command_repr=command_repr, cancelled=True,
                    isolation_evidence={"trust_level": trust, "policy_decision": "cancelled"},
                )
        except Exception:
            pass

    iso = await run_isolated(
        cmd_list,
        cwd=cwd,
        env=safe_env,
        timeout_s=timeout,
        language=language or "bash",
        require_isolation=True,
        allow_network=bool(allow_network),
        policy=trust,
    )
    evidence = iso.to_evidence() if hasattr(iso, "to_evidence") else {}
    evidence["mode"] = mode
    duration = iso.duration_ms or int((time.monotonic() - t0) * 1000)

    if iso.status == "isolation_unavailable":
        return GovernedResult(
            ok=False, exit_code=126, stdout="", stderr=iso.stderr or "isolation_unavailable",
            status="isolation_unavailable", duration_ms=duration, mode=mode,
            command_repr=command_repr, isolation=iso.isolation,
            isolation_strength=iso.strength, isolation_evidence=evidence,
        )
    if iso.status == "timeout":
        return GovernedResult(
            ok=False, exit_code=-1,
            stdout=(iso.stdout or "")[:MAX_STDOUT],
            stderr=(iso.stderr or f"command timed out after {timeout}s")[:MAX_STDERR],
            status="timeout", duration_ms=duration, mode=mode,
            command_repr=command_repr, isolation=iso.isolation,
            isolation_strength=iso.strength, isolation_evidence=evidence,
        )

    code = int(iso.exit_code if iso.exit_code is not None else 1)
    ok = code == 0 and iso.status == "ok"
    return GovernedResult(
        ok=ok,
        exit_code=code,
        stdout=(iso.stdout or "")[:MAX_STDOUT],
        stderr=(iso.stderr or "")[:MAX_STDERR],
        status="success" if ok else "failed",
        duration_ms=duration,
        mode=mode,
        command_repr=command_repr,
        isolation=iso.isolation,
        isolation_strength=iso.strength,
        isolation_evidence=evidence,
    )


# Secret patterns for output scrubbing (best-effort; primary control is env scrub)
_OUTPUT_SECRET_RE = re.compile(
    r"(?i)((?:api[_-]?key|token|password|secret)\s*[:=]\s*['\"]?[^\s'\"]{8,}"
    r"|Bearer\s+[A-Za-z0-9._\-]{12,}"
    r"|ghp_[A-Za-z0-9]{20,}"
    r"|sk-[A-Za-z0-9]{20,}"
    r"|postgres(?:ql)?://[^\s]+)"
)



def scrub_output(text: str) -> str:
    if not text:
        return ""
    return _OUTPUT_SECRET_RE.sub(r"\1=***", text)
