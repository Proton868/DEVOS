"""Isolation wrappers: docker > bwrap > unshare > process."""
from __future__ import annotations
import os
import shutil
from typing import List


def isolation_available() -> dict:
    return {
        "docker": bool(shutil.which("docker")),
        "bwrap": bool(shutil.which("bwrap")),
        "unshare": bool(shutil.which("unshare")),
        "mode": (
            "docker" if shutil.which("docker") else
            "bwrap" if shutil.which("bwrap") else
            "unshare" if shutil.which("unshare") else
            "process"
        ),
        "enforced": bool(shutil.which("docker") or shutil.which("bwrap") or shutil.which("unshare")),
    }


def wrap_command(cmd: List[str], *, cwd: str, net: bool = False, memory_mb: int = 512, cpus: float = 1.0) -> List[str]:
    mode = isolation_available()["mode"]
    if mode == "docker":
        # ephemeral container; mount only workspace cwd
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
    if unshare:
        prefix = [unshare, "--user", "--pid", "--fork", "--mount-proc"]
        if not net:
            prefix.append("--net")
        return prefix + ["--"] + list(cmd)
    return list(cmd)
