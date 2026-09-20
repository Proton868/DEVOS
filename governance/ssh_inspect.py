"""Governed remote host inspection (read-only SSH).

Fixed command templates only — callers cannot inject arbitrary shell.
Uses governed_ssh_exec so host ownership, host-key policy, and risk class apply.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional

from governance.ssh_agent_exec import SshExecRequest, governed_ssh_exec
from governance.ssh_command_policy import RiskClass
from governance.ssh_credentials import assert_no_secret_material, scrub_ssh_secrets_from_text


class InspectKind(str, Enum):
    PROCESSES = "processes"
    SERVICES = "services"
    LOGS = "logs"
    RESOURCES = "resources"
    CONTAINERS = "containers"
    NETWORK = "network"
    SYSTEM = "system"


class InspectError(Exception):
    def __init__(self, code: str):
        self.code = str(code)[:64]
        super().__init__(self.code)


# Fixed templates only. Placeholders are validated/escaped — no free-form shell.
_COMMANDS: dict[InspectKind, str] = {
    InspectKind.PROCESSES: "ps aux --sort=-%cpu | head -n 40",
    InspectKind.SERVICES: "systemctl list-units --type=service --state=running --no-pager --no-legend | head -n 40",
    InspectKind.LOGS: "journalctl -n {lines} --no-pager -o short-iso",
    InspectKind.RESOURCES: "uname -a; echo '---'; df -h; echo '---'; free -m; echo '---'; uptime",
    InspectKind.CONTAINERS: "docker ps --format 'table {{.ID}}\t{{.Image}}\t{{.Status}}\t{{.Names}}' 2>/dev/null || echo 'docker_unavailable'",
    InspectKind.NETWORK: "ss -tulpn 2>/dev/null | head -n 40 || netstat -tulpn 2>/dev/null | head -n 40 || echo 'net_tools_unavailable'",
    InspectKind.SYSTEM: "uname -a; hostname; whoami; id",
}


def _build_command(kind: InspectKind, *, lines: int = 50, unit: Optional[str] = None) -> str:
    if kind == InspectKind.LOGS:
        lines = max(1, min(int(lines or 50), 200))
        if unit:
            unit = _safe_unit(unit)
            return f"journalctl -u {unit} -n {lines} --no-pager -o short-iso"
        return _COMMANDS[InspectKind.LOGS].format(lines=lines)
    if kind == InspectKind.SERVICES and unit:
        unit = _safe_unit(unit)
        return f"systemctl status {unit} --no-pager -l | head -n 40"
    return _COMMANDS[kind]


def _safe_unit(unit: str) -> str:
    u = (unit or "").strip()
    if not u or len(u) > 128:
        raise InspectError("invalid_unit")
    if not re.match(r"^[A-Za-z0-9@_.\\-]+$", u):
        raise InspectError("invalid_unit")
    if ".." in u or "/" in u:
        raise InspectError("invalid_unit")
    return u


@dataclass
class InspectResult:
    kind: str
    status: str
    connection_id: str
    command: str
    exit_status: Optional[int]
    stdout: str
    stderr: str
    parsed: dict
    evidence_id: str
    host_identity: dict
    risk_class: str
    duration_ms: int

    def to_public(self) -> dict:
        out = {
            "kind": self.kind,
            "status": self.status,
            "connection_id": self.connection_id,
            "command": self.command,
            "exit_status": self.exit_status,
            "stdout": self.stdout[:12000],
            "stderr": self.stderr[:4000],
            "parsed": self.parsed,
            "evidence_id": self.evidence_id,
            "host_identity": self.host_identity,
            "risk_class": self.risk_class,
            "duration_ms": self.duration_ms,
            "mode": "remote_ssh",
        }
        assert_no_secret_material(out)
        return out


def _parse_processes(stdout: str) -> dict:
    rows = []
    for line in (stdout or "").splitlines()[1:]:
        parts = line.split(None, 10)
        if len(parts) >= 11:
            rows.append({
                "user": parts[0],
                "pid": parts[1],
                "cpu": parts[2],
                "mem": parts[3],
                "command": parts[10][:200],
            })
        elif line.strip():
            rows.append({"raw": line[:300]})
    return {"processes": rows[:40], "count": len(rows)}


def _parse_services(stdout: str) -> dict:
    rows = []
    for line in (stdout or "").splitlines():
        parts = line.split(None, 4)
        if len(parts) >= 1 and parts[0].endswith(".service"):
            rows.append({
                "unit": parts[0],
                "load": parts[1] if len(parts) > 1 else "",
                "active": parts[2] if len(parts) > 2 else "",
                "sub": parts[3] if len(parts) > 3 else "",
                "description": parts[4][:120] if len(parts) > 4 else "",
            })
        elif line.strip():
            rows.append({"raw": line[:300]})
    return {"services": rows[:40], "count": len(rows)}


def _parse_logs(stdout: str) -> dict:
    lines = [ln[:500] for ln in (stdout or "").splitlines() if ln.strip()]
    return {"lines": lines[:200], "count": len(lines)}


def _parse_resources(stdout: str) -> dict:
    return {"raw_sections": [s.strip() for s in (stdout or "").split("---") if s.strip()]}


def _parse_containers(stdout: str) -> dict:
    if "docker_unavailable" in (stdout or ""):
        return {"available": False, "containers": []}
    rows = []
    for line in (stdout or "").splitlines()[1:]:
        if line.strip():
            rows.append({"raw": line[:300]})
    return {"available": True, "containers": rows[:40], "count": len(rows)}


def _parse_network(stdout: str) -> dict:
    if "net_tools_unavailable" in (stdout or ""):
        return {"available": False, "listeners": []}
    rows = [ln[:300] for ln in (stdout or "").splitlines() if ln.strip()]
    return {"available": True, "listeners": rows[:40], "count": len(rows)}


def _parse_system(stdout: str) -> dict:
    lines = [ln.strip() for ln in (stdout or "").splitlines() if ln.strip()]
    return {"lines": lines}


_PARSERS = {
    InspectKind.PROCESSES: _parse_processes,
    InspectKind.SERVICES: _parse_services,
    InspectKind.LOGS: _parse_logs,
    InspectKind.RESOURCES: _parse_resources,
    InspectKind.CONTAINERS: _parse_containers,
    InspectKind.NETWORK: _parse_network,
    InspectKind.SYSTEM: _parse_system,
}


async def inspect_remote(
    *,
    owner_id: str,
    connection_id: str,
    kind: InspectKind | str,
    actor: str = "user",
    lines: int = 50,
    unit: Optional[str] = None,
    transport: Any = None,
    timeout_s: float = 45.0,
) -> InspectResult:
    if isinstance(kind, str):
        try:
            kind = InspectKind(kind)
        except ValueError as e:
            raise InspectError("unknown_inspect_kind") from e

    command = _build_command(kind, lines=lines, unit=unit)
    # Force read-only risk path; policy still classifies
    req = SshExecRequest(
        host_id=connection_id,
        command=command,
        owner_id=owner_id,
        actor=actor,
        timeout_s=timeout_s,
        risk_class=RiskClass.READ_ONLY.value,
        user_confirmed=False,
        from_inspect_template=True,
    )
    try:
        ev = await governed_ssh_exec(req, transport=transport)
    except Exception as e:
        # Ownership / connection failures → denied result for API layer
        code = getattr(e, "args", [None])[0] or type(e).__name__
        return InspectResult(
            kind=kind.value,
            status="denied",
            connection_id=connection_id,
            command=command,
            exit_status=None,
            stdout="",
            stderr=str(code)[:200],
            parsed={},
            evidence_id="",
            host_identity={},
            risk_class=RiskClass.READ_ONLY.value,
            duration_ms=0,
        )
    stdout = scrub_ssh_secrets_from_text(ev.stdout_sanitized or "")
    stderr = scrub_ssh_secrets_from_text(ev.stderr_sanitized or "")
    parsed = _PARSERS[kind](stdout)
    return InspectResult(
        kind=kind.value,
        status=ev.status,
        connection_id=connection_id,
        command=ev.command,
        exit_status=ev.exit_status,
        stdout=stdout,
        stderr=stderr,
        parsed=parsed,
        evidence_id=ev.evidence_id,
        host_identity=dict(ev.host_identity or {}),
        risk_class=ev.risk_class,
        duration_ms=ev.duration_ms,
    )


def list_inspect_kinds() -> list[dict]:
    return [
        {"kind": k.value, "command_template": _COMMANDS.get(k, ""), "read_only": True}
        for k in InspectKind
    ]
