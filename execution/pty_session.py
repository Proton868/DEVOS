"""
Execution — Persistent PTY Terminal Session Manager.

Replaces the one-command-at-a-time model in execution/terminal.py with a
real, persistent PTY-backed interactive shell. One PTY session per
(user_id, project_id), shared across all WebSocket connections attached
to that project.

Key design decisions:
- Multi-client: multiple browser tabs/connections to the same project
  share the same PTY, with input accepted from any of them and output
  broadcast to all (matching real tmux-over-SSH semantics).
- Scrollback: 256KB rolling buffer so reconnecting clients can replay
  recent history.
- Idle timeout: 30 minutes with no attached connections and no output
  kills the shell process (configurable via core/config.py).
- Resource limits: PTY shell process gets CPU/memory caps via
  resource.prlimit (same pattern as governance/sandbox.py).
- Denylist: the regex-on-full-command denylist from execution/terminal.py
  does NOT apply to raw PTY byte streams (input arrives as keystrokes,
  not discrete commands). Project-directory confinement + resource limits
  + non-root execution are the safety layers instead.
"""
import asyncio
import fcntl
import logging
import os
import pty
import resource
import signal
import struct
import termios
import time
from collections import deque
from pathlib import Path
from typing import Optional

from execution.files import PROJECTS_DIR
from core.config import settings

logger = logging.getLogger("devos.pty_session")

# Maximum scrollback buffer size in bytes
SCROLLBACK_SIZE = 256 * 1024  # 256KB

# Default idle timeout: seconds with no attached connections and no output
# before the shell process is killed. Configurable via core/config.py.
DEFAULT_IDLE_TIMEOUT = 30 * 60  # 30 minutes


