"""Fail-closed execution isolation."""
from __future__ import annotations
import asyncio, os, shutil, tempfile, time
from dataclasses import dataclass
from enum import Enum
from typing import Optional

class IsolationLevel(str, Enum):
    ISOLATED="isolated"; DEGRADED="degraded"; UNSAFE="unsafe"

@dataclass
class IsolationResult:
    status: str; stdout: str; stderr: str; exit_code: int; duration_ms: int; isolation: str
    isolation_level: str = IsolationLevel.DEGRADED.value
    @property
    def is_isolated(self): return self.isolation_level == IsolationLevel.ISOLATED.value

def _which(*names):
    for n in names:
        p=shutil.which(n)
        if p: return p
    return None

def _allow_degraded():
    return os.environ.get("DEVOS_ALLOW_DEGRADED_ISOLATION","").lower() in ("1","true","yes")

def _docker_image(lang):
    if lang=="python": return os.environ.get("DEVOS_SANDBOX_PYTHON_IMAGE","python:3.13-slim")
    return os.environ.get("DEVOS_SANDBOX_NODE_IMAGE","node:22-slim")

def _docker_flags():
    return ["--cap-drop=ALL","--security-opt=no-new-privileges","--read-only","--pids-limit=128",
            "--tmpfs","/tmp:rw,noexec,nosuid,size=64m","--tmpfs","/work:rw,exec,nosuid,size=256m",
            "--user","65534:65534","--init"]

async def run_isolated(cmd, *, cwd=None, env=None, timeout_s=60, language="python", require_isolation=True):
    t0=time.monotonic()
    env={k:v for k,v in (env or os.environ).items() if k in ("PATH","HOME","LANG","LC_ALL","TERM","PYTHONPATH") or k.startswith("DEVOS_")}
    env.setdefault("PATH","/usr/bin:/bin")
    docker=_which("docker")
    if docker and os.environ.get("DEVOS_USE_DOCKER_SANDBOX","").lower() in ("1","true","yes"):
        work=cwd or tempfile.mkdtemp(prefix="devos-iso-")
        full=[docker,"run","--rm","--network=none","--cpus=1","--memory=512m",*_docker_flags(),
              "-v",f"{work}:/work:rw","-w","/work",_docker_image(language),*cmd]
        return await _run(full,None,env,timeout_s,"docker-network-none",IsolationLevel.ISOLATED,t0)
    bwrap=_which("bwrap","bubblewrap")
    if bwrap:
        work=cwd or tempfile.mkdtemp(prefix="devos-iso-")
        full=[bwrap,"--unshare-net","--die-with-parent","--ro-bind","/usr","/usr","--ro-bind","/bin","/bin",
              "--ro-bind","/lib","/lib","--ro-bind-try","/lib64","/lib64","--proc","/proc","--dev","/dev",
              "--tmpfs","/tmp","--bind",work,"/work","--chdir","/work","--",*cmd]
        return await _run(full,None,env,timeout_s,"bwrap-unshare-net",IsolationLevel.ISOLATED,t0)
    firejail=_which("firejail")
    if firejail:
        return await _run([firejail,"--net=none","--private","--quiet","--",*cmd],cwd,env,timeout_s,"firejail-net-none",IsolationLevel.ISOLATED,t0)
    unshare=_which("unshare")
    if unshare:
        return await _run([unshare,"--net","--",*cmd],cwd,env,timeout_s,"unshare-net",IsolationLevel.ISOLATED,t0)
    if require_isolation and not _allow_degraded():
        return IsolationResult("isolation_unavailable","","AI execution requires network isolation; none available. Set DEVOS_ALLOW_DEGRADED_ISOLATION=1 for local dev only.",
            126,int((time.monotonic()-t0)*1000),"env-stripped-fallback",IsolationLevel.UNSAFE.value)
    return await _run(cmd,cwd,env,timeout_s,"env-stripped-fallback",IsolationLevel.DEGRADED,t0)

async def _run(cmd,cwd,env,timeout_s,isolation,level,t0):
    lv=level.value if isinstance(level,IsolationLevel) else level
    try:
        proc=await asyncio.create_subprocess_exec(*cmd,cwd=cwd,env=env,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
        try:
            out,err=await asyncio.wait_for(proc.communicate(),timeout=timeout_s)
        except asyncio.TimeoutError:
            proc.kill(); return IsolationResult("timeout","","timeout",124,int((time.monotonic()-t0)*1000),isolation,lv)
        return IsolationResult("ok" if proc.returncode==0 else "error",out.decode("utf-8","replace")[:200000],
            err.decode("utf-8","replace")[:50000],proc.returncode or 0,int((time.monotonic()-t0)*1000),isolation,lv)
    except Exception as e:
        return IsolationResult("error","",str(e),1,int((time.monotonic()-t0)*1000),isolation,lv)
