"""
Execution Layer — Terminal.

Deliberately NOT the same code path as governance/sandbox.py's
SandboxedExecutor. That executor is for Brain-authored code snippets:
ephemeral work dir, wiped after each run, used via the write_python/
write_bash/write_node tool contracts with HITL gating for autonomous
agent actions.

This is for a human directly typing into their own project's terminal in
the IDE. It runs commands *in* the persistent project directory (so git,
npm install, pip install, etc. actually affect the project), one command
at a time, with output streamed back. It is not a full PTY (no curses
apps like vim/htop) — that would need ptyprocess/node-pty-equivalent,
which we're deliberately not adding as a dependency to stay inside the
requirements-lite footprint for the Acer Aspire One target. Build tools,
git, package managers, and scripts all work fine without a real PTY.

Trust model: this is direct human action inside a project the human
already owns/controls, not an autonomous Brain-invoked shell action —
so it is NOT queued through HITL per command (that would make an
interactive terminal unusable). It IS still denylist-checked for
catastrophic patterns, output-capped, and timeout-capped. Autonomous
agent-invoked shell execution continues to go through write_bash +
existing HITL gating, unchanged.
"""
import asyncio
import logging
import re
from pathlib import Path

from execution.files import PROJECTS_DIR

logger = logging.getLogger("devos.terminal")

MAX_OUTPUT_BYTES = 512_000
DEFAULT_TIMEOUT_S = 60

# Catastrophic-pattern denylist — not a full sandbox, just guards against the
# handful of one-command disasters (fork bombs, rm -rf /, disk wipes).
DENYLIST_PATTERNS = [
    r"rm\s+-rf\s+/(\s|$)",
    r"rm\s+-rf\s+/\*",
    r"rm\s+(-[a-zA-Z]*f[a-zA-Z]*r|--force.*--recursive|--recursive.*--force)\s+/",  # rm -fr / variants
    r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;",   # classic fork bomb
    r"mkfs\.",
    r"dd\s+.*of=/dev/(sda|nvme|disk)",
    r">\s*/dev/sd[a-z]",
]


class DeniedCommand(Exception):
    pass


class TerminalService:
    def __init__(self, user_id: str, project_id: str):
        self.root = (PROJECTS_DIR / user_id / project_id).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _check_denylist(self, command: str):
        for pattern in DENYLIST_PATTERNS:
            if re.search(pattern, command, re.IGNORECASE):
                raise DeniedCommand(f"Command blocked by safety denylist: matches '{pattern}'")

    async def run(self, command: str, timeout: int = DEFAULT_TIMEOUT_S) -> dict:
        """Run one command to completion, buffered. Used by the plain HTTP route."""
        self._check_denylist(command)
        proc = await asyncio.create_subprocess_shell(
            command, cwd=str(self.root),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            status = "success" if proc.returncode == 0 else "failed"
        except asyncio.TimeoutError:
            proc.kill()
            await proc.communicate()
            return {"status": "timeout", "stdout": "", "stderr": f"Command timed out after {timeout}s",
                    "exit_code": -1}
        return {
            "status": status,
            "stdout": stdout[:MAX_OUTPUT_BYTES].decode(errors="replace"),
            "stderr": stderr[:MAX_OUTPUT_BYTES // 2].decode(errors="replace"),
            "exit_code": proc.returncode,
        }

    async def run_streaming(self, command: str, on_chunk, timeout: int = DEFAULT_TIMEOUT_S) -> dict:
        """Run one command, calling on_chunk(stream_name, bytes) as output arrives.
        Used by the WebSocket route so the IDE terminal feels live."""
        self._check_denylist(command)
        proc = await asyncio.create_subprocess_shell(
            command, cwd=str(self.root),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )

        async def pump(stream, name):
            total = 0
            while True:
                chunk = await stream.readline()
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_OUTPUT_BYTES:
                    await on_chunk(name, b"\n[OUTPUT TRUNCATED]\n")
                    break
                await on_chunk(name, chunk)

        try:
            await asyncio.wait_for(
                asyncio.gather(pump(proc.stdout, "stdout"), pump(proc.stderr, "stderr")),
                timeout=timeout,
            )
            await proc.wait()
            status = "success" if proc.returncode == 0 else "failed"
        except asyncio.TimeoutError:
            proc.kill()
            await proc.communicate()
            return {"status": "timeout", "exit_code": -1}
        return {"status": status, "exit_code": proc.returncode}
