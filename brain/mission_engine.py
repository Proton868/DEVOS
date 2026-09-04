"""
Autonomous mission helpers over existing orchestration.

Not a second scheduler/executor. Uses:
  DAG readiness, specialty policy, UCIP, Agent Runtime, verification.
"""
from __future__ import annotations

import asyncio
import enum
import logging
import os
import uuid
from dataclasses import dataclass, field
from typing import Optional

from brain.orchestration_dag import (
    OrchestrationNode,
    OrchestrationEdge,
    NodeStatus,
    DepCondition,
    compute_readiness,
    propagate_failure,
    validate_dag,
)
from brain.orchestration_runtime import NodeExecutionRequest, run_node_on_agent_runtime
from brain.specialty_policy import evaluate_node_request
from brain.capability_canon import canonicalize_set
from brain.orchestration_store import persist_plan
from brain.orchestration_verify import verify_workspace_artifacts

logger = logging.getLogger("devos.mission_engine")

# Bounded concurrency — not a custom worker pool
MAX_PARALLEL = int(os.environ.get("DEVOS_ORCH_MAX_PARALLEL", "3"))


class FailureClass(str, enum.Enum):
    TRANSIENT = "transient"
    BUILD_FAILURE = "build_failure"
    TEST_FAILURE = "test_failure"
    CODE_ERROR = "code_error"
    DEPENDENCY_FAILURE = "dependency_failure"
    ENVIRONMENT_FAILURE = "environment_failure"
    TOOL_FAILURE = "tool_failure"
    WORKSPACE_CONFLICT = "workspace_conflict"
    AUTHORIZATION_FAILURE = "authorization_failure"
    MISSING_INPUT = "missing_input"
    MISSING_CREDENTIAL = "missing_credential"
    USER_DECISION_REQUIRED = "user_decision_required"
    EXTERNAL_SIDE_EFFECT = "external_side_effect"
    HITL_REJECTED = "hitl_rejected"
    BUDGET_EXCEEDED = "budget_exceeded"
    EXECUTION_ERROR = "execution_error"
    TIMEOUT = "timeout"
    RESOURCE_UNAVAILABLE = "resource_unavailable"
    PROVIDER_ERROR = "provider_error"
    VERIFICATION_FAILURE = "verification_failure"
    USER_BLOCKED = "user_blocked"
    UNKNOWN = "unknown"


class DecisionType(str, enum.Enum):
    RETRY = "retry"
    REPAIR = "repair"
    REPLAN = "replan"
    ADD_NODE = "add_node"
    MODIFY_NODE = "modify_node"
    REMOVE_NODE = "remove_node"
    WAIT = "wait"
    ASK_USER = "ask_user"
    ABORT = "abort"
    CONTINUE = "continue"
    COMPLETE = "complete"


@dataclass
class ExecutionEvidence:
    """Durable evidence for Nuha diagnosis — no fabricated fields."""
    node_id: str
    task_id: Optional[str] = None
    persona_id: Optional[str] = None
    workspace_id: Optional[str] = None
    status: str = "unknown"
    success: bool = False
    error: Optional[str] = None
    files_changed: list = field(default_factory=list)
    verification: Optional[dict] = None
    authorization_decision: Optional[str] = None
    cancelled: bool = False
    raw_summary: Optional[str] = None
    unavailable: list = field(default_factory=list)  # fields runtime could not provide

    def to_dict(self) -> dict:
        return {
            "node_id": self.node_id,
            "task_id": self.task_id,
            "persona_id": self.persona_id,
            "workspace_id": self.workspace_id,
            "status": self.status,
            "success": self.success,
            "error": self.error,
            "files_changed": list(self.files_changed or []),
            "verification": self.verification,
            "authorization_decision": self.authorization_decision,
            "cancelled": self.cancelled,
            "raw_summary": self.raw_summary,
            "unavailable": list(self.unavailable or []),
        }


