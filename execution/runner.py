"""
Execution Layer — runs code that the Brain writes.
Does NOT decide what to run. Does NOT reason about results.
Just executes faithfully and returns structured output to the Brain.

This is the PyRunner core, redesigned as a subordinate layer
that serves the Brain rather than being a standalone tool.
"""

import asyncio
import logging
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger("devos.execution")

# Absolute paths, not relative -- SCRIPT_DIR is used both as the subprocess's
# cwd AND to build script_file's path, which is then passed as a command
# argument. When both were relative ("data/scripts"), the argument path got
# resolved a second time against the cwd, producing a doubled, nonexistent
# path like data/scripts/data/scripts/{id}.py (found via testing in record.md
# Session 22 — every script run through this path was silently broken).
SCRIPT_DIR = Path("data/scripts").resolve()
VENV_DIR   = Path("data/venvs").resolve()
SCRIPT_DIR.mkdir(parents=True, exist_ok=True)
VENV_DIR.mkdir(parents=True, exist_ok=True)

LANG_CONFIG = {
    "python": {"ext": ".py",  "cmd": ["python3"]},
    "bash":   {"ext": ".sh",  "cmd": ["bash"]},
    "node":   {"ext": ".js",  "cmd": ["node"]},
}


class ExecutionResult:
    """Structured result from the Execution Layer back to the Brain."""

    def __init__(self, script_id: str, language: str, code: str,
                 stdout: str, stderr: str, exit_code: int,
                 duration_ms: int, status: str):
        self.script_id   = script_id
        self.language    = language
        self.code        = code
        self.stdout      = stdout
        self.stderr      = stderr
        self.exit_code   = exit_code
        self.duration_ms = duration_ms
        self.status      = status  # "success" | "failed" | "timeout"
        self.started_at  = datetime.now(timezone.utc)

    def to_dict(self) -> dict:
        return {
            "script_id":   self.script_id,
            "language":    self.language,
            "status":      self.status,
            "exit_code":   self.exit_code,
            "stdout":      self.stdout,
            "stderr":      self.stderr,
            "duration_ms": self.duration_ms,
        }

    def to_brain_summary(self) -> str:
        """Compact summary for Brain consumption."""
        parts = [f"[{self.status.upper()}] exit={self.exit_code} ({self.duration_ms}ms)"]
        if self.stdout.strip():
            parts.append(f"STDOUT:\n{self.stdout.strip()[:3000]}")
        if self.stderr.strip():
            parts.append(f"STDERR:\n{self.stderr.strip()[:1000]}")
        if not self.stdout.strip() and not self.stderr.strip():
            parts.append("(no output)")
        return "\n".join(parts)


class ExecutionLayer:
    """
    Receives code from the Brain, executes it, returns results.
    The Brain drives this layer — it never acts on its own.
    """

    async def run(
        self,
        code: str,
        language: str = "python",
        script_id: Optional[str] = None,
        env_vars: Optional[dict] = None,
        secrets: Optional[dict] = None,
        venv_path: Optional[str] = None,
        timeout: int = 60,
    ) -> dict:
        """
        Execute code. Return structured result dict for the Brain.
        This is the ONLY entry point — the Brain always calls this.
        """
        script_id = script_id or str(uuid.uuid4())
        cfg = LANG_CONFIG.get(language, LANG_CONFIG["python"])
        script_file = SCRIPT_DIR / f"{script_id}{cfg['ext']}"

        # Write code to file
        script_file.write_text(code, encoding="utf-8")

        # Build command
        if language == "python" and venv_path:
            python_bin = Path(venv_path) / "bin" / "python3"
            cmd = [str(python_bin) if python_bin.exists() else "python3", str(script_file)]
        else:
            cmd = cfg["cmd"] + [str(script_file)]

        # Build environment
        env = os.environ.copy()
        if env_vars:
            env.update(env_vars)
        if secrets:
            for k, v in secrets.items():
                env[f"SECRET_{k.upper()}"] = str(v)

        start_ns = time.perf_counter_ns()
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
                cwd=str(SCRIPT_DIR),
            )
            try:
                stdout_b, stderr_b = await asyncio.wait_for(
                    proc.communicate(), timeout=timeout
                )
                exit_code = proc.returncode if proc.returncode is not None else 0
                status = "success" if exit_code == 0 else "failed"
            except asyncio.TimeoutError:
                proc.kill()
                await proc.communicate()
                stdout_b, stderr_b = b"", b"Execution timed out."
                exit_code = -1
                status = "timeout"
        except Exception as e:
            stdout_b = b""
            stderr_b = str(e).encode()
            exit_code = -1
            status = "failed"
        finally:
            script_file.unlink(missing_ok=True)

        duration_ms = (time.perf_counter_ns() - start_ns) // 1_000_000
        stdout = stdout_b.decode("utf-8", errors="replace")
        stderr = stderr_b.decode("utf-8", errors="replace")

        # Truncate massive outputs before returning to Brain
        MAX = 50_000
        if len(stdout) > MAX:
            stdout = stdout[-MAX:] + f"\n[...truncated to last {MAX} chars]"
        if len(stderr) > MAX:
            stderr = stderr[-MAX:] + f"\n[...truncated]"

        result = ExecutionResult(
            script_id=script_id, language=language, code=code,
            stdout=stdout, stderr=stderr, exit_code=exit_code,
            duration_ms=duration_ms, status=status,
        )

        logger.info(f"[Execution] {script_id[:8]} {language} → {status} ({duration_ms}ms)")
        return result.to_dict()

    async def install_packages(
        self, packages: list[str], env_name: str, python_version: str = "3.11"
    ) -> dict:
        """
        Create a venv and install packages.
        Called by Brain when it needs a specific library.
        """
        venv_path = VENV_DIR / env_name
        if not venv_path.exists():
            proc = await asyncio.create_subprocess_exec(
                "python3", "-m", "venv", str(venv_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await proc.communicate()
            if proc.returncode != 0:
                return {"success": False, "error": stderr.decode()}

        pip = venv_path / "bin" / "pip"
        proc = await asyncio.create_subprocess_exec(
            str(pip), "install", "--quiet", *packages,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        success = proc.returncode == 0
        return {
            "success": success,
            "venv_path": str(venv_path),
            "output": (stdout + stderr).decode(errors="replace")[:2000],
        }

    async def get_installed_packages(self, venv_path: str) -> list[str]:
        """List packages in a venv — Brain can use this to check what's available."""
        pip = Path(venv_path) / "bin" / "pip"
        if not pip.exists():
            return []
        proc = await asyncio.create_subprocess_exec(
            str(pip), "list", "--format=freeze",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        return [line.split("==")[0] for line in stdout.decode().splitlines() if "==" in line]
