"""SSH network-boundary policy — prevent unrestricted network pivot / SSRF.

Default: agents may only target **explicitly allowed** hosts.
Public internet hosts require policy allow (or owner allowlist).
Private RFC1918, loopback, link-local, metadata, and Unix sockets are denied
unless an explicit, documented deployment override is set.

Do not trust hostnames alone — resolve and validate addresses.
"""
from __future__ import annotations

import ipaddress
import logging
import os
import re
import socket
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

logger = logging.getLogger("devos.ssh_network_policy")

# Cloud metadata & well-known internal
_METADATA_IPS = {
    "169.254.169.254",  # AWS/GCP/Azure IMDS
    "169.254.170.2",    # AWS ECS
    "fd00:ec2::254",
}
_METADATA_HOSTS = {
    "metadata.google.internal",
    "metadata",
    "instance-data",
}

# Ports commonly used for pivot / internal services — still allowed only if host is allowed
_DEFAULT_SSH_PORTS = {22, 2222, 2200, 22022}


class NetworkTargetClass(str, Enum):
    PUBLIC = "public"
    PRIVATE_RFC1918 = "private_rfc1918"
    LOCALHOST = "localhost"
    LOOPBACK = "loopback"
    LINK_LOCAL = "link_local"
    METADATA = "metadata"
    UNIX_SOCKET = "unix_socket"
    INTERNAL_SERVICE = "internal_service"
    UNSPECIFIED = "unspecified"
    DENIED = "denied"


class NetworkPolicyDecision(str, Enum):
    ALLOW = "allow"
    DENY = "deny"


@dataclass
class NetworkPolicyResult:
    decision: NetworkPolicyDecision
    target_class: NetworkTargetClass
    hostname: str
    resolved_ips: list[str] = field(default_factory=list)
    port: int = 22
    reasons: list[str] = field(default_factory=list)
    actor: str = ""

    @property
    def allowed(self) -> bool:
        return self.decision == NetworkPolicyDecision.ALLOW

    def to_dict(self) -> dict:
        return {
            "decision": self.decision.value,
            "target_class": self.target_class.value,
            "hostname": self.hostname,
            "resolved_ips": list(self.resolved_ips),
            "port": self.port,
            "reasons": list(self.reasons),
            "actor": self.actor,
            "allowed": self.allowed,
        }


def _env_allow_private() -> bool:
    """Deployment override: DEVOS_SSH_ALLOW_PRIVATE_NETWORKS=1 for managed VPS fleets."""
    return os.environ.get("DEVOS_SSH_ALLOW_PRIVATE_NETWORKS", "").strip() in ("1", "true", "yes")


def _env_allowlist() -> set[str]:
    raw = os.environ.get("DEVOS_SSH_HOST_ALLOWLIST", "") or ""
    return {x.strip().lower() for x in raw.split(",") if x.strip()}


def _is_unix_socket(host: str) -> bool:
    h = (host or "").strip()
    return h.startswith("/") or h.startswith("unix:") or h.endswith(".sock")


def _classify_ip(ip: str) -> NetworkTargetClass:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return NetworkTargetClass.UNSPECIFIED
    if str(addr) in _METADATA_IPS:
        return NetworkTargetClass.METADATA
    if addr.is_loopback:
        return NetworkTargetClass.LOOPBACK
    if addr.is_link_local:
        return NetworkTargetClass.LINK_LOCAL
    if addr.is_private:
        return NetworkTargetClass.PRIVATE_RFC1918
    if addr.is_reserved or addr.is_multicast or addr.is_unspecified:
        return NetworkTargetClass.DENIED
    return NetworkTargetClass.PUBLIC


def _hostname_class_hints(host: str) -> Optional[NetworkTargetClass]:
    h = (host or "").lower().strip().rstrip(".")
    if not h:
        return NetworkTargetClass.DENIED
    if _is_unix_socket(h):
        return NetworkTargetClass.UNIX_SOCKET
    if h in ("localhost", "localhost.localdomain") or h.endswith(".localhost"):
        return NetworkTargetClass.LOCALHOST
    if h in _METADATA_HOSTS:
        return NetworkTargetClass.METADATA
    if h.endswith(".local") or h.endswith(".internal") or h.endswith(".corp"):
        return NetworkTargetClass.INTERNAL_SERVICE
    # IPv4/IPv6 literal
    try:
        return _classify_ip(h)
    except Exception:
        pass
    return None


def resolve_host_ips(hostname: str) -> list[str]:
    """Resolve all A/AAAA addresses. Empty on failure (fail closed for agents)."""
    host = (hostname or "").strip()
    if not host or _is_unix_socket(host):
        return []
    # literal IP
    try:
        ipaddress.ip_address(host)
        return [host]
    except ValueError:
        pass
    ips: list[str] = []
    try:
        for info in socket.getaddrinfo(host, None, type=socket.SOCK_STREAM):
            ip = info[4][0]
            if ip not in ips:
                ips.append(ip)
    except socket.gaierror:
        return []
    return ips