@dataclass
class NuhaDecision:
    """Structured recovery decision — LLM never mutates the graph directly."""
    decision_type: str
    reason: str
    evidence_refs: list = field(default_factory=list)
    proposed_changes: list = field(default_factory=list)
    required_capabilities: list = field(default_factory=list)
    risk_class: str = "medium"
    requires_user: bool = False
    failure_class: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "decision_type": self.decision_type,
            "reason": self.reason,
            "evidence_refs": list(self.evidence_refs or []),
            "proposed_changes": list(self.proposed_changes or []),
            "required_capabilities": list(self.required_capabilities or []),
            "risk_class": self.risk_class,
            "requires_user": self.requires_user,
            "failure_class": self.failure_class,
        }


def classify_failure(
    error: Optional[str] = None,
    status: str = "",
    evidence: Optional[dict] = None,
) -> FailureClass:
    e = (error or "").lower()
    s = (status or "").lower()
    ev = evidence or {}
    text = " ".join([
        e, s,
        str(ev.get("error") or ""),
        str(ev.get("raw_summary") or ""),
        str((ev.get("verification") or {}).get("errors") or ""),
    ]).lower()

    if "auth" in text or "denied" in text or "authorization" in text:
        return FailureClass.AUTHORIZATION_FAILURE
    if "hitl" in text or "approval" in text:
        return FailureClass.HITL_REJECTED
    if "credential" in text or "api key" in text or "secret" in text and "missing" in text:
        return FailureClass.MISSING_CREDENTIAL
    if "conflict" in text or "overwrite" in text:
        return FailureClass.WORKSPACE_CONFLICT
    if "timeout" in text or "timed out" in text:
        return FailureClass.TIMEOUT
    if "budget" in text or "max_steps" in text or "exhausted" in text:
        return FailureClass.BUDGET_EXCEEDED
    if "rate limit" in text or "provider" in text:
        return FailureClass.PROVIDER_ERROR
    if "resource" in text or "unavailable" in text:
        return FailureClass.RESOURCE_UNAVAILABLE
    if "npm err" in text or "build failed" in text or "compilation" in text or "cannot find module" in text:
        return FailureClass.BUILD_FAILURE
    if "test failed" in text or "assertion" in text or "jest" in text or "pytest" in text:
        return FailureClass.TEST_FAILURE
    if "syntaxerror" in text or "typeerror" in text or "import error" in text or "nameerror" in text:
        return FailureClass.CODE_ERROR
    if "enoent" in text or "module not found" in text or "dependency" in text:
        return FailureClass.DEPENDENCY_FAILURE
    if "permission denied" in text or "eacces" in text:
        return FailureClass.ENVIRONMENT_FAILURE
    if "deploy" in text or "production" in text:
        return FailureClass.EXTERNAL_SIDE_EFFECT
    if "verif" in text or (ev.get("verification") and not (ev.get("verification") or {}).get("passed")):
        return FailureClass.VERIFICATION_FAILURE
    if "missing" in text and ("input" in text or "file" in text):
        return FailureClass.MISSING_INPUT
    if "transient" in text or "retry" in text or "503" in text or "temporarily" in text:
        return FailureClass.TRANSIENT
    if s in ("failed", "error"):
        return FailureClass.EXECUTION_ERROR
    return FailureClass.UNKNOWN


def diagnose_evidence(evidence: ExecutionEvidence) -> FailureClass:
    return classify_failure(
        error=evidence.error,
        status=evidence.status,
        evidence=evidence.to_dict(),
    )


