"""Governed agentic SSH execution.

Model proposes → policy authorizes → transport executes → evidence records.
Nuha never receives credential material or unrestricted transport handles.
"""
from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from governance.ssh_command_policy import (
    CommandPolicyInput,
    evaluate_command_policy,
    reject_remote_policy_injection,
    RiskClass,
)
from governance.ssh_credentials import scrub_ssh_secrets_from_text, assert_no_secret_material

logger = logging.getLogger("devos.ssh_agent_exec")


@dataclass
class IntentRequest:
    target_host_connection_id: str
    objective: str
    constraints: dict = field(default_factory=dict)
    owner_id: str = ""
    actor: str = "agent"

    def to_dict(self) -> dict:
        return {
            "target_host_connection_id": self.target_host_connection_id,
            "objective": self.objective,
            "constraints": dict(self.constraints),
            "owner_id": self.owner_id,
            "actor": self.actor,
        }


@dataclass
class PlanStep:
    step_id: str
    kind: str  # inspect | plan | approve | exec | verify
    description: str
    command: Optional[str] = None
    risk_class: Optional[str] = None
    requires_approval: bool = False
    verification: Optional[str] = None  # expected check description

    def to_dict(self) -> dict:
        return {
            "step_id": self.step_id,
            "kind": self.kind,
            "description": self.description,
            "command": self.command,
            "risk_class": self.risk_class,
            "requires_approval": self.requires_approval,
            "verification": self.verification,
        }


@dataclass
class ExecutionPlan:
    plan_id: str
    intent: IntentRequest
    steps: list[PlanStep]
    risk_level: str
    required_capabilities: list[str]
    expected_effects: list[str]

    def to_dict(self) -> dict:
        return {
            "plan_id": self.plan_id,
            "intent": self.intent.to_dict(),
            "steps": [s.to_dict() for s in self.steps],
            "risk_level": self.risk_level,
            "required_capabilities": list(self.required_capabilities),
            "expected_effects": list(self.expected_effects),
        }


@dataclass
class SshExecRequest:
    host_id: str  # connection_id
    command: str
    working_directory: Optional[str] = None
    timeout_s: float = 60.0
    risk_class: Optional[str] = None
    owner_id: str = ""
    actor: str = "agent"
    user_confirmed: bool = False
    automation_policy_allows: bool = False
    # Fixed inspect templates may include pipes; still not free-form shell from the model.
    from_inspect_template: bool = False
    cancel_job_id: Optional[str] = None


@dataclass
class SshExecEvidence:
    evidence_id: str
    command: str
    exit_status: Optional[int]
    stdout_sanitized: str
    stderr_sanitized: str
    duration_ms: int
    host_identity: dict
    risk_class: str
    policy: dict
    status: str  # succeeded | failed | denied | cancelled | unknown
    actor: str
    injection_flags: list[str] = field(default_factory=list)

    def to_agent_result(self) -> dict:
        """What Nuha may see — no secrets."""
        out = {
            "evidence_id": self.evidence_id,
            "command": self.command,
            "exit_status": self.exit_status,
            "stdout": self.stdout_sanitized[:8000],
            "stderr": self.stderr_sanitized[:4000],
            "duration_ms": self.duration_ms,
            "host_identity": self.host_identity,
            "risk_class": self.risk_class,
            "status": self.status,
            "policy_allowed": self.policy.get("allowed"),
            "injection_flags": list(self.injection_flags),
        }
        assert_no_secret_material(out)
        return out


class SshExecDenied(Exception):
    def __init__(self, code: str, policy: Optional[dict] = None):
        self.code = code
        self.policy = policy or {}
        super().__init__(code)


