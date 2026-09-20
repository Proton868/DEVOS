"""Remote SSH interactive session attached to DevOS terminal WebSocket protocol.

Protocol compatible with local PtySession clients:
  client → {type: input|resize|...}
  server → {type: data|status|error|host_identity}

Sessions are labeled mode=remote_ssh so the UI can distinguish LOCAL vs REMOTE.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

from execution.ssh_transport import SshTransportService, MockSshBackend, TransportSession
from governance.ssh_reconnect import (
    get_controller,
    SessionConnectionState,
    DisconnectReason,
)

logger = logging.getLogger("devos.ssh_remote_session")

# (user_id, connection_id) → RemoteSshSession
_SESSIONS: dict[tuple[str, str], "RemoteSshSession"] = {}


class RemoteSshSession:
    def __init__(
        self,
        user_id: str,
        connection_id: str,
        transport_session: TransportSession,
        host_evidence: dict,
        transport: SshTransportService,
    ):
        self.user_id = user_id
        self.connection_id = connection_id
        self.transport_session = transport_session
        self.host_evidence = host_evidence
        self.transport = transport
        self.mode = "remote_ssh"
        self._clients: list = []
        self._scrollback: bytearray = bytearray()
        self._pty: Any = None

    def attach(self, websocket) -> None:
        self._clients.append(websocket)

    def detach(self, websocket) -> None:
        if websocket in self._clients:
            self._clients.remove(websocket)

    def _session_key(self) -> str:
        return f"{self.user_id}:{self.connection_id}"

    def connection_state(self) -> str:
        ctrl = get_controller(self._session_key())
        if self.transport_session.connected and ctrl.state in (
            SessionConnectionState.CONNECTED,
            SessionConnectionState.RECONNECTED,
        ):
            return ctrl.state.value if ctrl.state == SessionConnectionState.RECONNECTED else SessionConnectionState.CONNECTED.value
        if ctrl.state == SessionConnectionState.RECONNECTING:
            return SessionConnectionState.RECONNECTING.value
        if ctrl.state == SessionConnectionState.FAILED:
            return SessionConnectionState.FAILED.value
        if self.transport_session.connected:
            return SessionConnectionState.CONNECTED.value
        return SessionConnectionState.DISCONNECTED.value

    async def send_status(self, websocket) -> None:
        await websocket.send_json({
            "type": "status",
            "mode": "remote_ssh",
            "connection_id": self.connection_id,
            "session_id": self.transport_session.session_id,
            "connected": self.transport_session.connected,
            "connection_state": self.connection_state(),
            "host_identity": self.host_evidence,
        })

    async def notify_state(self, state: str, **extra) -> None:
        payload = {
            "type": "connection_state",
            "mode": "remote_ssh",
            "connection_id": self.connection_id,
            "connection_state": state,
            **extra,
        }
        for ws in list(self._clients):
            try:
                await ws.send_json(payload)
            except Exception:
                pass

    async def write(self, data: bytes) -> None:
        # Mock/path: echo to clients for interactive tests without real PTY
        if self._pty is None:
            # Store and broadcast as if remote echoed (test backend)
            text = data
            self._scrollback.extend(text)
            await self._broadcast(text)
            return
        # Real asyncssh process would write to stdin
        try:
            self._pty.stdin.write(data)
        except Exception as e:
            logger.warning("ssh_remote_write_failed: %s", type(e).__name__)

    async def resize(self, cols: int, rows: int) -> None:
        if self._pty is not None and hasattr(self._pty, "change_terminal_size"):
            try:
                self._pty.change_terminal_size(cols, rows)
            except Exception:
                pass

    async def _broadcast(self, data: bytes) -> None:
        msg = {"type": "data", "data": data.decode("utf-8", errors="replace"), "mode": "remote_ssh"}
        dead = []
        for ws in self._clients:
            try:
                await ws.send_json(msg)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.detach(ws)

    async def handle_disconnect(self, reason: str = "network_loss") -> None:
        """Surface DISCONNECTED to clients and mark transport dropped."""
        from governance.ssh_reconnect import get_controller, DisconnectReason
        self.transport.mark_dropped(self.transport_session.session_id, reason=reason)
        ctrl = get_controller(self._session_key())
        if self.transport_session.host_fingerprint:
            ctrl.pinned_fingerprint = (
                ctrl.pinned_fingerprint or self.transport_session.host_fingerprint
            )
        ctrl.mark_disconnected(reason)
        await self.notify_state(
            "DISCONNECTED",
            reason=reason,
            host_identity=self.host_evidence,
        )

    async def reconnect(
        self,
        *,
        reason: str = "network_loss",
        private_key_pem: Optional[str] = None,
        password: Optional[str] = None,
        passphrase: Optional[str] = None,
        agent_forwarding: bool = False,
        host_verify=None,
        max_attempts: int = 5,
        backoff_s: float = 0.05,
    ):
        """Live TCP reconnect for this interactive session.

        Emits RECONNECTING then RECONNECTED or FAILED to attached clients.
        Refuses success if host identity changed.
        """
        from execution.ssh_transport import SshConnectParams, SshHostVerifyFailed, SshTransportError
        from governance.ssh_reconnect import SessionConnectionState

        await self.notify_state("RECONNECTING", reason=reason)
        params = SshConnectParams(
            host=self.transport_session.host,
            port=self.transport_session.port,
            username=self.transport_session.username,
            host_key_fingerprint=self.transport_session.host_fingerprint,
            host_key_type=self.transport_session.host_key_type,
        )
        try:
            sess = await self.transport.reconnect(
                self.transport_session.session_id,
                params,
                private_key_pem=private_key_pem,
                password=password,
                passphrase=passphrase,
                agent_forwarding=agent_forwarding,
                host_verify=host_verify,
                session_key=self._session_key(),
                reason=reason,
                max_attempts=max_attempts,
                backoff_s=backoff_s,
            )
            self.transport_session = sess
            # Refresh evidence fingerprint if present
            if sess.host_fingerprint:
                self.host_evidence = dict(self.host_evidence or {})
                self.host_evidence["fingerprint_sha256"] = sess.host_fingerprint
                self.host_evidence["key_type"] = sess.host_key_type
            await self.notify_state(
                "RECONNECTED",
                reason=reason,
                host_identity=self.host_evidence,
            )
            await self.notify_state(
                "CONNECTED",
                reason=reason,
                host_identity=self.host_evidence,
            )
            return sess
        except SshHostVerifyFailed as e:
            await self.notify_state(
                "FAILED",
                reason="host_identity_changed",
                error=e.code,
                host_identity=self.host_evidence,
            )
            raise
        except SshTransportError as e:
            await self.notify_state(
                "FAILED",
                reason=reason,
                error=e.code,
                host_identity=self.host_evidence,
            )
            raise

    async def close(self) -> None:
        from governance.ssh_reconnect import get_controller
        await self.transport.close(self.transport_session.session_id)
        self.transport_session.connected = False
        get_controller(self._session_key()).mark_disconnected("session_closed")
        await self.notify_state("DISCONNECTED", reason="session_closed")


async def get_or_create_remote_session(
    user_id: str,
    connection_id: str,
    *,
    actor: str = "user",
    auto_approve_new: bool = False,
    transport: Optional[SshTransportService] = None,
) -> RemoteSshSession:
    key = (user_id, connection_id)
    if key in _SESSIONS and _SESSIONS[key].transport_session.connected:
        return _SESSIONS[key]

    from execution.ssh_capabilities import SSHConnectionCapability

    transport = transport or SshTransportService(backend=MockSshBackend())
    cap = SSHConnectionCapability(owner_id=user_id, transport=transport)
    ts, verify = await cap.connect(
        connection_id=connection_id,
        actor=actor,
        auto_approve_new=auto_approve_new,
    )
    sess = RemoteSshSession(
        user_id=user_id,
        connection_id=connection_id,
        transport_session=ts,
        host_evidence=verify.to_evidence(),
        transport=transport,
    )
    _SESSIONS[key] = sess
    return sess


def clear_remote_sessions_for_tests() -> None:
    _SESSIONS.clear()