def decide_recovery(
    evidence: ExecutionEvidence,
    attempt_count: int = 0,
    max_attempts: int = 2,
) -> NuhaDecision:
    """
    Typed recovery decision from evidence. Does not mutate the graph.
    Graph changes happen only via apply_revision / repair after this decision.
    """
    fc = diagnose_evidence(evidence)
    refs = [evidence.node_id]
    if evidence.task_id:
        refs.append(f"task:{evidence.task_id}")

    if evidence.cancelled:
        return NuhaDecision(
            decision_type=DecisionType.ABORT.value,
            reason="node cancelled",
            evidence_refs=refs,
            requires_user=False,
            failure_class=fc.value,
        )

    if fc in (
        FailureClass.AUTHORIZATION_FAILURE,
        FailureClass.HITL_REJECTED,
        FailureClass.MISSING_CREDENTIAL,
        FailureClass.USER_DECISION_REQUIRED,
        FailureClass.EXTERNAL_SIDE_EFFECT,
        FailureClass.USER_BLOCKED,
    ):
        return NuhaDecision(
            decision_type=DecisionType.ASK_USER.value,
            reason=f"requires human: {fc.value}",
            evidence_refs=refs,
            requires_user=True,
            failure_class=fc.value,
            risk_class="high",
        )

    if fc == FailureClass.WORKSPACE_CONFLICT:
        return NuhaDecision(
            decision_type=DecisionType.WAIT.value if attempt_count < 1 else DecisionType.ASK_USER.value,
            reason="workspace write conflict",
            evidence_refs=refs,
            requires_user=attempt_count >= 1,
            failure_class=fc.value,
        )

    if fc == FailureClass.BUDGET_EXCEEDED:
        return NuhaDecision(
            decision_type=DecisionType.ASK_USER.value,
            reason="budget exceeded",
            evidence_refs=refs,
            requires_user=True,
            failure_class=fc.value,
        )

    if attempt_count >= max_attempts:
        return NuhaDecision(
            decision_type=DecisionType.ASK_USER.value,
            reason=f"bounded attempts exhausted ({attempt_count})",
            evidence_refs=refs,
            requires_user=True,
            failure_class=fc.value,
        )

    if fc == FailureClass.TRANSIENT:
        return NuhaDecision(
            decision_type=DecisionType.RETRY.value,
            reason="transient failure — bounded retry",
            evidence_refs=refs,
            failure_class=fc.value,
        )

    if fc in (
        FailureClass.BUILD_FAILURE,
        FailureClass.CODE_ERROR,
        FailureClass.TEST_FAILURE,
        FailureClass.DEPENDENCY_FAILURE,
        FailureClass.VERIFICATION_FAILURE,
    ):
        return NuhaDecision(
            decision_type=DecisionType.REPAIR.value,
            reason=f"evidence indicates {fc.value} — inject repair nodes",
            evidence_refs=refs,
            proposed_changes=[{"action": "repair_node", "target": evidence.node_id}],
            required_capabilities=list(
                canonicalize_set(["fs.read", "fs.write", "shell.exec"])
            ),
            failure_class=fc.value,
        )

    if fc in (FailureClass.PROVIDER_ERROR, FailureClass.TIMEOUT, FailureClass.RESOURCE_UNAVAILABLE):
        return NuhaDecision(
            decision_type=DecisionType.RETRY.value if attempt_count < max_attempts else DecisionType.ASK_USER.value,
            reason=f"{fc.value}",
            evidence_refs=refs,
            requires_user=attempt_count >= max_attempts,
            failure_class=fc.value,
        )

    # default: replan strategy once, then ask
    if attempt_count < 1:
        return NuhaDecision(
            decision_type=DecisionType.REPLAN.value,
            reason="unknown/execution failure — replan approach",
            evidence_refs=refs,
            proposed_changes=[{"action": "replace_strategy", "target": evidence.node_id}],
            failure_class=fc.value,
        )
    return NuhaDecision(
        decision_type=DecisionType.ASK_USER.value,
        reason="recovery exhausted",
        evidence_refs=refs,
        requires_user=True,
        failure_class=fc.value,
    )


def get_ready_nodes(nodes: list, edges: list) -> list:
    """All nodes currently eligible (deps satisfied, not executing/terminal)."""
    ready_ids = set(compute_readiness(nodes, edges))
    out = []
    for n in nodes:
        if n.id not in ready_ids:
            continue
        st = (n.status or "").lower()
        if st in (
            "running", "queued", "completed", "verified", "cancelled",
            "blocked", "failed", "verifying", "authorized",
        ):
            # already in flight or done — skip unless pending/ready/blocked_by_dependency
            if st not in ("pending", "ready", "blocked_by_dependency", "replanning"):
                continue
        out.append(n)
    return out


