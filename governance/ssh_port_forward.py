"""Governed SSH port forwarding policy.

Forwarding is HIGH-RISK network capability. Default: DENY all.

Local / remote / SOCKS forwarding are not exposed as agent capabilities unless
explicitly authorized with destination allowlists that pass network boundary
checks. Isolation > feature completeness.

This module enforces policy only. It does not open tunnels by default.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from governance.ssh_network_policy import validate_ssh_target, NetworkPolicyDecision


class ForwardKind(str, Enum):
    LOCAL = "local"       # -L
    REMOTE = "remote"     # -R
    SOCKS = "socks"       # -D


class ForwardDenied(Exception):
    def __init__(self, code: str):
        self.code = str(code)[:64]
        super().__init__(self.code)


@dataclass
class ForwardRequest:
    owner_id: str
    connection_id: str
    kind: ForwardKind
    # For LOCAL: listen_host:listen_port -> dest_host:dest_port
    listen_host: str = "127.0.0.1"
    listen_port: int = 0
    dest_host: str = ""
    dest_port: int = 0
    actor: str = "agent"
    user_confirmed: bool = False
    workspace_id: str = ""
    agent_id: str = ""
    duration_s: int = 300
    # Explicit destination allowlist (host:port or host)
    dest_allowlist: list[str] = field(default_factory=list)


@dataclass
class ForwardDecision:
    allowed: bool
    reasons: list[str]
    kind: str
    record: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "allowed": self.allowed,
            "reasons": list(self.reasons),
            "kind": self.kind,
            "record": dict(self.record),
        }


def _dest_allowed(host: str, port: int, allowlist: list[str]) -> bool:
    if not allowlist:
        return False
    h = host.lower().strip()
    for entry in allowlist:
        e = entry.strip().lower()
        if not e:
            continue
        if ":" in e:
            eh, ep = e.rsplit(":", 1)
            if eh == h and str(port) == ep:
                return True
        elif e == h:
            return True
    return False


def authorize_port_forward(req: ForwardRequest) -> ForwardDecision:
    """Policy gate for port forwarding. Default deny.

    Never allows metadata, private (unless allowlist+override), localhost dest
    for agents, or SOCKS without explicit confirmation + allowlist.
    Does not open a tunnel — callers must not proceed if allowed is False.
    """
    reasons: list[str] = []
    kind = req.kind.value if isinstance(req.kind, ForwardKind) else str(req.kind)

    # Global: agents never get forwarding without confirmation
    if req.actor == "agent" and not req.user_confirmed:
        return ForwardDecision(False, ["forwarding_requires_confirmation"], kind)

    # SOCKS is highest risk — require confirmation + non-empty allowlist always
    if req.kind == ForwardKind.SOCKS:
        reasons.append("socks_high_risk")
        if not req.user_confirmed:
            return ForwardDecision(False, reasons + ["socks_requires_confirmation"], kind)
        if not req.dest_allowlist:
            return ForwardDecision(False, reasons + ["socks_requires_dest_allowlist"], kind)
        # Still do not auto-enable SOCKS in this release — isolation first
        return ForwardDecision(
            False,
            reasons + ["socks_not_enabled_isolation_priority"],
            kind,
            record=_audit_record(req, allowed=False),
        )

    if not req.dest_host or not req.dest_port:
        return ForwardDecision(False, ["destination_required"], kind)

    # Network boundary on destination
    net = validate_ssh_target(req.dest_host, req.dest_port, actor=req.actor)
    if not net.allowed:
        return ForwardDecision(
            False,
            ["dest_network_denied"] + list(net.reasons),
            kind,
            record=_audit_record(req, allowed=False, net=net.to_dict()),
        )

    if not _dest_allowed(req.dest_host, req.dest_port, req.dest_allowlist):
        return ForwardDecision(
            False,
            ["dest_not_in_allowlist"],
            kind,
            record=_audit_record(req, allowed=False, net=net.to_dict()),
        )

    # Listen host: only loopback for LOCAL by default (prevent bind-all)
    lh = (req.listen_host or "").strip().lower()
    if req.kind == ForwardKind.LOCAL and lh not in ("127.0.0.1", "localhost", "::1"):
        return ForwardDecision(
            False,
            ["local_forward_must_bind_loopback"],
            kind,
            record=_audit_record(req, allowed=False),
        )

    # Even when policy passes destination checks, do not enable actual tunnels yet
    # unless DEVOS_SSH_ENABLE_PORT_FORWARD=1 (explicit opt-in for controlled deployments)
    import os
    if os.environ.get("DEVOS_SSH_ENABLE_PORT_FORWARD", "").strip() not in ("1", "true", "yes"):
        return ForwardDecision(
            False,
            ["port_forward_disabled_isolation_priority"],
            kind,
            record=_audit_record(req, allowed=False, net=net.to_dict()),
        )

    rec = _audit_record(req, allowed=True, net=net.to_dict())
    try:
        from governance.structured_audit import emit_audit_event
        from governance.audit import AuditEventType
        emit_audit_event(
            action="ssh.port_forward.authorize",
            result="allowed",
            event_type=AuditEventType.GOVERNANCE,
            actor_id=req.owner_id,
            resource="ssh_forward",
            resource_id=req.connection_id,
            details=rec,
        )
    except Exception:
        pass
    return ForwardDecision(True, ["authorized"], kind, record=rec)


def _audit_record(req: ForwardRequest, *, allowed: bool, net: Optional[dict] = None) -> dict:
    return {
        "allowed": allowed,
        "kind": req.kind.value if isinstance(req.kind, ForwardKind) else str(req.kind),
        "source": f"{req.listen_host}:{req.listen_port}",
        "destination": f"{req.dest_host}:{req.dest_port}",
        "port": req.dest_port,
        "user": req.owner_id,
        "workspace": req.workspace_id,
        "agent": req.agent_id or req.actor,
        "duration_s": req.duration_s,
        "approval": req.user_confirmed,
        "connection_id": req.connection_id,
        "network": net or {},
    }
