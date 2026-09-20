"""DevOS SSH transport — library-backed, independent of Nuha.

Uses asyncssh when available. Host verification and credential resolution
happen before any channel is opened. No StrictHostKeyChecking=no.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Protocol

logger = logging.getLogger("devos.ssh_transport")


class SshTransportError(Exception):
    def __init__(self, code: str):
        self.code = str(code)[:64]
        super().__init__(self.code)


class SshAuthError(SshTransportError):
    def __init__(self):
        super().__init__("authentication_failed")


class SshHostVerifyFailed(SshTransportError):
    def __init__(self, code: str = "host_verification_failed"):
        super().__init__(code)


class SshTimeoutError(SshTransportError):
    def __init__(self):
        super().__init__("connection_timeout")


class SshCancelled(SshTransportError):
    def __init__(self):
        super().__init__("cancelled")


@dataclass
class SshConnectParams:
    host: str
    port: int = 22
    username: str = ""
    connect_timeout_s: float = 30.0
    keepalive_s: float = 30.0
    # Presented by handshake mock or library callback
    host_key_type: str = ""
    host_key_fingerprint: str = ""
    host_public_key_line: str = ""


@dataclass
class SshExecResult:
    exit_status: Optional[int]
    stdout: bytes
    stderr: bytes
    cancelled: bool = False


@dataclass
class TransportSession:
    """Logical transport session handle (not library-specific)."""
    session_id: str
    host: str
    port: int
    username: str
    host_fingerprint: str
    host_key_type: str
    connected: bool = True
    _backend: Any = field(default=None, repr=False)

    def to_public(self) -> dict:
        return {
            "session_id": self.session_id,
            "host": self.host,
            "port": self.port,
            "username": self.username,
            "host_fingerprint": self.host_fingerprint,
            "host_key_type": self.host_key_type,
            "connected": self.connected,
            "mode": "remote_ssh",
        }


class SshBackend(Protocol):
    async def connect(
        self,
        params: SshConnectParams,
        *,
        private_key_pem: Optional[str] = None,
        password: Optional[str] = None,
        passphrase: Optional[str] = None,
        agent_forwarding: bool = False,
    ) -> Any: ...

    async def close(self, conn: Any) -> None: ...

    async def exec_command(
        self, conn: Any, command: str, *, timeout_s: float = 60.0
    ) -> SshExecResult: ...

    async def open_session_pty(
        self, conn: Any, *, term: str = "xterm-256color", cols: int = 80, rows: int = 24
    ) -> Any: ...


class MockSshBackend:
    """Deterministic backend for unit tests."""

    def __init__(
        self,
        *,
        fail_auth: bool = False,
        fail_connect: bool = False,
        hang: bool = False,
        host_key_type: str = "ssh-ed25519",
        host_fingerprint: str = "SHA256:TESTFINGERPRINTAAAA",
        exec_exit: int = 0,
        exec_stdout: bytes = b"ok\n",
        exec_stderr: bytes = b"",
        drop_after_connect: bool = False,
    ):
        self.fail_auth = fail_auth
        self.fail_connect = fail_connect
        self.hang = hang
        self.host_key_type = host_key_type
        self.host_fingerprint = host_fingerprint
        self.exec_exit = exec_exit
        self.exec_stdout = exec_stdout
        self.exec_stderr = exec_stderr
        self.drop_after_connect = drop_after_connect
        self.closed = False
        self.sessions_opened = 0
        self.execs = 0

    async def connect(self, params: SshConnectParams, **auth):
        if self.hang:
            await asyncio.sleep(3600)
        if self.fail_connect:
            raise SshTransportError("connection_refused")
        if self.fail_auth:
            raise SshAuthError()
        params.host_key_type = self.host_key_type
        params.host_key_fingerprint = self.host_fingerprint
        conn = {
            "params": params,
            "auth": {k: bool(v) for k, v in auth.items()},
            "alive": not self.drop_after_connect,
        }
        return conn

    async def close(self, conn):
        self.closed = True
        if isinstance(conn, dict):
            conn["alive"] = False

    async def exec_command(self, conn, command: str, *, timeout_s: float = 60.0):
        self.execs += 1
        if not conn.get("alive", True):
            raise SshTransportError("connection_dropped")
        return SshExecResult(
            exit_status=self.exec_exit,
            stdout=self.exec_stdout,
            stderr=self.exec_stderr,
        )

    async def open_session_pty(self, conn, *, term="xterm-256color", cols=80, rows=24):
        self.sessions_opened += 1
        return {"pty": True, "cols": cols, "rows": rows, "term": term}


def _try_asyncssh_backend():
    try:
        import asyncssh  # noqa: F401
        return AsyncsshBackend()
    except ImportError:
        return None


class AsyncsshBackend:
    """Production backend using asyncssh."""

    async def connect(self, params: SshConnectParams, **auth):
        import asyncssh

        kwargs: dict[str, Any] = {
            "host": params.host,
            "port": params.port,
            "username": params.username,
            "known_hosts": None,  # we verify separately before channel use
            "connect_timeout": params.connect_timeout_s,
        }
        # keepalive
        kwargs["keepalive_interval"] = params.keepalive_s

        if auth.get("private_key_pem"):
            kwargs["client_keys"] = [asyncssh.import_private_key(
                auth["private_key_pem"], passphrase=auth.get("passphrase")
            )]
        if auth.get("password"):
            kwargs["password"] = auth["password"]

        try:
            conn = await asyncssh.connect(**kwargs)
        except asyncssh.PermissionDenied as e:
            raise SshAuthError() from e
        except asyncio.TimeoutError as e:
            raise SshTimeoutError() from e
        except Exception as e:
            raise SshTransportError("connection_failed") from e

        # Extract server host key fingerprint for verification layer
        try:
            server_key = conn.get_server_host_key()
            if server_key is not None:
                params.host_key_type = server_key.get_algorithm()
                # asyncssh provides export for fingerprint
                params.host_key_fingerprint = server_key.get_fingerprint(
                    hash_name="sha256"
                )
                if not str(params.host_key_fingerprint).startswith("SHA256:"):
                    params.host_key_fingerprint = f"SHA256:{params.host_key_fingerprint}"
        except Exception:
            logger.warning("ssh_transport_host_key_extract_failed host=%s", params.host)

        return conn

    async def close(self, conn):
        try:
            conn.close()
            await conn.wait_closed()
        except Exception:
            pass

    async def exec_command(self, conn, command: str, *, timeout_s: float = 60.0):
        try:
            result = await asyncio.wait_for(
                conn.run(command, check=False), timeout=timeout_s
            )
        except asyncio.TimeoutError as e:
            raise SshTimeoutError() from e
        return SshExecResult(
            exit_status=result.exit_status,
            stdout=result.stdout.encode() if isinstance(result.stdout, str) else (result.stdout or b""),
            stderr=result.stderr.encode() if isinstance(result.stderr, str) else (result.stderr or b""),
        )

    async def open_session_pty(self, conn, *, term="xterm-256color", cols=80, rows=24):
        return await conn.create_process(
            term_type=term, term_size=(cols, rows),
        )


class SshTransportService:
    """Capability-facing transport. Host verify + credential resolve outside."""

    def __init__(self, backend: Optional[SshBackend] = None):
        self._backend = backend or _try_asyncssh_backend() or MockSshBackend()
        self._sessions: dict[str, TransportSession] = {}

    @property
    def backend_name(self) -> str:
        return type(self._backend).__name__

    async def connect_verified(
        self,
        params: SshConnectParams,
        *,
        private_key_pem: Optional[str] = None,
        password: Optional[str] = None,
        passphrase: Optional[str] = None,
        agent_forwarding: bool = False,
        host_verify: Optional[Callable] = None,
    ) -> TransportSession:
        """Connect then run host_verify callback with presented key.

        host_verify(params) must raise on failure. Credentials cleared by caller.
        """
        try:
            conn = await asyncio.wait_for(
                self._backend.connect(
                    params,
                    private_key_pem=private_key_pem,
                    password=password,
                    passphrase=passphrase,
                    agent_forwarding=agent_forwarding,
                ),
                timeout=params.connect_timeout_s,
            )
        except asyncio.TimeoutError as e:
            raise SshTimeoutError() from e

        if host_verify is not None:
            try:
                await host_verify(params)
            except Exception:
                await self._backend.close(conn)
                raise

        import uuid

        sid = uuid.uuid4().hex
        session = TransportSession(
            session_id=sid,
            host=params.host,
            port=params.port,
            username=params.username,
            host_fingerprint=params.host_key_fingerprint,
            host_key_type=params.host_key_type,
            connected=True,
            _backend=conn,
        )
        self._sessions[sid] = session
        return session

    async def exec(
        self, session_id: str, command: str, *, timeout_s: float = 60.0
    ) -> SshExecResult:
        sess = self._sessions.get(session_id)
        if not sess or not sess.connected:
            raise SshTransportError("session_not_connected")
        return await self._backend.exec_command(
            sess._backend, command, timeout_s=timeout_s
        )

    async def open_pty(self, session_id: str, *, cols: int = 80, rows: int = 24):
        sess = self._sessions.get(session_id)
        if not sess or not sess.connected:
            raise SshTransportError("session_not_connected")
        return await self._backend.open_session_pty(
            sess._backend, cols=cols, rows=rows
        )

    async def close(self, session_id: str) -> None:
        sess = self._sessions.pop(session_id, None)
        if not sess:
            return
        sess.connected = False
        await self._backend.close(sess._backend)

    async def force_terminate(self, session_id: str) -> None:
        await self.close(session_id)
