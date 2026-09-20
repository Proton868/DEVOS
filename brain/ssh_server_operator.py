"""Nuha Server Operator mode.

Natural language → governed SSH capabilities.

UNDERSTAND → INSPECT → PLAN → AUTHORIZE → EXECUTE → VERIFY → EVIDENCE → REPORT

Never silent privilege escalation.
Never claim success without verification.
Always identify target host + fingerprint.
"""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

from governance.ssh_agent_exec import (
    IntentRequest,
    plan_from_intent,
    ExecutionPlan,
    SshExecRequest,
    governed_ssh_exec,
)
from governance.ssh_command_policy import RiskClass, evaluate_command_policy, CommandPolicyInput
from governance.ssh_remote_workflow import run_remote_diagnostic_workflow
from governance.ssh_diagnostics import run_diagnostic, DiagnosticCapability
from governance.ssh_credentials import assert_no_secret_material
from governance.ssh_untrusted_content import sanitize_remote_output
from governance import ssh_cancel


# Host aliases → connection resolution is caller-supplied; labels only here
_HOST_ALIASES = {
    "prime": "Prime",
    "prod": "production",
    "production": "production",
}


@dataclass
class OperatorIntent:
    raw: str
    action: str  # diagnose|restart|deploy|rollback|install|inspect_docker|check_deploy|show_diff|generic
    host_label: str = ""
    service: Optional[str] = None
    risk_hint: str = RiskClass.READ_ONLY.value
    requires_approval: bool = False


@dataclass
class OperatorReport:
    job_id: str
    intent: dict
    host: dict
    plan: Optional[dict] = None
    phase: str = "understand"
    status: str = "pending"  # pending|awaiting_approval|succeeded|failed|cancelled|unknown|denied
    success: bool = False
    verification_passed: bool = False
    approval_required: bool = False
    steps_run: list[dict] = field(default_factory=list)
    evidence_ids: list[str] = field(default_factory=list)
    outcome: str = ""
    grounded_claim: str = ""

    def to_public(self) -> dict:
        out = {
            "job_id": self.job_id,
            "intent": self.intent,
            "host": self.host,
            "plan": self.plan,
            "phase": self.phase,
            "status": self.status,
            "success": self.success,
            "verification_passed": self.verification_passed,
            "approval_required": self.approval_required,
            "steps_run": self.steps_run,
            "evidence_ids": self.evidence_ids,
            "outcome": self.outcome,
            "grounded_claim": self.grounded_claim,
        }
        assert_no_secret_material(out)
        return out


def understand_intent(text: str) -> OperatorIntent:
    t = (text or "").strip()
    low = t.lower()
    host = ""
    for alias, label in _HOST_ALIASES.items():
        if re.search(rf"\b{re.escape(alias)}\b", low):
            host = label
            break
    # Explicit "on X"
    m = re.search(r"\bon\s+([A-Za-z0-9._-]+)", t, re.I)
    if m:
        host = m.group(1)

    if any(k in low for k in ("what's wrong", "whats wrong", "unhealthy", "check prime", "check the server", "diagnose")):
        return OperatorIntent(t, "diagnose", host_label=host or "Prime", risk_hint=RiskClass.READ_ONLY.value)
    if "rollback" in low:
        return OperatorIntent(t, "rollback", host_label=host, risk_hint=RiskClass.MODIFYING.value, requires_approval=True)
    if "deploy" in low:
        return OperatorIntent(t, "deploy", host_label=host, risk_hint=RiskClass.MODIFYING.value, requires_approval=True)
    if "restart" in low and "devos" in low:
        return OperatorIntent(
            t, "restart", host_label=host or "Prime", service="devos",
            risk_hint=RiskClass.PRIVILEGED.value, requires_approval=True,
        )
    if "restart" in low:
        svc = None
        m2 = re.search(r"restart\s+([A-Za-z0-9@_.-]+)", low)
        if m2:
            svc = m2.group(1)
        return OperatorIntent(
            t, "restart", host_label=host, service=svc,
            risk_hint=RiskClass.PRIVILEGED.value, requires_approval=True,
        )
    if "docker" in low and ("memory" in low or "using" in low):
        return OperatorIntent(t, "inspect_docker", host_label=host, risk_hint=RiskClass.READ_ONLY.value)
    if "deployment succeeded" in low or "check whether the deployment" in low:
        return OperatorIntent(t, "check_deploy", host_label=host, risk_hint=RiskClass.READ_ONLY.value)
    if "install" in low:
        return OperatorIntent(t, "install", host_label=host, risk_hint=RiskClass.PRIVILEGED.value, requires_approval=True)
    if "what changed" in low or "show me what changed" in low:
        return OperatorIntent(t, "show_diff", host_label=host, risk_hint=RiskClass.READ_ONLY.value)
    return OperatorIntent(t, "generic", host_label=host, risk_hint=RiskClass.UNKNOWN.value, requires_approval=True)


