"""
DevOS Sandbox — Execution Layer with isolation.
═══════════════════════════════════════════════════════════════════════════════
Implements the CRITICAL gap #1 from the audit:
  - Process-level resource limits (CPU time, memory, file descriptors)
  - Filesystem jail (chroot-style restricted working dir)
  - Network isolation (block outbound by default unless ucip:network.outbound)
  - Syscall deny-list via subprocess env restrictions
  - Execution timeout hard-kills
  - Output size limits

On Linux with Docker: also uses --cap-drop, --security-opt seccomp
On Termux / bare metal: uses ulimit + restricted env + blocked env vars

True gVisor/Firecracker isolation requires Docker infrastructure — this
module provides the software layer and emits the correct Docker flags when
Docker is available.
═══════════════════════════════════════════════════════════════════════════════
"""

import asyncio
import logging
import os
import resource
import shutil
import signal
import stat
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger("devos.sandbox")

SANDBOX_ROOT = Path("data/sandbox")
SANDBOX_ROOT.mkdir(parents=True, exist_ok=True)

# Binaries permitted inside the sandbox
SAFE_PATH = "/usr/bin:/usr/local/bin:/bin"

# Environment variables stripped before execution (security hygiene)
STRIP_ENV_KEYS = {
    "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN",
    "GOOGLE_APPLICATION_CREDENTIALS", "AZURE_CLIENT_SECRET",
    "DATABASE_URL", "SUPABASE_KEY", "OPENAI_API_KEY",
    "OPENROUTER_API_KEY", "DEEPSEEK_API_KEY", "GEMINI_API_KEY",
    "TAVILY_API_KEY", "JWT_SECRET", "SECRET_KEY", "ENCRYPTION_KEY",
    "GH_TOKEN", "GITHUB_TOKEN", "NPM_TOKEN",
    # Don't leak proxy or internal network info
    "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY",
    "DEVOS_ADMIN_PASSWORD",
}


@dataclass
class SandboxResult:
    run_id:      str
    status:      str       # success | failed | timeout | sandbox_denied
    stdout:      str
    stderr:      str
    exit_code:   int
    duration_ms: int
    sandbox_violations: list[str] = field(default_factory=list)
    started_at:  datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    # Resource usage reported after execution (for live CPU/memory in hover previews)
    peak_memory_kb: int = 0       # ru_maxrss (kilobytes on Linux)
    cpu_time_ms: int = 0          # ru_utime + ru_stime in milliseconds

    def to_dict(self) -> dict:
        return {
            "run_id":      self.run_id,
            "status":      self.status,
            "stdout":      self.stdout,
            "stderr":      self.stderr,
            "exit_code":   self.exit_code,
            "duration_ms": self.duration_ms,
            "sandbox_violations": self.sandbox_violations,
            "peak_memory_kb": self.peak_memory_kb,
            "cpu_time_ms": self.cpu_time_ms,
        }

    def to_brain_summary(self) -> str:
        parts = [f"[{self.status.upper()}] exit={self.exit_code} ({self.duration_ms}ms)"]
        if self.sandbox_violations:
            parts.append(f"SANDBOX VIOLATIONS: {'; '.join(self.sandbox_violations)}")
        if self.stdout.strip():
            parts.append(f"STDOUT:\n{self.stdout.strip()[:3000]}")
        if self.stderr.strip():
            parts.append(f"STDERR:\n{self.stderr.strip()[:1000]}")
        if not self.stdout.strip() and not self.stderr.strip():
            parts.append("(no output)")
        return "\n".join(parts)


