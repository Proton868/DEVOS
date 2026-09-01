"""Execution isolation for AI/automation paths (network-off preferred)."""
from __future__ import annotations
import asyncio, os, shutil, tempfile, time
from dataclasses import dataclass
from typing import Optional

@dataclass
class IsolationResult:
    status: str
    stdout: str
    stderr: str
    exit_code: int
    duration_ms: int
    isolation: str

def _which(*names: str) -> Optional[str]:
    for n in names:
        p = shutil.which(n)
        if p: return p
    return None

async def run_isolated(cmd: list[str], *, cwd: Optional[str]=None, env: Optional[dict]=None,
                       timeout_s: int=60, language: str="python") -> IsolationResult:
    t0 = time.monotonic()
    env = {k: v for k, v in (env or os.environ).items()
           if k in ("PATH","HOME","LANG","LC_ALL","TERM","PYTHONPATH") or k.startswith("DEVOS_")}
    env.setdefault("PATH", "/usr/bin:/bin")
    isolation = "process"
    docker = _which("docker")
    if docker and os.environ.get("DEVOS_USE_DOCKER_SANDBOX","").lower() in ("1","true","yes"):
        isolation = "docker-network-none"
        work = cwd or tempfile.mkdtemp(prefix="devos-iso-")
        full = [docker,"run","--rm","--network=none","--cpus=1","--memory=512m",
                "-v",f"{work}:/work:rw","-w","/work",
                "python:3.13-slim" if language=="python" else "node:22-slim", *cmd]
        return await _run(full, None, env, timeout_s, isolation, t0)
    bwrap = _which("bwrap","bubblewrap")
    if bwrap:
        isolation = "bwrap-unshare-net"
        work = cwd or tempfile.mkdtemp(prefix="devos-iso-")
        full = [bwrap,"--unshare-net","--die-with-parent","--ro-bind","/usr","/usr",
                "--ro-bind","/bin","/bin","--ro-bind","/lib","/lib","--ro-bind-try","/lib64","/lib64",
                "--proc","/proc","--dev","/dev","--tmpfs","/tmp","--bind",work,"/work","--chdir","/work","--",*cmd]
        return await _run(full, None, env, timeout_s, isolation, t0)
    firejail = _which("firejail")
    if firejail:
        isolation = "firejail-net-none"
        return await _run([firejail,"--net=none","--private","--quiet","--",*cmd], cwd, env, timeout_s, isolation, t0)
    unshare = _which("unshare")
    if unshare:
        isolation = "unshare-net"
        return await _run([unshare,"--net","--",*cmd], cwd, env, timeout_s, isolation, t0)
    isolation = "env-stripped-fallback"
    return await _run(cmd, cwd, env, timeout_s, isolation, t0)

async def _run(cmd, cwd, env, timeout_s, isolation, t0) -> IsolationResult:
    try:
        proc = await asyncio.create_subprocess_exec(*cmd, cwd=cwd, env=env,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
        except asyncio.TimeoutError:
            proc.kill()
            return IsolationResult("timeout","", "timeout", 124, int((time.monotonic()-t0)*1000), isolation)
        return IsolationResult("ok" if proc.returncode==0 else "error",
            out.decode("utf-8","replace")[:200000], err.decode("utf-8","replace")[:50000],
            proc.returncode or 0, int((time.monotonic()-t0)*1000), isolation)
    except Exception as e:
        return IsolationResult("error","", str(e), 1, int((time.monotonic()-t0)*1000), isolation)