async def governed_ssh_exec(
    req: SshExecRequest,
    *,
    transport_session_id: Optional[str] = None,
    transport: Any = None,
    host_identity: Optional[dict] = None,
) -> SshExecEvidence:
    """Authorize + execute one remote command. Stops on policy deny."""
    from governance.ssh_domain import assert_connection_usable
    from execution.ssh_transport import SshTransportService, MockSshBackend

    await assert_connection_usable(req.owner_id, req.host_id)

    if req.from_inspect_template:
        from governance.ssh_command_policy import CommandPolicyDecision
        policy = CommandPolicyDecision(
            risk_class=RiskClass.READ_ONLY,
            allowed=True,
            requires_confirmation=False,
            reasons=["inspect_template_allowlist"],
            matched_patterns=["inspect_template"],
            sanitized_command=(req.command or "").strip()[:4000],
            remote_output_influence=False,
        )
    else:
        policy = evaluate_command_policy(
            CommandPolicyInput(
                command=req.command,
                actor=req.actor,
                working_directory=req.working_directory,
                host_id=req.host_id,
                requested_risk_class=req.risk_class,
                user_confirmed=req.user_confirmed,
                automation_policy_allows=req.automation_policy_allows,
            )
        )

    host_identity = dict(host_identity or {})
    evidence_id = uuid.uuid4().hex

    if not policy.allowed:
        logger.info(
            "ssh_exec_denied owner=%s host=%s risk=%s reasons=%s",
            req.owner_id, req.host_id, policy.risk_class.value, policy.reasons,
        )
        return SshExecEvidence(
            evidence_id=evidence_id,
            command=policy.sanitized_command,
            exit_status=None,
            stdout_sanitized="",
            stderr_sanitized="",
            duration_ms=0,
            host_identity=host_identity,
            risk_class=policy.risk_class.value,
            policy=policy.to_dict(),
            status="denied",
            actor=req.actor,
        )

    from governance import ssh_cancel
    if req.cancel_job_id and ssh_cancel.is_cancelled(req.cancel_job_id):
        ssh_cancel.mark_cancelled(req.cancel_job_id)
        return SshExecEvidence(
            evidence_id=evidence_id,
            command=policy.sanitized_command,
            exit_status=None,
            stdout_sanitized="",
            stderr_sanitized="",
            duration_ms=0,
            host_identity=host_identity,
            risk_class=policy.risk_class.value,
            policy=policy.to_dict(),
            status="cancelled",
            actor=req.actor,
        )

    # Execute via existing transport (caller may supply live session)
    transport = transport or SshTransportService(backend=MockSshBackend())
    t0 = time.monotonic()
    exit_status: Optional[int] = None
    stdout = b""
    stderr = b""
    status = "succeeded"

    try:
        if transport_session_id:
            result = await transport.exec(
                transport_session_id, policy.sanitized_command, timeout_s=req.timeout_s
            )
        else:
            # Ephemeral connect path is host-verify gated by capability layer
            from execution.ssh_capabilities import SSHConnectionCapability

            cap = SSHConnectionCapability(owner_id=req.owner_id, transport=transport)
            session, verify = await cap.connect(
                connection_id=req.host_id, actor=req.actor
            )
            host_identity = verify.to_evidence()
            result = await transport.exec(
                session.session_id, policy.sanitized_command, timeout_s=req.timeout_s
            )
            await transport.close(session.session_id)
        exit_status = result.exit_status
        stdout = result.stdout or b""
        stderr = result.stderr or b""
        if exit_status not in (0, None):
            status = "failed"
    except Exception as e:
        status = "failed"
        stderr = str(getattr(e, "code", type(e).__name__)).encode()
        logger.info("ssh_exec_failed owner=%s err=%s", req.owner_id, type(e).__name__)

    duration_ms = int((time.monotonic() - t0) * 1000)
    out_s = scrub_ssh_secrets_from_text(stdout.decode("utf-8", errors="replace"))
    err_s = scrub_ssh_secrets_from_text(stderr.decode("utf-8", errors="replace"))
    flags = reject_remote_policy_injection(out_s + "\n" + err_s)
    if flags:
        # Remote output never upgrades policy; flag only
        logger.warning("ssh_exec_injection_phrases owner=%s flags=%s", req.owner_id, flags)

    if req.cancel_job_id and ssh_cancel.is_cancelled(req.cancel_job_id):
        status = "cancelled"
        ssh_cancel.mark_cancelled(req.cancel_job_id)
        ssh_cancel.mark_signal_sent(req.cancel_job_id, "post_exec_check")

    evidence = SshExecEvidence(
        evidence_id=evidence_id,
        command=policy.sanitized_command,
        exit_status=exit_status,
        stdout_sanitized=out_s[:8000],
        stderr_sanitized=err_s[:4000],
        duration_ms=duration_ms,
        host_identity=host_identity,
        risk_class=policy.risk_class.value,
        policy=policy.to_dict(),
        status=status,
        actor=req.actor,
        injection_flags=flags,
    )
    # Persist lightweight execution record when possible
    try:
        from governance.ssh_domain import create_execution_record

        await create_execution_record(
            owner_id=req.owner_id,
            tenant_id=None,
            connection_id=req.host_id,
            command=policy.sanitized_command,
            actor_id=req.owner_id,
            agent_id="nuha" if req.actor == "agent" else None,
        )
    except Exception:
        pass
    return evidence