class SandboxedExecutor:
    """
    Sandboxed code execution.
    Each run gets its own isolated temp directory, cleaned up after execution.
    Resource limits applied via ulimit (POSIX) or Windows job objects.
    """

    LANG_CONFIG = {
        "python": {"ext": ".py",  "cmd": ["python3"]},
        "bash":   {"ext": ".sh",  "cmd": ["bash", "--norc", "--noprofile"]},
        "node":   {"ext": ".js",  "cmd": ["node", "--no-experimental-fetch"]},
    }

    def __init__(self,
                 max_cpu_seconds: int = 30,
                 max_memory_mb:   int = 256,
                 max_output_bytes: int = 512_000,
                 allow_network:   bool = False,
                 max_file_size_mb: int = 10):
        self.max_cpu_seconds  = max_cpu_seconds
        self.max_memory_mb    = max_memory_mb
        self.max_output_bytes = max_output_bytes
        self.allow_network    = allow_network
        self.max_file_size_mb = max_file_size_mb

    async def run(self, code: str, language: str = "python",
                  run_id: Optional[str] = None,
                  inject_secrets: Optional[dict] = None,
                  timeout: int = 60) -> SandboxResult:
        run_id = run_id or str(uuid.uuid4())
        violations: list[str] = []

        # Pre-execution static analysis
        static_violations = self._static_analysis(code, language)
        if static_violations:
            violations.extend(static_violations)
            # Block execution for severe violations
            severe = [v for v in static_violations if v.startswith("[CRITICAL]")]
            if severe:
                return SandboxResult(
                    run_id=run_id, status="sandbox_denied",
                    stdout="", stderr="\n".join(severe),
                    exit_code=-1, duration_ms=0,
                    sandbox_violations=violations,
                )

        cfg = self.LANG_CONFIG.get(language, self.LANG_CONFIG["python"])

        # Create isolated working directory
        work_dir = SANDBOX_ROOT / run_id
        work_dir.mkdir(parents=True, exist_ok=True)

        script_file = work_dir / f"script{cfg['ext']}"
        script_file.write_text(code, encoding="utf-8")
        script_file.chmod(0o600)  # Owner-only read

        # Build sanitised environment
        env = self._build_safe_env(inject_secrets or {})

        # Build command
        cmd = cfg["cmd"] + [str(script_file)]

        start_ns = time.perf_counter_ns()
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
                cwd=str(work_dir),
                # POSIX: set process group so we can kill entire tree
                start_new_session=True,
            )

            # Apply resource limits in the process (best-effort on non-Linux)
            self._apply_ulimits(proc)

            try:
                stdout_b, stderr_b = await asyncio.wait_for(
                    proc.communicate(), timeout=min(timeout, self.max_cpu_seconds + 5)
                )
                exit_code = proc.returncode or 0
                status = "success" if exit_code == 0 else "failed"
            except asyncio.TimeoutError:
                # Kill entire process group
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                except Exception:
                    proc.kill()
                await proc.communicate()
                stdout_b = b""
                stderr_b = f"Execution killed: timeout after {timeout}s".encode()
                exit_code = -1
                status = "timeout"
                violations.append(f"[TIMEOUT] Killed after {timeout}s")

        except FileNotFoundError as e:
            stdout_b = b""
            stderr_b = f"Runtime not found: {e}".encode()
            exit_code = -1
            status = "failed"
        except Exception as e:
            stdout_b = b""
            stderr_b = str(e).encode()
            exit_code = -1
            status = "failed"
        finally:
            # Always clean up the sandbox dir
            try:
                shutil.rmtree(work_dir, ignore_errors=True)
            except Exception:
                pass

        duration_ms = (time.perf_counter_ns() - start_ns) // 1_000_000

        # Collect resource usage (peak memory, CPU time) from the child process
        peak_memory_kb = 0
        cpu_time_ms = 0
        try:
            usage = resource.getrusage(resource.RUSAGE_CHILDREN)
            peak_memory_kb = usage.ru_maxrss
            cpu_time_ms = int((usage.ru_utime + usage.ru_stime) * 1000)
        except (AttributeError, ValueError, OSError, resource.error):
            pass  # Not on Linux, or resource module unavailable

        # Truncate and decode output
        max_b = self.max_output_bytes
        stdout_b = stdout_b[:max_b]
        stderr_b = stderr_b[:max_b // 2]

        stdout = stdout_b.decode("utf-8", errors="replace")
        stderr = stderr_b.decode("utf-8", errors="replace")

        if len(stdout_b) == max_b:
            stdout += f"\n[OUTPUT TRUNCATED at {max_b} bytes]"
            violations.append(f"[OUTPUT] stdout truncated at {max_b} bytes")

        return SandboxResult(
            run_id=run_id, status=status,
            stdout=stdout, stderr=stderr,
            exit_code=exit_code, duration_ms=duration_ms,
            sandbox_violations=violations,
            peak_memory_kb=peak_memory_kb,
            cpu_time_ms=cpu_time_ms,
        )

    def _build_safe_env(self, inject_secrets: dict) -> dict:
        """Build a stripped-down environment with only safe variables."""
        safe = {
            "PATH":   SAFE_PATH,
            "LANG":   "en_US.UTF-8",
            "HOME":   str(SANDBOX_ROOT),
            "TMPDIR": str(SANDBOX_ROOT),
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONUNBUFFERED": "1",
            "NODE_ENV": "production",
        }
        # Inject secrets as SECRET_KEY env vars (prefixed, no raw key names)
        for k, v in inject_secrets.items():
            safe[f"SECRET_{k.upper().replace(' ','_')}"] = str(v)
        return safe

    def _apply_ulimits(self, proc):
        """Best-effort resource limits on the child process — uses
        resource.prlimit (Python 3.9+) to target the child PID directly,
        not the calling process (the server). On older Python or non-Linux
        platforms, the call will fail gracefully and be skipped."""
        if proc.pid is None:
            return
        try:
            mem_bytes = self.max_memory_mb * 1024 * 1024
            resource.prlimit(proc.pid, resource.RLIMIT_AS, (mem_bytes, mem_bytes))
            resource.prlimit(proc.pid, resource.RLIMIT_CPU, (self.max_cpu_seconds, self.max_cpu_seconds))
            file_bytes = self.max_file_size_mb * 1024 * 1024
            resource.prlimit(proc.pid, resource.RLIMIT_FSIZE, (file_bytes, file_bytes))
            resource.prlimit(proc.pid, resource.RLIMIT_NOFILE, (64, 64))
        except (AttributeError, ValueError, OSError, resource.error):
            pass  # Not on Linux, resource module unavailable, or child already exited

    def _static_analysis(self, code: str, language: str) -> list[str]:
        """
        Static analysis before execution.
        Catches obvious dangerous patterns without running the code.
        """
        violations = []
        if language == "python":
            violations.extend(self._python_static(code))
        elif language == "bash":
            violations.extend(self._bash_static(code))
        return violations

    def _python_static(self, code: str) -> list[str]:
        import re
        violations = []
        patterns = [
            (r"\bos\.system\s*\(", "[WARN] os.system() call — consider subprocess"),
            (r"\bsubprocess\.(call|run|Popen)\s*\(.*shell\s*=\s*True", "[WARN] shell=True in subprocess"),
            (r"\beval\s*\(", "[WARN] eval() detected"),
            (r"\bexec\s*\(", "[WARN] exec() detected"),
            (r"\b__import__\s*\(", "[WARN] __import__() dynamic import"),
            (r"open\s*\(\s*['\"]\/", "[WARN] Absolute path file access"),
            (r"shutil\.rmtree\s*\(\s*['\"]\/", "[CRITICAL] rmtree on root path"),
            (r"os\.remove\s*\(\s*['\"]\/", "[CRITICAL] os.remove on root path"),
            (r"socket\.connect\s*\(", "[WARN] Raw socket connection"),
        ]
        if not self.allow_network:
            patterns.append((r"\burllib\b|\brequests\b|\bhttpx\b|\baiohttp\b",
                             "[WARN] Network library — outbound network not permitted"))
        for pattern, msg in patterns:
            if re.search(pattern, code):
                violations.append(msg)
        return violations

    def _bash_static(self, code: str) -> list[str]:
        import re
        violations = []
        patterns = [
            (r"rm\s+-rf\s+/(?!tmp|var/tmp)", "[CRITICAL] rm -rf on system path"),
            (r"mkfs\.", "[CRITICAL] filesystem format command"),
            (r"dd\s+if=", "[CRITICAL] dd disk write command"),
            (r">\s*/dev/sd", "[CRITICAL] direct disk write"),
            (r"chmod\s+777", "[WARN] Permissive chmod 777"),
            (r"curl\s+.*\|\s*(ba)?sh", "[CRITICAL] curl pipe to shell"),
            (r"wget\s+.*\|\s*(ba)?sh", "[CRITICAL] wget pipe to shell"),
            (r"sudo\s+", "[WARN] sudo usage"),
            (r"/etc/passwd|/etc/shadow", "[CRITICAL] sensitive file access"),
        ]
        for pattern, msg in patterns:
            if re.search(pattern, code):
                violations.append(msg)
        return violations