def path_conflict(paths_a: list, paths_b: list) -> bool:
    """True if two path sets may write the same file."""
    def norm(p):
        return (p or "").replace("\\", "/").lstrip("./").lower()
    sa = {norm(p) for p in paths_a if p}
    sb = {norm(p) for p in paths_b if p}
    if not sa or not sb:
        return False
    if sa & sb:
        return True
    # prefix conflict: a is under b or vice versa for directories
    for a in sa:
        for b in sb:
            if a and b and (a.startswith(b + "/") or b.startswith(a + "/")):
                return True
    return False


def partition_ready_for_parallel(ready: list) -> list[list]:
    """
    Group ready nodes so concurrent batches have no write-path conflicts.
    Returns ordered batches (each batch can run in parallel).
    """
    remaining = list(ready)
    batches: list[list] = []
    while remaining:
        batch = []
        paths_in_batch: list = []
        still = []
        for n in remaining:
            paths = list(n.expected_outputs or []) + list(getattr(n, "write_paths", None) or [])
            conflict = any(path_conflict(paths, paths_in_batch) for _ in [0] if paths_in_batch)
            # check pairwise with current batch members
            ok = True
            for m in batch:
                mp = list(m.expected_outputs or [])
                if path_conflict(paths, mp):
                    ok = False
                    break
            if ok:
                batch.append(n)
                paths_in_batch.extend(paths)
            else:
                still.append(n)
        if not batch:
            # force progress
            batch = [remaining[0]]
            still = remaining[1:]
        batches.append(batch)
        remaining = still
    return batches


@dataclass
class PlanRevision:
    revision: int
    parent_plan_id: Optional[str]
    reason: str
    preserved_verified: list[str] = field(default_factory=list)
    new_node_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "revision": self.revision,
            "parent_plan_id": self.parent_plan_id,
            "reason": self.reason,
            "preserved_verified": list(self.preserved_verified),
            "new_node_ids": list(self.new_node_ids),
        }


def create_repair_nodes(
    plan,
    failed_node: OrchestrationNode,
    failure_class: FailureClass,
) -> list[OrchestrationNode]:
    """Nuha-style repair: inject repair node that depends on nothing new; rebuild chain."""
    rid = f"repair-{failed_node.id}-{uuid.uuid4().hex[:6]}"
    repair = OrchestrationNode(
        id=rid,
        description=f"Repair after {failure_class.value}: {failed_node.description}",
        persona_id=failed_node.persona_id or "code",
        dependencies=[],
        capabilities=list(failed_node.capabilities or ["fs.read", "fs.write"]),
        expected_outputs=list(failed_node.expected_outputs or []),
        verification_criteria=list(failed_node.verification_criteria or ["repair verified"]),
        risk=failed_node.risk or "medium",
        status=NodeStatus.PENDING.value,
    )
    # rebuild / re-verify after repair
    rebuild_id = f"rebuild-{failed_node.id}-{uuid.uuid4().hex[:6]}"
    rebuild = OrchestrationNode(
        id=rebuild_id,
        description=f"Rebuild/re-verify after repair of {failed_node.id}",
        persona_id=failed_node.persona_id or "code",
        dependencies=[rid],
        capabilities=list(failed_node.capabilities or ["fs.read", "shell.exec"]),
        expected_outputs=list(failed_node.expected_outputs or []),
        verification_criteria=["build or artifact verification passes"],
        risk=failed_node.risk or "medium",
        status=NodeStatus.PENDING.value,
    )
    return [repair, rebuild]


