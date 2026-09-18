"""Isolation wrappers aligned with execution.isolation policy.

Fail-closed for untrusted execution: never silently fall back to bare host
process when policy requires isolation. Prefer docker > bwrap > unshare;
refuse degraded process mode unless DEVOS_ALLOW_DEGRADED_ISOLATION=1 AND
the caller explicitly marks trust as trusted (never for untrusted).
"""
from __future__ import annotations

import logging
import os
import shutil
from typing import List, Optional

logger = logging.getLogger("devos.isolation_runtime")


class IsolationUnavailable(RuntimeError):
    """Raised when no suitable isolation backend is available for the policy."""

    def __init__(self, reason: str, *, backend: str = "none", strength: str = "none"):
        super().__init__(reason)
        self.reason = reason
        self.backend = backend
        self.strength = strength


def isolation_available() -> dict:
    docker = bool(shutil.which("docker"))
    bwrap = bool(shutil.which("bwrap"))
    unshare = bool(shutil.which("unshare"))
    if docker:
        mode = "docker"
        strength = "strong"
    elif bwrap:
        mode = "bwrap"
        strength = "strong"
    elif unshare:
        mode = "unshare"
        strength = "network_only"
    else:
        mode = "process"
        strength = "none"
    # Suitable for untrusted only when strong (docker/bwrap). unshare is network_only.
    suitable_untrusted = mode in ("docker", "bwrap")
    if mode == "unshare" and os.environ.get("DEVOS_ALLOW_UNSHARE_UNTRUSTED", "").lower() in (
        "1", "true", "yes",
    ):
        # Explicit ops opt-in only — still not as strong as container.
        suitable_untrusted = True
    return {
        "docker": docker,
        "bwrap": bwrap,
        "unshare": unshare,
        "mode": mode,
        "strength": strength,
        "enforced": mode != "process",
        "suitable_for_untrusted": suitable_untrusted,
    }


def wrap_command(
    cmd: List[str],
    *,
    cwd: str,
    net: bool = False,
    memory_mb: int = 512,
    cpus: float = 1.0,
    trust: str = "untrusted",
) -> List[str]:
    """Wrap argv with isolation prefix.

    trust:
      - untrusted (default): refuse process fallback; require strong isolation
      - trusted: may use best-effort wrap or bare process
      - privileged: same as trusted for wrapping (policy elsewhere)
    """
    info = isolation_available()
    mode = info["mode"]
    trust_l = (trust or "untrusted").lower().strip()

    if trust_l == "untrusted":
        if not info.get("suitable_for_untrusted"):
            allow_deg = os.environ.get("DEVOS_ALLOW_DEGRADED_ISOLATION", "").lower() in (
                "1", "true", "yes",
            )
            if allow_deg and mode == "unshare":
                # still wrap with unshare when explicitly allowed
                pass
            elif mode == "process" or not info.get("suitable_for_untrusted"):
                raise IsolationUnavailable(
                    f"isolation_unavailable: no strong sandbox for untrusted "
                    f"(mode={mode}, strength={info.get('strength')})",
                    backend=mode,
                    strength=str(info.get("strength") or "none"),
                )

    if mode == "docker":
        docker = shutil.which("docker")
        args = [
            docker, "run", "--rm",
            "--network", "bridge" if net else "none",
            "--memory", f"{memory_mb}m",
            "--cpus", str(cpus),
            "--pids-limit", "256",
            "--read-only",
            "--tmpfs", "/tmp:size=64m",
            "-v", f"{cwd}:{cwd}:rw",
            "-w", cwd,
            "--user", str(os.getuid()),
            "node:20-alpine",
        ]
        return args + list(cmd)
    if mode == "bwrap":
        bwrap = shutil.which("bwrap")
        args = [
            bwrap, "--die-with-parent",
            "--bind", cwd, cwd,
            "--chdir", cwd,
            "--proc", "/proc",
            "--dev", "/dev",
            "--unshare-pid",
        ]
        if not net:
            args.append("--unshare-net")
        return args + ["--"] + list(cmd)
    unshare = shutil.which("unshare")
    if unshare and mode == "unshare":
        prefix = [unshare, "--user", "--pid", "--fork", "--mount-proc"]
        if not net:
            prefix.append("--net")
        return prefix + ["--"] + list(cmd)

    # Bare process — only reachable for trusted/privileged when no sandbox binary
    if trust_l == "untrusted":
        raise IsolationUnavailable(
            "isolation_unavailable: process fallback forbidden for untrusted",
            backend="process",
            strength="none",
        )
    logger.warning("isolation_runtime: trusted process fallback cmd0=%s", (cmd or [""])[0])
    return list(cmd)