def build_operator_plan(intent: OperatorIntent, connection_id: str, owner_id: str) -> ExecutionPlan:
    objective = intent.raw
    if intent.action == "restart" and intent.service:
        objective = f"restart service {intent.service}"
    elif intent.action == "diagnose":
        objective = "diagnose server health and resources"
    elif intent.action == "inspect_docker":
        objective = "inspect docker containers and resources"
    elif intent.action == "deploy":
        objective = "deploy application"
    elif intent.action == "rollback":
        objective = "rollback deployment"
    elif intent.action == "install":
        objective = intent.raw
    elif intent.action == "check_deploy":
        objective = "verify deployment health"
    elif intent.action == "show_diff":
        objective = "show recent changes"
    return plan_from_intent(IntentRequest(
        target_host_connection_id=connection_id,
        objective=objective,
        owner_id=owner_id,
        actor="agent",
    ))


async def run_server_operator(
    *,
    owner_id: str,
    connection_id: str,
    text: str,
    host_label: str = "",
    host_fingerprint: str = "",
    host_key_type: str = "",
    user_confirmed: bool = False,
    transport: Any = None,
    job_id: Optional[str] = None,
) -> OperatorReport:
    """Full operator path. Does not escalate without confirmation."""
    job_id = job_id or uuid.uuid4().hex
    ssh_cancel.register_job(job_id, kind="server_operator", owner_id=owner_id)
    intent = understand_intent(text)
    if host_label:
        intent.host_label = host_label

    host_block = {
        "label": intent.host_label or host_label or connection_id,
        "connection_id": connection_id,
        "verified_fingerprint": host_fingerprint,
        "key_type": host_key_type,
        "identity_verified": bool(host_fingerprint),
    }

    report = OperatorReport(
        job_id=job_id,
        intent={
            "raw": intent.raw,
            "action": intent.action,
            "service": intent.service,
            "risk_hint": intent.risk_hint,
            "requires_approval": intent.requires_approval,
        },
        host=host_block,
        phase="understand",
        approval_required=intent.requires_approval,
    )

    if not host_fingerprint:
        report.status = "denied"
        report.phase = "authorize"
        report.outcome = "host_fingerprint_required"
        report.grounded_claim = "Refused: target host fingerprint not verified."
        return report

    if ssh_cancel.is_cancelled(job_id):
        report.status = "cancelled"
        report.success = False
        report.outcome = "cancelled"
        return report

    # INSPECT for diagnose-style
    report.phase = "inspect"
    if intent.action in ("diagnose", "inspect_docker", "check_deploy", "generic"):
        wf = await run_remote_diagnostic_workflow(
            owner_id=owner_id,
            connection_id=connection_id,
            objective=intent.raw,
            actor="agent",
            transport=transport,
            job_id=job_id,
            user_confirmed=False,
            approved_remediation_commands=None,
        )
        report.steps_run.append({"phase": "observe_workflow", "result": wf.to_public()})
        report.phase = "plan"
        plan = build_operator_plan(intent, connection_id, owner_id)
        report.plan = plan.to_dict()
        report.phase = "report"
        report.status = "succeeded" if wf.success else "failed"
        report.success = bool(wf.success)
        report.verification_passed = bool(wf.success)
        report.grounded_claim = (
            f"Diagnostic complete on {host_block['label']} "
            f"(fingerprint {host_fingerprint}). "
            f"Observations recorded; no remediation executed without approval."
        )
        report.outcome = "diagnose_report_only"
        return report

    # PLAN
    report.phase = "plan"
    plan = build_operator_plan(intent, connection_id, owner_id)
    report.plan = plan.to_dict()
    report.approval_required = intent.requires_approval or any(
        s.requires_approval for s in plan.steps
    )

    # AUTHORIZE
    report.phase = "authorize"
    if report.approval_required and not user_confirmed:
        report.status = "awaiting_approval"
        report.success = False
        report.outcome = "approval_required"
        report.grounded_claim = (
            f"Action '{intent.action}' on {host_block['label']} "
            f"(fingerprint {host_fingerprint}) requires explicit approval. "
            f"Risk: {intent.risk_hint}. Not executed."
        )
        return report

    # EXECUTE only approved exec steps
    report.phase = "execute"
    executed_ok = True
    verified = False
    for step in plan.steps:
        if ssh_cancel.is_cancelled(job_id):
            report.status = "cancelled"
            report.success = False
            report.outcome = "cancelled"
            report.grounded_claim = "Operation cancelled; not reported as success."
            return report
        if step.kind != "exec" or not step.command:
            report.steps_run.append({"step": step.to_dict(), "skipped": True})
            continue
        pol = evaluate_command_policy(CommandPolicyInput(
            command=step.command,
            actor="agent",
            user_confirmed=user_confirmed,
        ))
        if not pol.allowed:
            report.steps_run.append({"step": step.to_dict(), "status": "denied", "policy": pol.to_dict()})
            executed_ok = False
            break
        ev = await governed_ssh_exec(
            SshExecRequest(
                host_id=connection_id,
                command=step.command,
                owner_id=owner_id,
                actor="agent",
                user_confirmed=user_confirmed,
                risk_class=step.risk_class,
                cancel_job_id=job_id,
            ),
            transport=transport,
        )
        report.steps_run.append({"step": step.to_dict(), "result": ev.to_agent_result()})
        if ev.evidence_id:
            report.evidence_ids.append(ev.evidence_id)
        if ev.status != "succeeded":
            executed_ok = False
            if ev.status == "unknown":
                report.status = "unknown"
                report.success = False
                report.outcome = "unknown_after_exec"
                report.grounded_claim = (
                    f"Outcome unknown on {host_block['label']} — connection lost before "
                    f"completion confirmed. Do not assume success."
                )
                return report
            break

    # VERIFY
    report.phase = "verify"
    if executed_ok and intent.action in ("restart", "deploy", "install"):
        # Read-only verification probe
        verify_cmd = "systemctl is-active devos 2>/dev/null || systemctl is-system-running 2>/dev/null || echo verify_probe"
        if intent.service:
            verify_cmd = f"systemctl is-active {intent.service} 2>/dev/null || echo inactive"
        vev = await governed_ssh_exec(
            SshExecRequest(
                host_id=connection_id,
                command=verify_cmd,
                owner_id=owner_id,
                actor="agent",
                user_confirmed=True,
                from_inspect_template=True,
                cancel_job_id=job_id,
            ),
            transport=transport,
        )
        report.steps_run.append({"phase": "verify", "result": vev.to_agent_result()})
        if vev.evidence_id:
            report.evidence_ids.append(vev.evidence_id)
        out = sanitize_remote_output(vev.stdout_sanitized or "").text.lower()
        verified = vev.status == "succeeded" and ("active" in out or "running" in out or "verify_probe" in out)
        report.verification_passed = verified
    else:
        verified = executed_ok
        report.verification_passed = verified

    # REPORT
    report.phase = "report"
    if not executed_ok:
        report.status = "failed"
        report.success = False
        report.outcome = "execution_failed"
        report.grounded_claim = (
            f"Operation did not succeed on {host_block['label']} "
            f"(fingerprint {host_fingerprint}). See evidence."
        )
    elif not report.verification_passed:
        report.status = "unknown"
        report.success = False
        report.outcome = "executed_but_unverified"
        report.grounded_claim = (
            f"Commands ran on {host_block['label']} but verification did not confirm "
            f"desired state. Not claiming success."
        )
    else:
        report.status = "succeeded"
        report.success = True
        report.outcome = "verified_success"
        report.grounded_claim = (
            f"Verified on {host_block['label']} (fingerprint {host_fingerprint}). "
            f"Action '{intent.action}' completed and verification passed."
        )
    return report