def apply_revision(plan, failed_node: OrchestrationNode, failure_class: FailureClass) -> PlanRevision:
    """
    Dynamic replan: keep verified nodes, inject repair nodes, re-validate DAG.
    Does not inherit prior authorization for new nodes.
    """
    rev_n = int(getattr(plan, "revision", 1) or 1) + 1
    preserved = [
        n.id for n in plan.nodes
        if (n.status or "").lower() in ("verified", "completed")
    ]
    repairs = create_repair_nodes(plan, failed_node, failure_class)
    for r in repairs:
        plan.nodes.append(r)
    # edges: repair is root-ish; rebuild depends on repair
    plan.edges.append(OrchestrationEdge(
        source=repairs[0].id, target=repairs[1].id, condition=DepCondition.VERIFIED.value,
    ))
    # dependents of failed_node should wait on rebuild
    for e in list(plan.edges):
        if e.source == failed_node.id:
            plan.edges.append(OrchestrationEdge(
                source=repairs[1].id, target=e.target, condition=e.condition,
            ))
    issues = validate_dag(plan.nodes, plan.edges)
    plan.dag_issues = issues
    plan.dag_valid = len(issues) == 0
    plan.revision = rev_n
    if not hasattr(plan, "revisions") or plan.revisions is None:
        plan.revisions = []
    rev = PlanRevision(
        revision=rev_n,
        parent_plan_id=plan.id,
        reason=f"{failure_class.value}:{failed_node.id}",
        preserved_verified=preserved,
        new_node_ids=[r.id for r in repairs],
    )
    plan.revisions.append(rev.to_dict())
    plan.emit("plan.revision", rev.to_dict())
    return rev


async def dispatch_node(plan, node: OrchestrationNode) -> dict:
    """Authorize (specialty) + run one node via existing Agent Runtime adapter."""
    from brain.orchestration import RiskLevel  # local

    decision = evaluate_node_request(
        persona_id=node.persona_id,
        requested_caps=set(node.capabilities or []),
        risk=getattr(plan, "risk_level", None),
    )
    plan.emit("ucip.policy_eval", {"node_id": node.id, "decision": decision.to_dict()})
    if not decision.allow:
        node.status = NodeStatus.BLOCKED.value
        node.blocking_reason = "; ".join(decision.reasons)
        node.authorization_decision = "deny"
        return {"node_id": node.id, "success": False, "failure_class": FailureClass.AUTHORIZATION_FAILURE.value}

    if decision.hitl_required and getattr(plan, "risk_level", "") in ("critical", RiskLevel.CRITICAL.value if hasattr(RiskLevel, "CRITICAL") else "critical"):
        node.status = NodeStatus.AWAITING_APPROVAL.value
        plan.status = "waiting_for_user"
        plan.emit("hitl.requested", {"node_id": node.id})
        return {"node_id": node.id, "success": False, "failure_class": FailureClass.HITL_REJECTED.value, "awaiting_user": True}

    node.authorization_decision = "allow"
    node.capabilities = list(canonicalize_set(decision.effective_caps)) or list(node.capabilities or [])
    try:
        node.set_status(NodeStatus.AUTHORIZED)
        node.set_status(NodeStatus.QUEUED)
        node.set_status(NodeStatus.RUNNING)
    except Exception:
        node.status = NodeStatus.RUNNING.value

    req = NodeExecutionRequest(
        plan_id=plan.id,
        node_id=node.id,
        user_id=plan.user_id,
        workspace_id=plan.workspace_id or "default",
        persona_id=node.persona_id,
        objective=f"Goal: {plan.goal}\nStep: {node.description}",
        effective_caps=list(node.capabilities or []),
        authorization_decision="allow",
    )
    result = await run_node_on_agent_runtime(req)
    if result.task_id:
        node.job_or_task_id = result.task_id
        if result.task_id not in plan.agent_task_ids:
            plan.agent_task_ids.append(result.task_id)

    if result.status == "cancelled":
        node.status = NodeStatus.CANCELLED.value
        return {"node_id": node.id, "success": False, "cancelled": True}

    if not result.success:
        node.status = NodeStatus.FAILED.value
        fc = classify_failure(result.error, result.status)
        return {
            "node_id": node.id,
            "success": False,
            "failure_class": fc.value,
            "error": result.error,
            "result": result.to_dict(),
        }

    # Verify this node
    node.status = NodeStatus.VERIFYING.value
    evidence = await verify_workspace_artifacts(
        user_id=plan.user_id,
        workspace_id=plan.workspace_id or "default",
        goal=plan.goal,
        expected_outputs=list(node.expected_outputs or []),
        files_changed=list(result.files_changed or []),
    )
    node.verification_evidence = evidence
    if evidence.get("passed"):
        try:
            node.set_status(NodeStatus.VERIFIED)
            node.set_status(NodeStatus.COMPLETED)
        except Exception:
            node.status = NodeStatus.COMPLETED.value
        return {"node_id": node.id, "success": True, "result": result.to_dict(), "evidence": evidence}

    node.status = NodeStatus.FAILED.value
    return {
        "node_id": node.id,
        "success": False,
        "failure_class": FailureClass.VERIFICATION_FAILURE.value,
        "evidence": evidence,
        "result": result.to_dict(),
    }


