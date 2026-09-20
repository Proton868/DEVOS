"""Production-oriented SSH session reconnection state machine.

States (interactive sessions must surface these explicitly):
  CONNECTED | RECONNECTING | DISCONNECTED | RECONNECTED | FAILED

Never silently reconnect when host identity has changed.
"""
from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

logger = logging.getLogger("devos.ssh_reconnect")


class SessionConnectionState(str, Enum):
    CONNECTED = "CONNECTED"
    RECONNECTING = "RECONNECTING"
    DISCONNECTED = "DISCONNECTED"
    RECONNECTED = "RECONNECTED"
    FAILED = "FAILED"


class DisconnectReason(str, Enum):
    NETWORK_LOSS = "network_loss"
    SERVER_REBOOT = "server_reboot"
    DAEMON_RESTART = "ssh_daemon_restart"
    IDLE_TIMEOUT = "idle_timeout"
    TCP_RESET = "tcp_reset"
    APP_RESTART = "application_restart"
    BROWSER_RECONNECT = "browser_reconnect"
    WORKER_RESTART = "devos_worker_restart"
    UNKNOWN = "unknown"


@dataclass
class ReconnectAttempt:
    attempt_id: str
    reason: str
    started_at: float
    finished_at: Optional[float] = None
    state: str = SessionConnectionState.RECONNECTING.value
    error: Optional[str] = None
    host_identity_ok: bool = True


@dataclass
class SessionReconnectController:
    session_key: str  # owner_id:connection_id
    pinned_fingerprint: str = ""
    pinned_key_type: str = ""
    state: SessionConnectionState = SessionConnectionState.DISCONNECTED
    attempts: list[ReconnectAttempt] = field(default_factory=list)
    max_attempts: int = 5
    backoff_s: float = 1.0

    def to_public(self) -> dict:
        return {
            "session_key": self.session_key,
            "state": self.state.value,
            "pinned_fingerprint": self.pinned_fingerprint,
            "attempts": len(self.attempts),
            "last_error": self.attempts[-1].error if self.attempts else None,
        }

    def mark_connected(self, *, fingerprint: str = "", key_type: str = "") -> None:
        if fingerprint:
            self.pinned_fingerprint = fingerprint
        if key_type:
            self.pinned_key_type = key_type
        self.state = SessionConnectionState.CONNECTED

    def mark_disconnected(self, reason: DisconnectReason | str = DisconnectReason.UNKNOWN) -> None:
        self.state = SessionConnectionState.DISCONNECTED
        logger.info(
            "ssh_session_disconnected key=%s reason=%s",
            self.session_key,
            reason.value if isinstance(reason, DisconnectReason) else reason,
        )

    def begin_reconnect(self, reason: DisconnectReason | str = DisconnectReason.UNKNOWN) -> ReconnectAttempt:
        if len(self.attempts) >= self.max_attempts:
            self.state = SessionConnectionState.FAILED
            att = ReconnectAttempt(
                attempt_id=uuid.uuid4().hex,
                reason=str(reason),
                started_at=time.monotonic(),
                finished_at=time.monotonic(),
                state=SessionConnectionState.FAILED.value,
                error="max_reconnect_attempts",
            )
            self.attempts.append(att)
            return att
        self.state = SessionConnectionState.RECONNECTING
        att = ReconnectAttempt(
            attempt_id=uuid.uuid4().hex,
            reason=reason.value if isinstance(reason, DisconnectReason) else str(reason),
            started_at=time.monotonic(),
        )
        self.attempts.append(att)
        return att

    def validate_host_identity(self, *, fingerprint: str, key_type: str = "") -> bool:
        """Return False if host identity changed — NEVER auto-reconnect."""
        if not self.pinned_fingerprint:
            return True
        if fingerprint and fingerprint != self.pinned_fingerprint:
            logger.warning(
                "ssh_reconnect_host_changed key=%s pinned=%s seen=%s",
                self.session_key, self.pinned_fingerprint, fingerprint,
            )
            return False
        if self.pinned_key_type and key_type and key_type != self.pinned_key_type:
            return False
        return True

    def complete_reconnect(
        self,
        attempt: ReconnectAttempt,
        *,
        fingerprint: str,
        key_type: str = "",
        ok: bool = True,
        error: Optional[str] = None,
    ) -> SessionConnectionState:
        attempt.finished_at = time.monotonic()
        if not ok:
            attempt.state = SessionConnectionState.FAILED.value
            attempt.error = error or "reconnect_failed"
            attempt.host_identity_ok = True
            self.state = SessionConnectionState.FAILED
            return self.state

        if not self.validate_host_identity(fingerprint=fingerprint, key_type=key_type):
            attempt.state = SessionConnectionState.FAILED.value
            attempt.error = "host_identity_changed"
            attempt.host_identity_ok = False
            self.state = SessionConnectionState.FAILED
            return self.state

        attempt.state = SessionConnectionState.RECONNECTED.value
        attempt.host_identity_ok = True
        if fingerprint:
            self.pinned_fingerprint = fingerprint
        if key_type:
            self.pinned_key_type = key_type
        self.state = SessionConnectionState.RECONNECTED
        # RECONNECTED is a transitional success signal; steady state becomes CONNECTED
        self.state = SessionConnectionState.CONNECTED
        # But callers should observe the attempt state RECONNECTED
        return SessionConnectionState.RECONNECTED


# In-process controllers (worker-local; durable session rows live in DB)
_CONTROLLERS: dict[str, SessionReconnectController] = {}


def get_controller(session_key: str) -> SessionReconnectController:
    if session_key not in _CONTROLLERS:
        _CONTROLLERS[session_key] = SessionReconnectController(session_key=session_key)
    return _CONTROLLERS[session_key]


def clear_controllers_for_tests() -> None:
    _CONTROLLERS.clear()