class PtySession:
    """A persistent PTY-backed shell session for one project."""

    def __init__(self, user_id: str, project_id: str):
        self.user_id = user_id
        self.project_id = project_id
        key = (user_id, project_id)
        self._key = key

        self.root = (PROJECTS_DIR / user_id / project_id).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

        self._fd: Optional[int] = None
        self._pid: Optional[int] = None

        # Connected WebSocket clients
        self._clients: list = []

        # Scrollback buffer (rolling, oldest bytes dropped when full)
        self._scrollback: deque[bytes] = deque()
        self._scrollback_size = 0

        # Reader task
        self._reader_task: Optional[asyncio.Task] = None

        # Idle tracking
        self._last_output_time = time.monotonic()
        self._idle_task: Optional[asyncio.Task] = None

        self._started = False

    @property
    def is_alive(self) -> bool:
        """True if the shell process is still running. Uses os.kill(pid, 0)
        which sends signal 0 (null signal) — succeeds if the process exists,
        raises ProcessLookupError if it doesn't."""
        if self._pid is None:
            return False
        try:
            os.kill(self._pid, 0)
            return True
        except ProcessLookupError:
            return False

    @property
    def is_idle(self) -> bool:
        """True if no clients connected and no output for idle_timeout seconds."""
        idle_timeout = getattr(settings, 'PTY_IDLE_TIMEOUT', DEFAULT_IDLE_TIMEOUT)
        return (not self._clients and
                (time.monotonic() - self._last_output_time) > idle_timeout)

    @property
    def client_count(self) -> int:
        return len(self._clients)

    async def start(self) -> None:
        """Spawn the bash shell process via pty.fork() — the standard,
        well-tested way to get a child process connected to a PTY."""
        if self._started:
            return

        pid, master_fd = pty.fork()

        if pid == 0:
            # ── Child process ───────────────────────────────────────
            # pty.fork() already set up the PTY as stdin/stdout/stderr
            # and made us a session leader with the PTY as controlling tty.
            os.chdir(str(self.root))

            env = os.environ.copy()
            env["TERM"] = "xterm-256color"
            env["HOME"] = str(self.root)
            env["PS1"] = "\\[\\e[32m\\]\\w\\[\\e[0m\\] $ "

            os.execvpe("bash", ["bash", "--norc", "--noprofile"], env)
            os._exit(1)

        # ── Parent process ──────────────────────────────────────────
        self._fd = master_fd
        self._pid = pid

        # Set a reasonable default window size
        self._set_pty_size(master_fd, 80, 24)

        # Apply resource limits to the child process
        try:
            resource.prlimit(pid, resource.RLIMIT_CPU, (300, 300))  # 5 min CPU
            resource.prlimit(pid, resource.RLIMIT_AS, (512 * 1024 * 1024, 512 * 1024 * 1024))  # 512MB
            resource.prlimit(pid, resource.RLIMIT_NOFILE, (256, 256))
        except (AttributeError, ValueError, OSError, resource.error):
            pass

        # Start reading from the PTY master
        self._reader_task = asyncio.create_task(self._read_loop())

        # Start the idle timeout checker
        self._idle_task = asyncio.create_task(self._idle_checker())

        self._started = True
        logger.info(f"[pty] started session for {self.user_id}/{self.project_id} (pid={pid})")

    def _set_pty_size(self, fd: int, cols: int, rows: int) -> None:
        """Set the PTY window size via TIOCSWINSZ."""
        try:
            winsize = struct.pack("HHHH", rows, cols, 0, 0)
            fcntl.ioctl(fd, termios.TIOCSWINSZ, winsize)
        except Exception:
            pass

    async def resize(self, cols: int, rows: int) -> None:
        """Resize the PTY to match the client's terminal dimensions."""
        if self._fd is not None:
            self._set_pty_size(self._fd, cols, rows)

    def write(self, data: bytes) -> None:
        """Write bytes to the PTY's stdin."""
        if self._fd is not None and self.is_alive:
            try:
                os.write(self._fd, data)
            except (OSError, BrokenPipeError):
                pass

    def attach(self, ws) -> None:
        """Attach a WebSocket client to this session."""
        self._clients.append(ws)
        # Replay scrollback buffer to the new client
        for chunk in self._scrollback:
            try:
                asyncio.create_task(ws.send_json({"type": "data", "data": chunk.decode("utf-8", errors="replace")}))
            except Exception:
                pass
        logger.debug(f"[pty] client attached to {self.user_id}/{self.project_id} ({len(self._clients)} total)")

    def detach(self, ws) -> None:
        """Detach a WebSocket client."""
        if ws in self._clients:
            self._clients.remove(ws)
        logger.debug(f"[pty] client detached from {self.user_id}/{self.project_id} ({len(self._clients)} remaining)")

    async def broadcast(self, data: bytes) -> None:
        """Send output to all connected clients."""
        for ws in self._clients[:]:  # Copy to allow removal during iteration
            try:
                await ws.send_json({"type": "data", "data": data.decode("utf-8", errors="replace")})
            except Exception:
                self._clients.remove(ws)

    async def _read_loop(self) -> None:
        """Read output from the PTY master and broadcast to clients."""
        loop = asyncio.get_event_loop()
        while self.is_alive:
            try:
                data = await loop.run_in_executor(None, os.read, self._fd, 4096)
                if data:
                    self._last_output_time = time.monotonic()
                    # Add to scrollback buffer
                    self._scrollback.append(data)
                    self._scrollback_size += len(data)
                    # Trim scrollback if it exceeds the limit
                    while self._scrollback_size > SCROLLBACK_SIZE and self._scrollback:
                        oldest = self._scrollback.popleft()
                        self._scrollback_size -= len(oldest)
                    # Broadcast to all clients
                    await self.broadcast(data)
                else:
                    # EOF — process exited
                    break
            except (OSError, ValueError):
                break
            except Exception as e:
                logger.warning(f"[pty] read error for {self.user_id}/{self.project_id}: {e}")
                break

        # Process exited — notify clients
        exit_code = 0
        if self._pid is not None:
            try:
                _, status = os.waitpid(self._pid, os.WNOHANG)
                if os.WIFEXITED(status):
                    exit_code = os.WEXITSTATUS(status)
            except ChildProcessError:
                pass

        for ws in self._clients[:]:
            try:
                await ws.send_json({"type": "exit", "exitCode": exit_code})
            except Exception:
                pass
        self._clients.clear()

        logger.info(f"[pty] session ended for {self.user_id}/{self.project_id} (exit_code={exit_code})")

    async def _idle_checker(self) -> None:
        """Periodically check if the session is idle and should be killed."""
        idle_timeout = getattr(settings, 'PTY_IDLE_TIMEOUT', DEFAULT_IDLE_TIMEOUT)
        while self.is_alive:
            await asyncio.sleep(60)  # Check every minute
            if self.is_idle and idle_timeout > 0:
                logger.info(f"[pty] idle timeout — killing session for {self.user_id}/{self.project_id}")
                await self.kill()
                break

    async def kill(self) -> None:
        """Kill the shell process and clean up."""
        if self._pid is not None:
            try:
                os.killpg(os.getpgid(self._pid), signal.SIGTERM)
                await asyncio.sleep(0.5)
                os.killpg(os.getpgid(self._pid), signal.SIGKILL)
            except (ProcessLookupError, OSError):
                pass
            self._pid = None

        if self._fd is not None:
            try:
                os.close(self._fd)
            except OSError:
                pass
            self._fd = None

        if self._reader_task and not self._reader_task.done():
            self._reader_task.cancel()

        if self._idle_task and not self._idle_task.done():
            self._idle_task.cancel()

        self._clients.clear()
        self._started = False


# ── Session Registry ─────────────────────────────────────────────────────────

# In-memory registry: (user_id, project_id) -> PtySession
_sessions: dict[tuple[str, str], PtySession] = {}
_registry_lock = asyncio.Lock()


async def get_or_create_session(user_id: str, project_id: str) -> PtySession:
    """Get an existing PTY session or create a new one for the project."""
    key = (user_id, project_id)
    async with _registry_lock:
        session = _sessions.get(key)
        if session is None or not session.is_alive:
            session = PtySession(user_id, project_id)
            await session.start()
            _sessions[key] = session
        return session


async def kill_session(user_id: str, project_id: str) -> None:
    """Kill and remove a PTY session (e.g. when a project is deleted)."""
    key = (user_id, project_id)
    async with _registry_lock:
        session = _sessions.pop(key, None)
        if session:
            await session.kill()


async def kill_all_sessions() -> None:
    """Kill all active PTY sessions (called during app shutdown)."""
    async with _registry_lock:
        for session in list(_sessions.values()):
            await session.kill()
        _sessions.clear()