"""Remote-operation agent workflow:

OBSERVE → PLAN → AUTHORIZE → EXECUTE → VERIFY → RECOVER/ROLLBACK → REPORT

Never OBSERVE → arbitrary EXECUTE.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from governance.ssh_diagnostics import (
    DiagnosticCapability,
    run_diagnostic,
    DiagnosticResult,
)
from governance.ssh_agent_exec import (
    IntentRequest,
    plan_from_intent,
    SshExecRequest,
    governed_ssh_exec,
    SshExecEvidence,
)
from governance.ssh_command_policy import evaluate_command_policy, CommandPolicyInput, RiskClass
from governance import ssh_cancel
from governance.ssh_credentials import assert_no_secret_material


class WorkflowPhase(str, Enum):
    OBSERVE = "observe"
    PLAN = "plan"
    AUTHORIZE = "authorize"
    EXECUTE = "execute"
    VERIFY = "verify"
    RECOVER = "recover"
    REPORT = "report"
    CANCELLED = "cancelled"
    FAILED = "failed"


@dataclass
class WorkflowReport:
    job_id: str
    phase: str
    status: str  # completed|failed|cancelled — never "success" for cancel
    success: bool
    observations: list[dict] = field(default_factory=list)
    plan: Optional[dict] = None
    authorizations: list[dict] = field(default_factory=list)
    executions: list[dict] = field(default_factory=list)
    verifications: list[dict] = field(default_factory=list)
    remediation_proposed: list[str] = field(default_factory=list)
    before_after: dict = field(default_factory=dict)
    outcome: str = ""

    def to_public(self) -> dict:
        out = {
            "job_id": self.job_id,
            "phase": self.phase,
            "status": self.status,
            "success": self.success,
            "observations": self.observations,
            "plan": self.plan,
            "authorizations": self.authorizations,
            "executions": self.executions,
            "verifications": self.verifications,
            "remediation_proposed": self.remediation_proposed,
            "before_after": self.before_after,
            "outcome": self.outcome,
        }
        assert_no_secret_material(out)
        return out


_OBSERVE_CAPS = [
    DiagnosticCapability.GET_SYSTEM_INFO,
    DiagnosticCapability.GET_UPTIME,
    DiagnosticCapability.GET_MEMORY_USAGE,
    DiagnosticCapability.GET_DISK_USAGE,
    DiagnosticCapability.LIST_CONTAINERS,
    DiagnosticCapability.LIST_SERVICES,
    DiagnosticCapability.GET_RECENT_LOGS,
    DiagnosticCapability.GET_SYSTEM_HEALTH,
]


async def run_remote_diagnostic_workflow(
    *,
    owner_id: str,
    connection_id: str,
    objective: str,
    actor: str = "agent",
    user_confirmed: bool = False,
    transport: Any = None,
    job_id: Optional[str] = None,
    approved_remediation_commands: Optional[list[str]] = None,
) -> WorkflowReport:
    """Full OBSERVE→…→REPORT for objectives like 'why is my server unhealthy?'."""
    job_id = job_id or uuid.uuid4().hex
    ssh_cancel.register_job(job_id, kind="ssh_remote_workflow", owner_id=owner_id)
    report = WorkflowReport(job_id=job_id, phase=WorkflowPhase.OBSERVE.value, status="running", success=False)

    def _cancelled() -> bool:
        return ssh_cancel.is_cancelled(job_id)

    # --- OBSERVE ---
    report.phase = WorkflowPhase.OBSERVE.value
    for cap in _OBSERVE_CAPS:
        if _cancelled():
            report.phase = WorkflowPhase.CANCELLED.value
            report.status = "cancelled"
            report.success = False
            report.outcome = "cancelled_during_observe"
            ssh_cancel.mark_cancelled(job_id)
            return report
        dr = await run_diagnostic(
            owner_id=owner_id,
            connection_id=connection_id,
            capability=cap,
            actor=actor,
            transport=transport,
            job_id=job_id,
            lines=30,
        )
        report.observations.append(dr.to_public())

    # --- PLAN ---
    report.phase = WorkflowPhase.PLAN.value
    if _cancelled():
        report.status = "cancelled"
        report.phase = WorkflowPhase.CANCELLED.value
        report.outcome = "cancelled_during_plan"
        return report

    intent = IntentRequest(
        target_host_connection_id=connection_id,
        objective=objective,
        owner_id=owner_id,
        actor=actor,
    )
    plan = plan_from_intent(intent)
    report.plan = plan.to_dict()

    # Evidence-supported findings (heuristic from structured diagnostics)
    findings = []
    for obs in report.observations:
        st = obs.get("structured") or {}
        if obs.get("capability") == "GetMemoryUsage":
            mem = st.get("memory") or {}
            # very light signal only
            findings.append("memory_snapshot_collected")
        if obs.get("capability") == "GetDiskUsage":
            for d in st.get("disks") or []:
                pct = str(d.get("use_pct") or "").rstrip("%")
                try:
                    if int(pct) >= 90:
                        findings.append(f"disk_high_usage:{d.get('mounted')}:{pct}%")
                except ValueError:
                    pass
        if obs.get("capability") == "GetSystemHealth":
            findings.append("health_snapshot_collected")
        if obs.get("status") == "failed":
            findings.append(f"observe_failed:{obs.get('capability')}")

    report.remediation_proposed = [
        "Review high disk usage and free space" if any("disk_high" in f for f in findings) else "",
        "Inspect failing services from service list",
        "Inspect container status if docker available",
    ]
    report.remediation_proposed = [r for r in report.remediation_proposed if r]

    # --- AUTHORIZE ---
    report.phase = WorkflowPhase.AUTHORIZE.value
    approved = list(approved_remediation_commands or [])
    for cmd in approved:
        if _cancelled():
            break
        pol = evaluate_command_policy(CommandPolicyInput(
            command=cmd, actor=actor, user_confirmed=user_confirmed,
        ))
        report.authorizations.append(pol.to_dict())
        if not pol.allowed:
            report.phase = WorkflowPhase.REPORT.value
            report.status = "failed"
            report.outcome = "authorization_denied_before_execute"
            report.success = False
            return report

    # --- EXECUTE (only authorized remediation; never raw observe→execute) ---
    report.phase = WorkflowPhase.EXECUTE.value
    report.before_after["before"] = [o for o in report.observations if o.get("capability") == "GetSystemHealth"]

    if not approved:
        # Observe-only diagnostic workflow ends at report without execute
        report.phase = WorkflowPhase.VERIFY.value
        report.verifications.append({"note": "no_remediation_authorized", "findings": findings})
        report.phase = WorkflowPhase.REPORT.value
        report.status = "completed"
        report.success = True
        report.outcome = "observe_plan_report_only"
        return report

    for cmd in approved:
        if _cancelled():
            report.phase = WorkflowPhase.CANCELLED.value
            report.status = "cancelled"
            report.success = False
            report.outcome = "cancelled_during_execute"
            ssh_cancel.mark_cancelled(job_id)
            return report
        ev = await governed_ssh_exec(
            SshExecRequest(
                host_id=connection_id,
                command=cmd,
                owner_id=owner_id,
                actor=actor,
                user_confirmed=user_confirmed,
                cancel_job_id=job_id,
            ),
            transport=transport,
        )
        report.executions.append(ev.to_agent_result())
        if ev.status in ("failed", "denied", "cancelled"):
            report.phase = WorkflowPhase.RECOVER.value
            report.outcome = f"execute_{ev.status}"
            # no blind continue
            break

    if _cancelled() or any(e.get("status") == "cancelled" for e in report.executions):
        report.phase = WorkflowPhase.CANCELLED.value
        report.status = "cancelled"
        report.success = False
        report.outcome = "cancelled"
        return report

    # --- VERIFY ---
    report.phase = WorkflowPhase.VERIFY.value
    if not _cancelled():
        health = await run_diagnostic(
            owner_id=owner_id,
            connection_id=connection_id,
            capability=DiagnosticCapability.GET_SYSTEM_HEALTH,
            actor=actor,
            transport=transport,
            job_id=job_id,
        )
        report.verifications.append(health.to_public())
        report.before_after["after"] = [health.to_public()]

    # --- REPORT ---
    report.phase = WorkflowPhase.REPORT.value
    failed_exec = any(e.get("status") in ("failed", "denied") for e in report.executions)
    report.success = not failed_exec and report.status != "cancelled"
    report.status = "failed" if failed_exec else "completed"
    if not report.outcome:
        report.outcome = "workflow_completed" if report.success else "workflow_failed"
    return report
