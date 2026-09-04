"""
Application Runtime — isolated process execution for user apps (NOT Agent Runtime).

Security:
  - deny-by-default environment (no DevOS/provider secrets)
  - cwd restricted to workspace root via FileService
  - resource timeouts
  - no Docker socket / no host secret inheritance
  - controlled internal ports only (bound to 127.0.0.1)
"""
from __future__ import annotations

import asyncio
import logging
import os
import signal
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from execution.files import FileService, PathViolation
from execution.app_detect import detect_application
from execution.isolation_runtime import wrap_command, isolation_available

logger = logging.getLogger("devos.app_runtime")

# Hard deny list — never forward into child processes
_BLOCKED_ENV_KEYS = {
    "OPENROUTER_API_KEY", "OPENAI_API_KEY", "GITHUB_TOKEN", "GH_TOKEN",
    "VERCEL_TOKEN", "NETLIFY_TOKEN", "CLOUDFLARE_TOKEN", "CF_API_TOKEN",
    "JWT_SECRET", "SUPABASE_SERVICE_ROLE_KEY", "SUPABASE_ANON_KEY",
    "DATABASE_URL", "REDIS_URL", "DOCKER_HOST", "SSH_AUTH_SOCK",
    "AWS_SECRET_ACCESS_KEY", "AWS_ACCESS_KEY_ID", "GOOGLE_APPLICATION_CREDENTIALS",
}
_BLOCKED_PREFIXES = (
    "OPENROUTER_", "OPENAI_", "GITHUB_", "VERCEL_", "NETLIFY_", "CLOUDFLARE_",
    "JWT_", "SUPABASE_", "DATABASE_", "REDIS_", "DEVOS_", "AWS_", "GOOGLE_",
    "STRIPE_", "ANTHROPIC_",
)


class AppRuntimeState(str, Enum):
    UNKNOWN = "UNKNOWN"
    PENDING = "PENDING"
    BUILDING = "BUILDING"
    BUILT = "BUILT"
    STARTING = "STARTING"
    READY = "READY"
    FAILED = "FAILED"
    STOPPED = "STOPPED"
    UNSUPPORTED = "UNSUPPORTED"


@dataclass
class AppRuntimeSpec:
    user_id: str
    project_id: str
    kind: str = "UNKNOWN_APP"
    build_command: Optional[list[str]] = None
    start_command: Optional[list[str]] = None
    install_command: Optional[list[str]] = None
    port: int = 0  # 0 = allocate
    allow_network: bool = False  # default deny; npm install needs network when True


@dataclass
class AppRuntimeStatus:
    state: AppRuntimeState
    detail: str = ""
    port: Optional[int] = None
    pid: Optional[int] = None
    logs_tail: str = ""
    evidence: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "state": self.state.value,
            "detail": self.detail,
            "port": self.port,
            "pid": self.pid,
            "logs_tail": self.logs_tail[-4000:],
            "evidence": self.evidence,
        }


def filter_env(extra: Optional[dict] = None, *, allow_network: bool = False) -> dict:
    """Deny-by-default environment for application children."""
    out = {
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "HOME": os.environ.get("HOME", "/tmp"),
        "LANG": "C.UTF-8",
        "NODE_ENV": "production",
        "CI": "true",
        "npm_config_yes": "true",
        "npm_config_fund": "false",
        "npm_config_audit": "false",
    }
    if allow_network:
        # npm registry only via normal PATH tools; still no secrets
        out["npm_config_registry"] = os.environ.get("npm_config_registry", "https://registry.npmjs.org/")
    if extra:
        for k, v in extra.items():
            ku = k.upper()
            if k in _BLOCKED_ENV_KEYS or ku in _BLOCKED_ENV_KEYS:
                continue
            if any(ku.startswith(p) for p in _BLOCKED_PREFIXES):
                continue
            if any(x in ku for x in ("SECRET", "TOKEN", "PASSWORD", "API_KEY", "PRIVATE_KEY")):
                continue
            out[k] = str(v)
    return out


def _pm_commands(pm: str, script: str) -> list[str]:
    if pm == "pnpm":
        return ["pnpm", "run", script] if script != "install" else ["pnpm", "install", "--frozen-lockfile"]
    if pm == "yarn":
        return ["yarn", script] if script != "install" else ["yarn", "install", "--frozen-lockfile"]
    if pm == "bun":
        return ["bun", "run", script] if script != "install" else ["bun", "install"]
    # npm default
    if script == "install":
        return ["npm", "ci"] if False else ["npm", "install", "--no-fund", "--no-audit"]
    return ["npm", "run", script]