def plan_from_intent(intent: IntentRequest) -> ExecutionPlan:
    """Deterministic planner for common server-management intents.

    Decomposes into inspect → plan → approve → exec → verify. Does not execute.
    """
    objective = (intent.objective or "").lower()
    steps: list[PlanStep] = []
    risk_level = RiskClass.READ_ONLY.value
    effects: list[str] = []

    def sid(i: int) -> str:
        return f"s{i}"

    # Always start with host/OS inspection
    steps.append(PlanStep(
        step_id=sid(1), kind="inspect", description="Verify target host and OS",
        command="uname -a", risk_class=RiskClass.READ_ONLY.value,
        verification="uname exits 0 and returns kernel string",
    ))
    steps.append(PlanStep(
        step_id=sid(2), kind="inspect", description="Inspect basic resources",
        command="df -h && free -m", risk_class=RiskClass.READ_ONLY.value,
        verification="disk and memory stats present",
    ))

    if any(k in objective for k in ("docker", "container")):
        steps.append(PlanStep(
            step_id=sid(3), kind="inspect", description="Inspect Docker presence",
            command="docker version", risk_class=RiskClass.READ_ONLY.value,
            verification="docker client responds or command missing is noted",
        ))
        if "install docker" in objective or "install" in objective and "docker" in objective:
            steps.append(PlanStep(
                step_id=sid(4), kind="approve",
                description="Request approval to install Docker",
                requires_approval=True,
                risk_class=RiskClass.PRIVILEGED.value,
            ))
            steps.append(PlanStep(
                step_id=sid(5), kind="exec",
                description="Install Docker (engine packages)",
                command="sudo apt-get update && sudo apt-get install -y docker.io",
                risk_class=RiskClass.PRIVILEGED.value,
                requires_approval=True,
                verification="docker version succeeds after install",
            ))
            risk_level = RiskClass.PRIVILEGED.value
            effects.append("docker_installed")

    if any(k in objective for k in ("deploy", "application", "app")):
        steps.append(PlanStep(
            step_id=sid(10), kind="inspect", description="Inspect current deployment layout",
            command="ls -la", risk_class=RiskClass.READ_ONLY.value,
        ))
        steps.append(PlanStep(
            step_id=sid(11), kind="plan", description="Build deployment plan from inspect results",
        ))
        steps.append(PlanStep(
            step_id=sid(12), kind="approve", description="Request approval for deployment changes",
            requires_approval=True, risk_class=RiskClass.MODIFYING.value,
        ))
        steps.append(PlanStep(
            step_id=sid(13), kind="exec", description="Apply deployment steps (placeholder commands)",
            command="echo deploy_placeholder", risk_class=RiskClass.MODIFYING.value,
            requires_approval=True,
            verification="health check endpoint or process check",
        ))
        steps.append(PlanStep(
            step_id=sid(14), kind="verify", description="Verify deployment health",
            command="echo health_check_placeholder", risk_class=RiskClass.READ_ONLY.value,
            verification="health check passes",
        ))
        risk_level = RiskClass.MODIFYING.value
        effects.append("application_deployed")

    if any(k in objective for k in ("restart service", "restart", "systemctl")):
        steps.append(PlanStep(
            step_id=sid(20), kind="inspect", description="Inspect service status",
            command="systemctl status", risk_class=RiskClass.READ_ONLY.value,
        ))
        steps.append(PlanStep(
            step_id=sid(21), kind="approve", description="Approve service restart",
            requires_approval=True, risk_class=RiskClass.PRIVILEGED.value,
        ))
        steps.append(PlanStep(
            step_id=sid(22), kind="exec", description="Restart service",
            command="sudo systemctl restart", risk_class=RiskClass.PRIVILEGED.value,
            requires_approval=True,
            verification="systemctl is-active returns active",
        ))
        risk_level = RiskClass.PRIVILEGED.value
        effects.append("service_restarted")

    if any(k in objective for k in ("log", "logs", "diagnose", "failure")):
        steps.append(PlanStep(
            step_id=sid(30), kind="inspect", description="Inspect recent logs",
            command="journalctl -n 50 --no-pager", risk_class=RiskClass.READ_ONLY.value,
            verification="log lines returned",
        ))

    if len(steps) <= 2 and "install" in objective:
        steps.append(PlanStep(
            step_id=sid(40), kind="approve", description="Approve package installation",
            requires_approval=True, risk_class=RiskClass.MODIFYING.value,
        ))
        steps.append(PlanStep(
            step_id=sid(41), kind="exec", description="Install requested software",
            command="sudo apt-get install -y", risk_class=RiskClass.PRIVILEGED.value,
            requires_approval=True,
            verification="package query shows installed",
        ))
        risk_level = RiskClass.PRIVILEGED.value
        effects.append("package_installed")

    # Terminal verification step
    steps.append(PlanStep(
        step_id=sid(99), kind="verify", description="Record evidence and stop on failure",
        verification="all critical steps succeeded; no continuation after failure",
    ))

    return ExecutionPlan(
        plan_id=uuid.uuid4().hex,
        intent=intent,
        steps=steps,
        risk_level=risk_level,
        required_capabilities=["ucip:ssh.exec", "ucip:ssh.connect"],
        expected_effects=effects,
    )