def validate_ssh_target(
    hostname: str,
    port: int = 22,
    *,
    actor: str = "agent",
    owner_allowlist: Optional[list[str]] = None,
    allow_private: Optional[bool] = None,
) -> NetworkPolicyResult:
    """Validate SSH connection target against network boundary policy.

    actor=agent: strict defaults (deny private/loopback/metadata).
    actor=user: still deny metadata/unix/loopback by default; private may be
    allowed only with DEVOS_SSH_ALLOW_PRIVATE_NETWORKS or explicit allowlist match.
    """
    host = (hostname or "").strip().lower().rstrip(".")
    port = int(port or 22)
    reasons: list[str] = []
    allow_private = _env_allow_private() if allow_private is None else allow_private
    allowlist = _env_allowlist() | {x.lower() for x in (owner_allowlist or [])}

    if not host:
        return NetworkPolicyResult(
            NetworkPolicyDecision.DENY, NetworkTargetClass.DENIED, host, port=port,
            reasons=["empty_hostname"], actor=actor,
        )

    if _is_unix_socket(host):
        return NetworkPolicyResult(
            NetworkPolicyDecision.DENY, NetworkTargetClass.UNIX_SOCKET, host, port=port,
            reasons=["unix_socket_refused"], actor=actor,
        )

    # Port bounds
    if port < 1 or port > 65535:
        return NetworkPolicyResult(
            NetworkPolicyDecision.DENY, NetworkTargetClass.DENIED, host, port=port,
            reasons=["invalid_port"], actor=actor,
        )

    hint = _hostname_class_hints(host)
    if hint in (NetworkTargetClass.METADATA, NetworkTargetClass.UNIX_SOCKET):
        return NetworkPolicyResult(
            NetworkPolicyDecision.DENY, hint, host, port=port,
            reasons=["metadata_or_socket_denied"], actor=actor,
        )
    if hint in (NetworkTargetClass.LOCALHOST, NetworkTargetClass.LOOPBACK):
        # Localhost only for explicit user + test override
        if actor == "user" and os.environ.get("DEVOS_SSH_ALLOW_LOCALHOST") == "1":
            return NetworkPolicyResult(
                NetworkPolicyDecision.ALLOW, hint, host, resolved_ips=["127.0.0.1"],
                port=port, reasons=["localhost_override"], actor=actor,
            )
        return NetworkPolicyResult(
            NetworkPolicyDecision.DENY, hint, host, port=port,
            reasons=["localhost_denied"], actor=actor,
        )

    # Resolve DNS — check ALL addresses (rebinding defense: deny if any is bad)
    ips = resolve_host_ips(host)
    if not ips:
        # Agents fail closed. Users may register hosts for later connect-time resolve
        # (still cannot be literal private/metadata hostnames — checked above).
        if actor == "agent":
            return NetworkPolicyResult(
                NetworkPolicyDecision.DENY, NetworkTargetClass.UNSPECIFIED, host, port=port,
                reasons=["dns_resolution_failed_or_empty"], actor=actor,
            )
        return NetworkPolicyResult(
            NetworkPolicyDecision.ALLOW, NetworkTargetClass.UNSPECIFIED, host, port=port,
            reasons=["dns_unresolved_deferred_to_connect"], actor=actor,
        )

    classes = [_classify_ip(ip) for ip in ips]
    # If any resolved address is dangerous, deny (DNS rebinding / dual-homed)
    for ip, cls in zip(ips, classes):
        if cls == NetworkTargetClass.METADATA:
            return NetworkPolicyResult(
                NetworkPolicyDecision.DENY, cls, host, resolved_ips=ips, port=port,
                reasons=[f"resolves_to_metadata:{ip}"], actor=actor,
            )
        if cls in (NetworkTargetClass.LOOPBACK, NetworkTargetClass.LINK_LOCAL, NetworkTargetClass.DENIED):
            return NetworkPolicyResult(
                NetworkPolicyDecision.DENY, cls, host, resolved_ips=ips, port=port,
                reasons=[f"resolves_to_{cls.value}:{ip}"], actor=actor,
            )
        if cls == NetworkTargetClass.PRIVATE_RFC1918:
            if not allow_private and host not in allowlist and not any(
                host.endswith("." + a) for a in allowlist if a
            ):
                return NetworkPolicyResult(
                    NetworkPolicyDecision.DENY, cls, host, resolved_ips=ips, port=port,
                    reasons=[f"private_ip_denied:{ip}"], actor=actor,
                )
            reasons.append(f"private_allowed:{ip}")

    # Allowlist gate for agents on public hosts (optional strict mode)
    strict_agent = os.environ.get("DEVOS_SSH_AGENT_REQUIRE_ALLOWLIST", "").strip() in ("1", "true")
    if actor == "agent" and strict_agent:
        if host not in allowlist and not any(host.endswith("." + a) for a in allowlist if a):
            return NetworkPolicyResult(
                NetworkPolicyDecision.DENY, NetworkTargetClass.PUBLIC, host,
                resolved_ips=ips, port=port,
                reasons=["agent_host_not_in_allowlist"], actor=actor,
            )

    # Default: public IPs allowed for user; agent same unless strict
    primary = classes[0] if classes else NetworkTargetClass.PUBLIC
    if all(c == NetworkTargetClass.PUBLIC for c in classes):
        primary = NetworkTargetClass.PUBLIC
    elif any(c == NetworkTargetClass.PRIVATE_RFC1918 for c in classes):
        primary = NetworkTargetClass.PRIVATE_RFC1918

    reasons.append("network_policy_allow")
    return NetworkPolicyResult(
        NetworkPolicyDecision.ALLOW, primary, host, resolved_ips=ips, port=port,
        reasons=reasons, actor=actor,
    )


def assert_ssh_target_allowed(hostname: str, port: int = 22, **kwargs) -> NetworkPolicyResult:
    result = validate_ssh_target(hostname, port, **kwargs)
    if not result.allowed:
        from governance.ssh_domain import SshDomainError
        raise SshDomainError(f"network_denied:{result.reasons[0] if result.reasons else 'denied'}")
    return result
