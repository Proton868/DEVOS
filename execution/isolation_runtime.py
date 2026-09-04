"""Optional Linux namespace isolation for Application Runtime children."""
from __future__ import annotations
import os
import shutil
from typing import List, Optional


def isolation_available() -> dict:
    return {
        "unshare": bool(shutil.which("unshare")),
        "bwrap": bool(shutil.which("bwrap")),
        "docker": bool(shutil.which("docker")),
        "enforced": bool(shutil.which("unshare") or shutil.which("bwrap")),
    }


def wrap_command(cmd: List[str], *, cwd: str, net: bool = False) -> List[str]:
    """Wrap command with unshare for PID/mount isolation when available (best-effort)."""
    unshare = shutil.which("unshare")
    if not unshare:
        return cmd
    # --map-root-user may require privileges; try user namespaces without net by default
    prefix = [unshare, "--user", "--pid", "--fork", "--mount-proc"]
    if not net:
        prefix.append("--net")
    # run as current user inside
    return prefix + ["--"] + list(cmd)