async def run_mission_parallel(plan, max_parallel: Optional[int] = None) -> object:
    """
    Parallel-ready mission loop using existing Agent Runtime.
    Sequential batches of non-conflicting ready nodes.
    """
    limit = max_parallel or MAX_PARALLEL
    attempts: dict[str, int] = {}
    max_attempts = 2

    while True:
        if (plan.status or "").lower() in ("cancellation_requested", "cancelling", "cancelled"):
            try:
                from execution.cancel_cascade import cascade_cancel_plan
                evidence = await cascade_cancel_plan(plan)
                plan.emit("orchestration.cancelled", evidence)
            except Exception as e:
                for n in plan.nodes:
                    if (n.status or "").lower() in ("running", "queued", "ready"):
                        n.status = NodeStatus.CANCELLED.value
                for tid in list(plan.agent_task_ids or []):
                    try:
                        from brain.agent_runtime import request_cancel
                        request_cancel(tid)
                    except Exception:
                        pass
                plan.emit("orchestration.cancelled", {"error": str(e)[:200]})
            plan.status = "cancelled"
            await persist_plan(plan)
            return plan

        ready = get_ready_nodes(plan.nodes, plan.edges)
        plan.emit("dag.readiness", {"ready": [n.id for n in ready]})

        if not ready:
            # terminal?
            statuses = [(n.status or "").lower() for n in plan.nodes]
            if any(s in ("failed", "blocked") for s in statuses):
                failed = next(
                    (n for n in plan.nodes if (n.status or "").lower() == "failed"),
                    None,
                )
                if failed:
                    ev = ExecutionEvidence(
                        node_id=failed.id,
                        task_id=failed.job_or_task_id,
                        persona_id=failed.persona_id,
                        workspace_id=plan.workspace_id,
                        status=failed.status,
                        success=False,
                        error=failed.blocking_reason,
                        verification=failed.verification_evidence,
                        authorization_decision=failed.authorization_decision,
                    )
                    decision = decide_recovery(ev, attempts.get(failed.id, 0), max_attempts)
                    plan.emit("nuha.decision", decision.to_dict())
                    if decision.requires_user or decision.decision_type == DecisionType.ASK_USER.value:
                        plan.status = "waiting_for_user"
                        await persist_plan(plan)
                        return plan
                    if decision.decision_type in (DecisionType.REPAIR.value, DecisionType.REPLAN.value):
                        attempts[failed.id] = attempts.get(failed.id, 0) + 1
                        plan.status = "replanning"
                        apply_revision(plan, failed, FailureClass(decision.failure_class or "unknown"))
                        await persist_plan(plan)
                        continue
                plan.status = "failed"
                await persist_plan(plan)
                return plan
            if all(s in ("completed", "verified", "cancelled") for s in statuses if s):
                plan.status = "completed"
                plan.emit("orchestration.completed", {"plan_id": plan.id})
                await persist_plan(plan)
                return plan
            # blocked waiting
            if any(s in ("awaiting_approval",) for s in statuses):
                plan.status = "waiting_for_user"
                await persist_plan(plan)
                return plan
            plan.status = "failed"
            plan.emit("job.failed", {"reason": "no ready nodes"})
            await persist_plan(plan)
            return plan

        batches = partition_ready_for_parallel(ready)
        batch = batches[0][:limit]
        plan.status = "running"
        plan.emit("mission.batch", {"nodes": [n.id for n in batch]})

        results = await asyncio.gather(
            *[dispatch_node(plan, n) for n in batch],
            return_exceptions=True,
        )
        await persist_plan(plan)

        for r in results:
            if isinstance(r, Exception):
                plan.emit("job.failed", {"error": str(r)[:300]})
                continue
            if r.get("awaiting_user"):
                plan.status = "waiting_for_user"
                await persist_plan(plan)
                return plan
            if r.get("cancelled"):
                plan.status = "cancelled"
                await persist_plan(plan)
                return plan
            if not r.get("success"):
                node = next((n for n in plan.nodes if n.id == r.get("node_id")), None)
                if node:
                    propagate_failure(plan.nodes, plan.edges, node.id)
                    # Evidence-driven decision (does not mutate graph itself)
                    ev = ExecutionEvidence(
                        node_id=node.id,
                        task_id=node.job_or_task_id,
                        persona_id=node.persona_id,
                        workspace_id=plan.workspace_id,
                        status=r.get("failure_class") or node.status,
                        success=False,
                        error=r.get("error"),
                        files_changed=list((r.get("result") or {}).get("files_changed") or []),
                        verification=r.get("evidence") or node.verification_evidence,
                        authorization_decision=node.authorization_decision,
                        cancelled=bool(r.get("cancelled")),
                        raw_summary=str((r.get("result") or {}).get("summary") or "")[:2000] or None,
                    )
                    decision = decide_recovery(
                        ev,
                        attempt_count=attempts.get(node.id, 0),
                        max_attempts=max_attempts,
                    )
                    plan.emit("nuha.decision", decision.to_dict())
                    # Store evidence on plan events for audit/restart
                    plan.emit("evidence.recorded", ev.to_dict())
                    if not hasattr(plan, "evidence_log"):
                        plan.evidence_log = []
                    plan.evidence_log.append(ev.to_dict())

                    if decision.decision_type == DecisionType.ASK_USER.value or decision.requires_user:
                        plan.status = "waiting_for_user"
                        plan.emit("mission.awaiting_user", {
                            "reason": decision.reason,
                            "node_id": node.id,
                            "failure_class": decision.failure_class,
                        })
                        await persist_plan(plan)
                        return plan
                    if decision.decision_type == DecisionType.ABORT.value:
                        plan.status = "cancelled"
                        await persist_plan(plan)
                        return plan
                    if decision.decision_type in (
                        DecisionType.REPAIR.value, DecisionType.REPLAN.value, DecisionType.ADD_NODE.value,
                    ):
                        attempts[node.id] = attempts.get(node.id, 0) + 1
                        plan.status = "recovering"
                        plan.emit("recovery.started", {"node_id": node.id, "decision": decision.to_dict()})
                        plan.status = "replanning"
                        fc = FailureClass(decision.failure_class or FailureClass.UNKNOWN.value)
                        apply_revision(plan, node, fc)
                        await persist_plan(plan)
                    elif decision.decision_type == DecisionType.RETRY.value:
                        attempts[node.id] = attempts.get(node.id, 0) + 1
                        node.status = NodeStatus.PENDING.value
                        plan.emit("recovery.retry", {"node_id": node.id, "attempt": attempts[node.id]})

        # XP only on verified success — existing path after full mission complete
        # loop continues until no ready / terminal

    return plan