async def run_plan_steps(
    plan: ExecutionPlan,
    *,
    user_confirmed_steps: Optional[set[str]] = None,
    stop_on_failure: bool = True,
    transport: Any = None,
) -> list[SshExecEvidence]:
    """Execute plan steps with verification gates. Does not continue after critical failure."""
    user_confirmed_steps = user_confirmed_steps or set()
    results: list[SshExecEvidence] = []

    for step in plan.steps:
        if step.kind in ("plan", "approve", "verify") and not step.command:
            continue
        if not step.command:
            continue
        confirmed = step.step_id in user_confirmed_steps or (
            not step.requires_approval
        )
        # For requires_approval, need explicit confirmation
        if step.requires_approval and step.step_id not in user_confirmed_steps:
            ev = SshExecEvidence(
                evidence_id=uuid.uuid4().hex,
                command=step.command or "",
                exit_status=None,
                stdout_sanitized="",
                stderr_sanitized="",
                duration_ms=0,
                host_identity={},
                risk_class=step.risk_class or RiskClass.UNKNOWN.value,
                policy={"allowed": False, "reasons": ["step_approval_required"]},
                status="denied",
                actor=plan.intent.actor,
            )
            results.append(ev)
            if stop_on_failure:
                break
            continue

        req = SshExecRequest(
            host_id=plan.intent.target_host_connection_id,
            command=step.command,
            owner_id=plan.intent.owner_id,
            actor=plan.intent.actor,
            user_confirmed=step.step_id in user_confirmed_steps,
            risk_class=step.risk_class,
        )
        ev = await governed_ssh_exec(req, transport=transport)
        results.append(ev)
        if stop_on_failure and ev.status in ("failed", "denied"):
            break
        # Exit code alone is not success for verify steps — caller checks verification
        if stop_on_failure and step.kind == "verify" and ev.exit_status not in (0, None):
            break

    return results
