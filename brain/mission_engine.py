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
    AUTHORIZATION_FAILURE = "authorization_failure"
    HITL_REJECTED = "hitl_rejected"
    WORKSPACE_CONFLICT = "workspace_conflict"
    EXECUTION_ERROR = "execution_error"
    TIMEOUT = "timeout"
    RESOURCE_UNAVAILABLE = "resource_unavailable"
    PROVIDER_ERROR = "provider_error"
    VERIFICATION_FAILURE = "verification_failure"
    USER_BLOCKED = "user_blocked"
    BUDGET_EXCEEDED = "budget_exceeded"
    UNKNOWN = "unknown"


def classify_failure(error: Optional[str], status: str = "") -> FailureClass:
    e = (error or "").lower()
    s = (status or "").lower()
    if "auth" in e or "denied" in e or "authorization" in e:
        return FailureClass.AUTHORIZATION_FAILURE
    if "hitl" in e or "approval" in e:
        return FailureClass.HITL_REJECTED
    if "conflict" in e or "overwrite" in e:
        return FailureClass.WORKSPACE_CONFLICT
    if "timeout" in e or "timed out" in e:
        return FailureClass.TIMEOUT
    if "budget" in e or "max_steps" in e or "exhausted" in e:
        return FailureClass.BUDGET_EXCEEDED
    if "provider" in e or "api key" in e or "rate limit" in e:
        return FailureClass.PROVIDER_ERROR
    if "resource" in e or "unavailable" in e:
        return FailureClass.RESOURCE_UNAVAILABLE
    if "verif" in e:
        return FailureClass.VERIFICATION_FAILURE
    if s in ("failed", "error"):
        return FailureClass.EXECUTION_ERROR
    return FailureClass.UNKNOWN


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
        if (plan.status or "").lower() in ("cancellation_requested", "cancelling"):
            # cancel outstanding
            for n in plan.nodes:
                if (n.status or "").lower() in ("running", "queued", "ready"):
                    n.status = NodeStatus.CANCELLED.value
            for tid in list(plan.agent_task_ids or []):
                try:
                    from brain.agent_runtime import request_cancel
                    request_cancel(tid)
                except Exception:
                    pass
            plan.status = "cancelled"
            plan.emit("orchestration.cancelled", {})
            await persist_plan(plan)
            return plan

        ready = get_ready_nodes(plan.nodes, plan.edges)
        plan.emit("dag.readiness", {"ready": [n.id for n in ready]})

        if not ready:
            # terminal?
            statuses = [(n.status or "").lower() for n in plan.nodes]
            if any(s in ("failed", "blocked") for s in statuses):
                # try replan on first failed without repair yet
                failed = next(
                    (n for n in plan.nodes if (n.status or "").lower() == "failed"),
                    None,
                )
                if failed and attempts.get(failed.id, 0) < max_attempts:
                    attempts[failed.id] = attempts.get(failed.id, 0) + 1
                    fc = classify_failure(
                        (failed.blocking_reason or ""),
                        failed.status,
                    )
                    plan.status = "recovering"
                    plan.emit("recovery.started", {"node_id": failed.id, "class": fc.value})
                    plan.status = "replanning"
                    apply_revision(plan, failed, fc)
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
                    # failure handled on next loop via replan

        # XP only on verified success — existing path after full mission complete
        # loop continues until no ready / terminal

    return plan