# In-memory registry of running processes (per process lifetime)
_RUNTIME: dict[str, "ApplicationRuntime"] = {}


def runtime_key(user_id: str, project_id: str) -> str:
    return f"{user_id}:{project_id}"


class ApplicationRuntime:
    def __init__(self, spec: AppRuntimeSpec):
        self.spec = spec
        self.fs = FileService(spec.user_id, spec.project_id)
        self.status = AppRuntimeStatus(state=AppRuntimeState.PENDING)
        self._proc: Optional[asyncio.subprocess.Process] = None
        self._log_buf: list[str] = []
        self._port: Optional[int] = None

    def _cwd(self) -> str:
        return str(self.fs.root)

    async def _run_cmd(
        self,
        cmd: list[str],
        *,
        timeout: float = 300,
        allow_network: bool = False,
        env_extra: Optional[dict] = None,
    ) -> tuple[int, str, str]:
        env = filter_env(env_extra, allow_network=allow_network)
        self._log_buf.append(f"$ {' '.join(cmd)}\n")
        try:
            run_cmd = wrap_command(cmd, cwd=self._cwd(), net=allow_network)
            proc = await asyncio.create_subprocess_exec(
                *run_cmd,
                cwd=self._cwd(),
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as e:
            return 127, "", f"command not found: {cmd[0]} ({e})"
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return -1, "", f"timeout after {timeout}s"
        out = stdout.decode(errors="replace")
        err = stderr.decode(errors="replace")
        self._log_buf.append(out)
        self._log_buf.append(err)
        return proc.returncode or 0, out, err

    def _detect_and_plan(self) -> dict:
        info = detect_application(self.fs)
        pm = info.get("package_manager") or "npm"
        kind = info.get("kind") or "UNKNOWN_APP"
        self.spec.kind = kind
        scripts = info.get("scripts") or {}
        if kind in ("NEXTJS_APP", "VITE_APP", "REACT_APP", "NODE_APP"):
            self.spec.install_command = _pm_commands(pm, "install")
            if "build" in scripts:
                self.spec.build_command = _pm_commands(pm, "build")
            elif kind == "NEXTJS_APP":
                self.spec.build_command = _pm_commands(pm, "build")
            if "start" in scripts:
                self.spec.start_command = _pm_commands(pm, "start")
            elif "preview" in scripts:
                self.spec.start_command = _pm_commands(pm, "preview")
            elif kind == "NEXTJS_APP":
                self.spec.start_command = ["npx", "next", "start", "-H", "127.0.0.1", "-p", "0"]
        return info

    async def install(self, timeout: float = 600) -> AppRuntimeStatus:
        info = self._detect_and_plan()
        if self.spec.kind in ("STATIC_SITE", "UNKNOWN_APP", "PYTHON_APP") and not self.spec.install_command:
            self.status = AppRuntimeStatus(
                state=AppRuntimeState.UNSUPPORTED if self.spec.kind == "UNKNOWN_APP" else AppRuntimeState.BUILT,
                detail="no install required" if self.spec.kind == "STATIC_SITE" else "unsupported",
                evidence={"detection": info},
            )
            return self.status
        if not self.spec.install_command:
            self.status = AppRuntimeStatus(state=AppRuntimeState.FAILED, detail="no install command")
            return self.status
        self.status = AppRuntimeStatus(state=AppRuntimeState.BUILDING, detail="installing dependencies")
        code, out, err = await self._run_cmd(
            self.spec.install_command, timeout=timeout, allow_network=True
        )
        if code != 0:
            self.status = AppRuntimeStatus(
                state=AppRuntimeState.FAILED,
                detail="install failed",
                logs_tail="".join(self._log_buf)[-4000:],
                evidence={"exit_code": code, "stderr_tail": err[-2000:]},
            )
            return self.status
        self.status = AppRuntimeStatus(
            state=AppRuntimeState.BUILT,
            detail="dependencies installed",
            logs_tail="".join(self._log_buf)[-4000:],
            evidence={"exit_code": 0, "detection": info, "isolation": isolation_available()},
        )
        return self.status

    async def build(self, timeout: float = 600) -> AppRuntimeStatus:
        if not self.spec.build_command:
            self._detect_and_plan()
        if self.spec.kind == "STATIC_SITE":
            self.status = AppRuntimeStatus(state=AppRuntimeState.BUILT, detail="static site — no build")
            return self.status
        if not self.spec.build_command:
            self.status = AppRuntimeStatus(state=AppRuntimeState.FAILED, detail="no build command")
            return self.status
        self.status = AppRuntimeStatus(state=AppRuntimeState.BUILDING, detail="building")
        # Prefer offline after install; still allow network for next telemetry opt-out
        code, out, err = await self._run_cmd(
            self.spec.build_command,
            timeout=timeout,
            allow_network=False,
            env_extra={"NEXT_TELEMETRY_DISABLED": "1"},
        )
        if code != 0:
            self.status = AppRuntimeStatus(
                state=AppRuntimeState.FAILED,
                detail="build failed",
                logs_tail="".join(self._log_buf)[-4000:],
                evidence={"exit_code": code, "stderr_tail": err[-2000:]},
            )
            return self.status
        self.status = AppRuntimeStatus(
            state=AppRuntimeState.BUILT,
            detail="build succeeded",
            logs_tail="".join(self._log_buf)[-4000:],
            evidence={"exit_code": 0},
        )
        return self.status

    async def start(self, port: int = 3911, timeout: float = 60) -> AppRuntimeStatus:
        await self.stop()
        if not self.spec.start_command:
            self._detect_and_plan()
        if self.spec.kind == "STATIC_SITE":
            self.status = AppRuntimeStatus(
                state=AppRuntimeState.READY,
                detail="static — use FileService preview, not app runtime",
                evidence={"static": True},
            )
            return self.status
        if not self.spec.start_command:
            self.status = AppRuntimeStatus(state=AppRuntimeState.FAILED, detail="no start command")
            return self.status
        # Force bind localhost only
        cmd = list(self.spec.start_command)
        env_extra = {
            "PORT": str(port),
            "HOST": "127.0.0.1",
            "HOSTNAME": "127.0.0.1",
            "NEXT_TELEMETRY_DISABLED": "1",
        }
        self.status = AppRuntimeStatus(state=AppRuntimeState.STARTING, detail="starting", port=port)
        env = filter_env(env_extra, allow_network=False)
        try:
            self._proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=self._cwd(),
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
        except FileNotFoundError as e:
            self.status = AppRuntimeStatus(state=AppRuntimeState.FAILED, detail=str(e))
            return self.status
        self._port = port
        # Health poll
        deadline = time.time() + timeout
        healthy = False
        import urllib.request
        while time.time() < deadline:
            if self._proc.returncode is not None:
                out = ""
                if self._proc.stdout:
                    try:
                        out = (await self._proc.stdout.read()).decode(errors="replace")
                    except Exception:
                        pass
                self.status = AppRuntimeStatus(
                    state=AppRuntimeState.FAILED,
                    detail="process exited before ready",
                    logs_tail=out[-4000:],
                    evidence={"exit_code": self._proc.returncode},
                )
                return self.status
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=1)
                healthy = True
                break
            except Exception:
                await asyncio.sleep(0.5)
        if not healthy:
            await self.stop()
            self.status = AppRuntimeStatus(
                state=AppRuntimeState.FAILED,
                detail="health check timeout",
                port=port,
            )
            return self.status
        self.status = AppRuntimeStatus(
            state=AppRuntimeState.READY,
            detail="listening",
            port=port,
            pid=self._proc.pid,
            evidence={"health": "ok", "bind": "127.0.0.1"},
        )
        _RUNTIME[runtime_key(self.spec.user_id, self.spec.project_id)] = self
        return self.status

    async def stop(self) -> AppRuntimeStatus:
        if self._proc and self._proc.returncode is None:
            try:
                self._proc.send_signal(signal.SIGTERM)
                try:
                    await asyncio.wait_for(self._proc.wait(), timeout=5)
                except asyncio.TimeoutError:
                    self._proc.kill()
                    await self._proc.wait()
            except ProcessLookupError:
                pass
        self._proc = None
        key = runtime_key(self.spec.user_id, self.spec.project_id)
        _RUNTIME.pop(key, None)
        self.status = AppRuntimeStatus(state=AppRuntimeState.STOPPED, detail="stopped", port=self._port)
        return self.status

    async def restart(self) -> AppRuntimeStatus:
        port = self._port or 3911
        await self.stop()
        return await self.start(port=port)


def get_runtime(user_id: str, project_id: str) -> Optional[ApplicationRuntime]:
    return _RUNTIME.get(runtime_key(user_id, project_id))
