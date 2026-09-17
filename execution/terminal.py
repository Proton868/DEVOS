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
catastrophic patterns, output-capped, and timeout-capped. Agent-invoked commands MUST go through AgentRuntime → run_command_in_project
→ run_governed → run_isolated (untrusted isolation). This module is NEVER
the agent command path.
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


    def _env(self) -> dict:
        """PATH for IDE terminal; host secrets/credentials stripped.

        Human IDE terminal is treated as trusted-local by default, but must
        not inherit server API keys / DB URLs from the service process.
        """
        import os
        from execution.governed_exec import scrub_env
        extras = [
            "/usr/local/sbin",
            "/usr/local/bin",
            "/usr/sbin",
            "/usr/bin",
            "/sbin",
            "/bin",
            str(Path.home() / ".local" / "bin"),
            "/home/ubuntu/.nvm/versions/node/current/bin",
        ]
        cur = os.environ.get("PATH", "")
        parts = [p for p in cur.split(":") if p] + [p for p in extras if p]
        seen = set()
        ordered = []
        for p in parts:
            if p not in seen:
                seen.add(p)
                ordered.append(p)
        return scrub_env(extra={
            "PATH": ":".join(ordered),
            "TERM": "xterm-256color",
            "HOME": str(Path.home()),
            "LANG": os.environ.get("LANG") or "C.UTF-8",
        })

    def _check_denylist(self, command: str):
        for pattern in DENYLIST_PATTERNS:
            if re.search(pattern, command, re.IGNORECASE):
                raise DeniedCommand(f"Command blocked by safety denylist: matches '{pattern}'")

    async def run(self, command: str, timeout: int = DEFAULT_TIMEOUT_S) -> dict:
        """Run one command to completion, buffered. Used by the plain HTTP route."""
        self._check_denylist(command)
        # Prefer bash -lc when available so login-style PATH applies
        import shlex
        shell_cmd = command
        if Path("/bin/bash").is_file():
            shell_cmd = f"/bin/bash -lc {shlex.quote(command)}"

        proc = await asyncio.create_subprocess_shell(
            shell_cmd,
            cwd=str(self.root),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
            env=self._env(),
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            status = "success" if proc.returncode == 0 else "failed"
        except asyncio.TimeoutError:
            proc.kill()
            await proc.communicate()
            return {"status": "timeout", "stdout": "", "stderr": f"Command timed out after {timeout}s",
                    "exit_code": -1}
        out = stdout[:MAX_OUTPUT_BYTES].decode(errors="replace")
        err = stderr[:MAX_OUTPUT_BYTES // 2].decode(errors="replace")
        if proc.returncode == 127 or "not found" in err.lower():
            # Helpful diagnostic for missing tools in the service environment
            hint = (
                "\n[devos] Command not found in terminal PATH. "
                "Install the tool on the host or use a full path "
                f"(cwd={self.root}, PATH prefix={self._env().get('PATH','')[:120]}…)."
            )
            if hint.strip() not in err:
                err = (err or "") + hint
        return {
            "status": status,
            "stdout": out,
            "stderr": err,
            "exit_code": proc.returncode,
        }

    async def run_streaming(self, command: str, on_chunk, timeout: int = DEFAULT_TIMEOUT_S) -> dict:
        """Run one command, calling on_chunk(stream_name, bytes) as output arrives.
        Used by the WebSocket route so the IDE terminal feels live."""
        self._check_denylist(command)
        import shlex
        shell_cmd = command
        if Path("/bin/bash").is_file():
            shell_cmd = f"/bin/bash -lc {shlex.quote(command)}"
        proc = await asyncio.create_subprocess_shell(
            shell_cmd,
            cwd=str(self.root),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
            env=self._env(),
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
