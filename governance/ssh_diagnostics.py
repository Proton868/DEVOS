"""Structured remote diagnostic capabilities (governed, read-only).

Prefer these over free-form shell when a structured interface is practical.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from governance.ssh_agent_exec import SshExecRequest, governed_ssh_exec
from governance.ssh_command_policy import RiskClass
from governance.ssh_credentials import assert_no_secret_material, scrub_ssh_secrets_from_text
from governance import ssh_cancel


class DiagnosticCapability(str, Enum):
    GET_SYSTEM_INFO = "GetSystemInfo"
    GET_DISK_USAGE = "GetDiskUsage"
    GET_MEMORY_USAGE = "GetMemoryUsage"
    GET_CPU_INFO = "GetCpuInfo"
    LIST_PROCESSES = "ListProcesses"
    LIST_SERVICES = "ListServices"
    GET_SERVICE_STATUS = "GetServiceStatus"
    LIST_CONTAINERS = "ListContainers"
    GET_CONTAINER_LOGS = "GetContainerLogs"
    GET_LISTENING_PORTS = "GetListeningPorts"
    GET_UPTIME = "GetUptime"
    GET_KERNEL = "GetKernel"
    GET_FILESYSTEM = "GetFilesystem"
    GET_RECENT_LOGS = "GetRecentLogs"
    GET_SYSTEM_HEALTH = "GetSystemHealth"


_TEMPLATES: dict[DiagnosticCapability, str] = {
    DiagnosticCapability.GET_SYSTEM_INFO: "uname -a; hostname; cat /etc/os-release 2>/dev/null | head -n 12",
    DiagnosticCapability.GET_KERNEL: "uname -r; uname -m",
    DiagnosticCapability.GET_CPU_INFO: "nproc; lscpu 2>/dev/null | head -n 20 || cat /proc/cpuinfo | head -n 20",
    DiagnosticCapability.GET_DISK_USAGE: "df -h",
    DiagnosticCapability.GET_MEMORY_USAGE: "free -m",
    DiagnosticCapability.GET_UPTIME: "uptime",
    DiagnosticCapability.GET_FILESYSTEM: "df -hT; mount | head -n 30",
    DiagnosticCapability.LIST_PROCESSES: "ps aux --sort=-%cpu | head -n 40",
    DiagnosticCapability.LIST_SERVICES: "systemctl list-units --type=service --state=running --no-pager --no-legend | head -n 40",
    DiagnosticCapability.GET_SERVICE_STATUS: "systemctl status {unit} --no-pager -l | head -n 40",
    DiagnosticCapability.LIST_CONTAINERS: "docker ps -a --format '{{.ID}} {{.Image}} {{.Status}} {{.Names}}' 2>/dev/null || echo docker_unavailable",
    DiagnosticCapability.GET_CONTAINER_LOGS: "docker logs --tail {lines} {container} 2>&1 | tail -n {lines}",
    DiagnosticCapability.GET_LISTENING_PORTS: "ss -tulpn 2>/dev/null | head -n 40 || netstat -tulpn 2>/dev/null | head -n 40",
    DiagnosticCapability.GET_RECENT_LOGS: "journalctl -n {lines} --no-pager -o short-iso",
    DiagnosticCapability.GET_SYSTEM_HEALTH: (
        "echo HEALTH_BEGIN; uptime; echo ---; free -m; echo ---; df -h /; "
        "echo ---; systemctl is-system-running 2>/dev/null || echo unknown; echo HEALTH_END"
    ),
}


def _safe_token(value: str, *, label: str = "token") -> str:
    v = (value or "").strip()
    if not v or len(v) > 128:
        raise ValueError(f"invalid_{label}")
    if not re.match(r"^[A-Za-z0-9@_.:\\-]+$", v):
        raise ValueError(f"invalid_{label}")
    if ".." in v or "/" in v:
        raise ValueError(f"invalid_{label}")
    return v


def _command_for(
    cap: DiagnosticCapability,
    *,
    unit: Optional[str] = None,
    container: Optional[str] = None,
    lines: int = 50,
) -> str:
    lines = max(1, min(int(lines or 50), 200))
    tmpl = _TEMPLATES[cap]
    if "{unit}" in tmpl:
        return tmpl.format(unit=_safe_token(unit or "", label="unit"))
    if "{container}" in tmpl:
        return tmpl.format(
            container=_safe_token(container or "", label="container"),
            lines=lines,
        )
    if "{lines}" in tmpl:
        return tmpl.format(lines=lines)
    return tmpl


@dataclass
class DiagnosticResult:
    capability: str
    status: str  # succeeded|failed|denied|cancelled
    structured: dict = field(default_factory=dict)
    command: str = ""
    exit_status: Optional[int] = None
    evidence_id: str = ""
    host_identity: dict = field(default_factory=dict)
    duration_ms: int = 0
    stdout_preview: str = ""

    def to_public(self) -> dict:
        out = {
            "capability": self.capability,
            "status": self.status,
            "structured": self.structured,
            "command": self.command,
            "exit_status": self.exit_status,
            "evidence_id": self.evidence_id,
            "host_identity": self.host_identity,
            "duration_ms": self.duration_ms,
            "stdout_preview": self.stdout_preview[:4000],
            "mode": "remote_ssh",
        }
        assert_no_secret_material(out)
        return out


def _parse(cap: DiagnosticCapability, stdout: str) -> dict:
    text = stdout or ""
    if cap == DiagnosticCapability.GET_DISK_USAGE:
        rows = []
        for line in text.splitlines()[1:]:
            p = line.split()
            if len(p) >= 6:
                rows.append({
                    "filesystem": p[0], "size": p[1], "used": p[2],
                    "avail": p[3], "use_pct": p[4], "mounted": p[5],
                })
        return {"disks": rows}
    if cap == DiagnosticCapability.GET_MEMORY_USAGE:
        rows = {}
        for line in text.splitlines()[1:]:
            p = line.split()
            if len(p) >= 4:
                rows[p[0].rstrip(":")] = {"total": p[1], "used": p[2], "free": p[3]}
        return {"memory": rows}
    if cap == DiagnosticCapability.LIST_PROCESSES:
        procs = []
        for line in text.splitlines()[1:]:
            p = line.split(None, 10)
            if len(p) >= 11:
                procs.append({"user": p[0], "pid": p[1], "cpu": p[2], "mem": p[3], "cmd": p[10][:160]})
        return {"processes": procs}
    if cap == DiagnosticCapability.LIST_SERVICES:
        svcs = []
        for line in text.splitlines():
            p = line.split(None, 4)
            if p and p[0].endswith(".service"):
                svcs.append({"unit": p[0], "active": p[2] if len(p) > 2 else ""})
        return {"services": svcs}
    if cap == DiagnosticCapability.LIST_CONTAINERS:
        if "docker_unavailable" in text:
            return {"available": False, "containers": []}
        return {"available": True, "containers": [ln.strip() for ln in text.splitlines() if ln.strip()]}
    if cap == DiagnosticCapability.GET_LISTENING_PORTS:
        return {"listeners": [ln.strip() for ln in text.splitlines() if ln.strip()][:40]}
    if cap == DiagnosticCapability.GET_SYSTEM_HEALTH:
        return {"health_raw": text.strip(), "ok_hint": "running" in text.lower() or "degraded" in text.lower()}
    if cap == DiagnosticCapability.GET_SYSTEM_INFO:
        return {"lines": [ln.strip() for ln in text.splitlines() if ln.strip()][:20]}
    return {"raw_lines": [ln.strip() for ln in text.splitlines() if ln.strip()][:40]}


async def run_diagnostic(
    *,
    owner_id: str,
    connection_id: str,
    capability: DiagnosticCapability | str,
    actor: str = "agent",
    unit: Optional[str] = None,
    container: Optional[str] = None,
    lines: int = 50,
    transport: Any = None,
    job_id: Optional[str] = None,
    timeout_s: float = 45.0,
) -> DiagnosticResult:
    if isinstance(capability, str):
        capability = DiagnosticCapability(capability)
    if job_id and ssh_cancel.is_cancelled(job_id):
        return DiagnosticResult(capability=capability.value, status="cancelled")

    try:
        command = _command_for(capability, unit=unit, container=container, lines=lines)
    except ValueError as e:
        return DiagnosticResult(
            capability=capability.value, status="denied",
            structured={"error": str(e)},
        )

    req = SshExecRequest(
        host_id=connection_id,
        command=command,
        owner_id=owner_id,
        actor=actor,
        timeout_s=timeout_s,
        risk_class=RiskClass.READ_ONLY.value,
        from_inspect_template=True,
        cancel_job_id=job_id,
    )
    try:
        ev = await governed_ssh_exec(req, transport=transport)
    except Exception as e:
        return DiagnosticResult(
            capability=capability.value,
            status="denied",
            structured={"error": type(e).__name__},
            command=command,
        )

    if job_id and ssh_cancel.is_cancelled(job_id):
        status = "cancelled"
    else:
        status = ev.status

    stdout = scrub_ssh_secrets_from_text(ev.stdout_sanitized or "")
    structured = _parse(capability, stdout) if status == "succeeded" else {}
    return DiagnosticResult(
        capability=capability.value,
        status=status,
        structured=structured,
        command=ev.command,
        exit_status=ev.exit_status,
        evidence_id=ev.evidence_id,
        host_identity=dict(ev.host_identity or {}),
        duration_ms=ev.duration_ms,
        stdout_preview=stdout[:2000],
    )


def list_diagnostic_capabilities() -> list[str]:
    return [c.value for c in DiagnosticCapability]
