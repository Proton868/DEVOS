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
        fail_connect_times: int = 0,
        reconnect_fingerprint: Optional[str] = None,
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
        self.fail_connect_times = int(fail_connect_times or 0)
        self._connect_attempts = 0
        # Optional different fingerprint on later connects (simulate host key change)
        self.reconnect_fingerprint = reconnect_fingerprint
        self.closed = False
        self.sessions_opened = 0
        self.execs = 0
        self.force_dead_conns: set[int] = set()

    async def connect(self, params: SshConnectParams, **auth):
        self._connect_attempts += 1
        if self.hang:
            await asyncio.sleep(3600)
        if self.fail_connect:
            raise SshTransportError("connection_refused")
        if self.fail_connect_times > 0 and self._connect_attempts <= self.fail_connect_times:
            raise SshTransportError("connection_refused")
        if self.fail_auth:
            raise SshAuthError()
        params.host_key_type = self.host_key_type
        # After first successful connect, optional alternate fingerprint for reconnect tests
        if self._connect_attempts > 1 and self.reconnect_fingerprint:
            params.host_key_fingerprint = self.reconnect_fingerprint
        else:
            params.host_key_fingerprint = self.host_fingerprint
        conn = {
            "id": self._connect_attempts,
            "params": params,
            "auth": {k: bool(v) for k, v in auth.items()},
            "alive": not self.drop_after_connect,
        }
        return conn

    def force_drop(self, conn) -> None:
        """Simulate TCP reset / daemon restart on an open connection."""
        if isinstance(conn, dict):
            conn["alive"] = False

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


    def mark_dropped(self, session_id: str, *, reason: str = "connection_dropped") -> None:
        """Mark a session as disconnected (TCP reset, idle timeout, etc.)."""
        sess = self._sessions.get(session_id)
        if not sess:
            return
        sess.connected = False
        if isinstance(sess._backend, dict):
            sess._backend["alive"] = False
        logger.info("ssh_transport_dropped session=%s reason=%s", session_id, reason)

    def is_alive(self, session_id: str) -> bool:
        sess = self._sessions.get(session_id)
        if not sess or not sess.connected:
            return False
        backend = sess._backend
        if isinstance(backend, dict):
            return bool(backend.get("alive", True))
        # asyncssh: try is_closing if available
        try:
            if hasattr(backend, "is_closing") and backend.is_closing():
                return False
        except Exception:
            return False
        return True

    async def reconnect(
        self,
        session_id: str,
        params: SshConnectParams,
        *,
        private_key_pem: Optional[str] = None,
        password: Optional[str] = None,
        passphrase: Optional[str] = None,
        agent_forwarding: bool = False,
        host_verify: Optional[Callable] = None,
        session_key: str = "",
        reason: str = "network_loss",
        max_attempts: Optional[int] = None,
        backoff_s: float = 0.05,
    ) -> TransportSession:
        """Live TCP reconnect with host-identity pin check.

        Uses SessionReconnectController:
          DISCONNECTED → RECONNECTING → RECONNECTED | FAILED

        Never completes successfully if the presented host fingerprint differs
        from the pinned fingerprint on the existing session.
        """
        from governance.ssh_reconnect import (
            get_controller,
            SessionConnectionState,
            DisconnectReason,
        )

        sess = self._sessions.get(session_id)
        if not sess:
            raise SshTransportError("session_not_found")

        key = session_key or f"{params.host}:{params.port}:{params.username}"
        ctrl = get_controller(key)
        if not ctrl.pinned_fingerprint and sess.host_fingerprint:
            ctrl.mark_connected(
                fingerprint=sess.host_fingerprint,
                key_type=sess.host_key_type or "",
            )
        elif ctrl.state != SessionConnectionState.CONNECTED:
            # ensure pin exists from session
            if sess.host_fingerprint:
                ctrl.pinned_fingerprint = sess.host_fingerprint
                ctrl.pinned_key_type = sess.host_key_type or ctrl.pinned_key_type

        self.mark_dropped(session_id, reason=reason)
        ctrl.mark_disconnected(reason)

        attempts = max_attempts if max_attempts is not None else ctrl.max_attempts
        last_error: Optional[str] = None

        for i in range(max(1, attempts)):
            att = ctrl.begin_reconnect(reason)
            if att.error == "max_reconnect_attempts":
                raise SshTransportError("max_reconnect_attempts")

            try:
                await asyncio.sleep(backoff_s * (i + 1))
                # Close any residual backend
                try:
                    await self._backend.close(sess._backend)
                except Exception:
                    pass

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

                # Host verify callback (policy layer) if provided
                if host_verify is not None:
                    await host_verify(params)

                fp = params.host_key_fingerprint or ""
                kt = params.host_key_type or ""
                st = ctrl.complete_reconnect(
                    att, fingerprint=fp, key_type=kt, ok=True,
                )
                if st == SessionConnectionState.FAILED:
                    try:
                        await self._backend.close(conn)
                    except Exception:
                        pass
                    raise SshHostVerifyFailed("host_identity_changed")

                # Success — reuse same session_id for client continuity
                sess._backend = conn
                sess.connected = True
                sess.host_fingerprint = fp or sess.host_fingerprint
                sess.host_key_type = kt or sess.host_key_type
                sess.host = params.host
                sess.port = params.port
                sess.username = params.username
                logger.info(
                    "ssh_transport_reconnected session=%s attempt=%s state=%s",
                    session_id, i + 1, st.value,
                )
                return sess

            except SshHostVerifyFailed:
                raise
            except SshAuthError:
                last_error = "authentication_failed"
                ctrl.complete_reconnect(
                    att, fingerprint=ctrl.pinned_fingerprint or "", ok=False, error=last_error,
                )
            except SshTimeoutError:
                last_error = "connection_timeout"
                ctrl.complete_reconnect(
                    att, fingerprint=ctrl.pinned_fingerprint or "", ok=False, error=last_error,
                )
            except Exception as e:
                last_error = getattr(e, "code", None) or type(e).__name__
                ctrl.complete_reconnect(
                    att, fingerprint=ctrl.pinned_fingerprint or "", ok=False, error=str(last_error)[:64],
                )

        ctrl.state = SessionConnectionState.FAILED
        raise SshTransportError(last_error or "reconnect_failed")

    async def keepalive_probe(self, session_id: str) -> bool:
        """Return True if session still alive; mark dropped if not."""
        if self.is_alive(session_id):
            return True
        self.mark_dropped(session_id, reason="keepalive_failed")
        return False
