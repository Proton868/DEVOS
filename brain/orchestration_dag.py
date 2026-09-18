"""
Formal DAG model for Nuha orchestration plans.

The DAG decides WHAT DEPENDS ON WHAT.
UCIP decides WHETHER it is permitted.
Jobs / Agent Runtime perform work.
Verification decides WHETHER IT WORKED.

Not a parallel scheduler — readiness + sequential dependency-safe execution.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Optional


class DepCondition(str, enum.Enum):
    """Future-proof dependency satisfaction conditions."""
    COMPLETED = "completed"
    VERIFIED = "verified"
    APPROVED = "approved"          # reserved
    ARTIFACT_CREATED = "artifact_created"  # reserved
    FAILED = "failed"              # reserved (for recovery edges)


class NodeStatus(str, enum.Enum):
    PENDING = "pending"
    BLOCKED_BY_DEPENDENCY = "blocked_by_dependency"
    READY = "ready"
    AUTHORIZATION_PENDING = "authorization_pending"
    AWAITING_APPROVAL = "awaiting_approval"
    AUTHORIZED = "authorized"
    QUEUED = "queued"
    RUNNING = "running"
    VERIFYING = "verifying"
    VERIFIED = "verified"
    FAILED = "failed"
    RECOVERING = "recovering"
    REPLANNING = "replanning"
    CANCELLATION_REQUESTED = "cancellation_requested"
    CANCELLING = "cancelling"
    CANCELLED = "cancelled"
    BLOCKED = "blocked"
    COMPLETED = "completed"


_NODE_TRANSITIONS: dict[NodeStatus, set[NodeStatus]] = {
    NodeStatus.PENDING: {NodeStatus.BLOCKED_BY_DEPENDENCY, NodeStatus.READY, NodeStatus.CANCELLED},
    NodeStatus.BLOCKED_BY_DEPENDENCY: {NodeStatus.READY, NodeStatus.CANCELLED, NodeStatus.BLOCKED},
    NodeStatus.READY: {
        NodeStatus.AUTHORIZATION_PENDING, NodeStatus.CANCELLED, NodeStatus.BLOCKED_BY_DEPENDENCY,
    },
    NodeStatus.AUTHORIZATION_PENDING: {
        NodeStatus.AUTHORIZED, NodeStatus.AWAITING_APPROVAL, NodeStatus.BLOCKED, NodeStatus.CANCELLED,
    },
    NodeStatus.AWAITING_APPROVAL: {
        NodeStatus.AUTHORIZED, NodeStatus.BLOCKED, NodeStatus.CANCELLED,
    },
    NodeStatus.AUTHORIZED: {NodeStatus.QUEUED, NodeStatus.CANCELLED},
    NodeStatus.QUEUED: {NodeStatus.RUNNING, NodeStatus.CANCELLATION_REQUESTED, NodeStatus.CANCELLED},
    NodeStatus.RUNNING: {
        NodeStatus.VERIFYING, NodeStatus.FAILED, NodeStatus.CANCELLATION_REQUESTED,
    },
    NodeStatus.VERIFYING: {
        NodeStatus.VERIFIED, NodeStatus.FAILED, NodeStatus.CANCELLATION_REQUESTED,
    },
    NodeStatus.VERIFIED: {NodeStatus.COMPLETED},
    NodeStatus.COMPLETED: set(),
    NodeStatus.FAILED: {NodeStatus.RECOVERING, NodeStatus.CANCELLED, NodeStatus.BLOCKED},
    NodeStatus.RECOVERING: {NodeStatus.REPLANNING, NodeStatus.FAILED, NodeStatus.CANCELLED},
    NodeStatus.REPLANNING: {NodeStatus.READY, NodeStatus.PENDING, NodeStatus.CANCELLED},
    NodeStatus.CANCELLATION_REQUESTED: {NodeStatus.CANCELLING},
    NodeStatus.CANCELLING: {NodeStatus.CANCELLED},
    NodeStatus.CANCELLED: set(),
    NodeStatus.BLOCKED: set(),
}


def can_node_transition(frm: NodeStatus, to: NodeStatus) -> bool:
    return to in _NODE_TRANSITIONS.get(frm, set())


def transition_node(frm: NodeStatus, to: NodeStatus) -> NodeStatus:
    if not can_node_transition(frm, to):
        raise ValueError(f"invalid node transition: {frm.value} → {to.value}")
    return to


@dataclass
class OrchestrationEdge:
    source: str
    target: str
    condition: str = DepCondition.VERIFIED.value

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "target": self.target,
            "condition": self.condition,
        }


@dataclass
class OrchestrationNode:
    id: str
    description: str
    persona_id: str = "code"
    dependencies: list[str] = field(default_factory=list)  # convenience; edges are authoritative
    capabilities: list[str] = field(default_factory=list)
    workspace_scope: str = "project"
    expected_outputs: list[str] = field(default_factory=list)
    verification_criteria: list[str] = field(default_factory=list)
    risk: str = "low"
    status: str = NodeStatus.PENDING.value
    job_or_task_id: Optional[str] = None
    operation_id: Optional[str] = None  # durable ExecutionOperation linkage when known
    authorization_decision: Optional[str] = None
    blocking_reason: Optional[str] = None
    verification_evidence: Optional[dict] = None
    # Recovery plan metadata — never treated as verification evidence.
    recovery_metadata: Optional[dict] = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "description": self.description,
            "persona_id": self.persona_id,
            "dependencies": list(self.dependencies),
            "capabilities": list(self.capabilities),
            "workspace_scope": self.workspace_scope,
            "expected_outputs": list(self.expected_outputs),
            "verification_criteria": list(self.verification_criteria),
            "risk": self.risk,
            "status": self.status,
            "job_or_task_id": self.job_or_task_id,
            "operation_id": self.operation_id,
            "authorization_decision": self.authorization_decision,
            "blocking_reason": self.blocking_reason,
            "verification_evidence": self.verification_evidence,
            "recovery_metadata": self.recovery_metadata,
        }

    def set_status(self, new: NodeStatus) -> None:
        cur = NodeStatus(self.status)
        self.status = transition_node(cur, new).value


class DAGValidationError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def validate_dag(
    nodes: list[OrchestrationNode],
    edges: list[OrchestrationEdge],
) -> list[str]:
    """Return list of issues. Empty => valid. Raises on hard cycle if raise_on_cycle."""
    issues: list[str] = []
    ids = [n.id for n in nodes]
    id_set = set(ids)
    if len(ids) != len(id_set):
        issues.append("duplicate_node_id")
    if not nodes:
        issues.append("empty_graph")
        return issues

    for n in nodes:
        if not n.persona_id:
            issues.append(f"node:{n.id}:missing_persona")
        if not n.capabilities:
            issues.append(f"node:{n.id}:missing_capabilities")

    for e in edges:
        if e.source not in id_set:
            issues.append(f"edge:missing_source:{e.source}")
        if e.target not in id_set:
            issues.append(f"edge:missing_target:{e.target}")
        if e.source == e.target:
            issues.append(f"edge:self_dependency:{e.source}")

    # Dependencies listed on nodes must exist
    for n in nodes:
        for d in n.dependencies:
            if d not in id_set:
                issues.append(f"node:{n.id}:missing_dependency:{d}")
            if d == n.id:
                issues.append(f"node:{n.id}:self_dependency")

    # Build adjacency from edges + node.dependencies
    adj: dict[str, list[str]] = {i: [] for i in id_set}
    for e in edges:
        if e.source in id_set and e.target in id_set:
            adj[e.source].append(e.target)
    for n in nodes:
        for d in n.dependencies:
            if d in id_set:
                adj[d].append(n.id)

    # Cycle detection (DFS)
    visiting, visited = set(), set()

    def visit(u: str, stack: list[str]) -> bool:
        if u in visited:
            return False
        if u in visiting:
            issues.append(f"cycle:{'->'.join(stack + [u])}")
            return True
        visiting.add(u)
        for v in adj.get(u, []):
            if visit(v, stack + [u]):
                return True
        visiting.discard(u)
        visited.add(u)
        return False

    for nid in id_set:
        if visit(nid, []):
            break

    # Root = no incoming; terminal = no outgoing
    incoming = {i: 0 for i in id_set}
    for src, targets in adj.items():
        for t in targets:
            incoming[t] = incoming.get(t, 0) + 1
    roots = [i for i, c in incoming.items() if c == 0]
    terminals = [i for i in id_set if not adj.get(i)]
    if not roots:
        issues.append("no_root_node")
    if not terminals:
        issues.append("no_terminal_node")

    return issues


def assert_valid_dag(nodes: list[OrchestrationNode], edges: list[OrchestrationEdge]) -> None:
    issues = validate_dag(nodes, edges)
    if issues:
        raise DAGValidationError("PLAN_INVALID", "; ".join(issues))


def _dep_satisfied(node: OrchestrationNode, condition: str) -> bool:
    st = NodeStatus(node.status)
    if condition == DepCondition.VERIFIED.value:
        return st in (NodeStatus.VERIFIED, NodeStatus.COMPLETED)
    if condition == DepCondition.COMPLETED.value:
        return st in (NodeStatus.COMPLETED, NodeStatus.VERIFIED)
    if condition == DepCondition.APPROVED.value:
        return st in (NodeStatus.AUTHORIZED, NodeStatus.QUEUED, NodeStatus.RUNNING,
                      NodeStatus.VERIFYING, NodeStatus.VERIFIED, NodeStatus.COMPLETED)
    if condition == DepCondition.FAILED.value:
        # Explicit recovery/failure edges only — parent must be FAILED (not recovering).
        return st == NodeStatus.FAILED
    return st in (NodeStatus.VERIFIED, NodeStatus.COMPLETED)


def _incoming_map(
    nodes: list[OrchestrationNode],
    edges: list[OrchestrationEdge],
) -> dict[str, list[tuple[str, str]]]:
    """Build target -> [(source, condition), ...] from edges and node.dependencies."""
    incoming: dict[str, list[tuple[str, str]]] = {n.id: [] for n in nodes}
    for e in edges:
        if e.target in incoming:
            pair = (e.source, e.condition)
            if pair not in incoming[e.target]:
                incoming[e.target].append(pair)
    for n in nodes:
        for d in n.dependencies:
            pair = (d, DepCondition.VERIFIED.value)
            if pair not in incoming.setdefault(n.id, []):
                incoming[n.id].append(pair)
    return incoming


def _children_map(
    nodes: list[OrchestrationNode],
    edges: list[OrchestrationEdge],
) -> dict[str, list[tuple[str, str]]]:
    """Build source -> [(target, condition), ...] (deduped)."""
    children: dict[str, list[tuple[str, str]]] = {n.id: [] for n in nodes}
    for e in edges:
        lst = children.setdefault(e.source, [])
        pair = (e.target, e.condition)
        if pair not in lst:
            lst.append(pair)
    for n in nodes:
        for d in n.dependencies:
            lst = children.setdefault(d, [])
            pair = (n.id, DepCondition.VERIFIED.value)
            if pair not in lst:
                lst.append(pair)
    return children


def has_recovery_path(
    nodes: list[OrchestrationNode],
    edges: list[OrchestrationEdge],
    node_id: str,
) -> bool:
    """True when an explicit FAILED-condition recovery edge leaves this node.

    Recovery topology is declared in the DAG (FAILED edges). Presence of such
    an edge means Nuha may drive RECOVERING/REPLANNING; the DAG does not
    authorize or execute recovery work itself.
    """
    children = _children_map(nodes, edges)
    for _tid, cond in children.get(node_id, []):
        if cond == DepCondition.FAILED.value:
            return True
    return False


def recovery_eligible_targets(
    nodes: list[OrchestrationNode],
    edges: list[OrchestrationEdge],
    failed_id: str,
) -> list[str]:
    """Node ids reachable via explicit FAILED-condition edges from failed_id."""
    return [
        tid for tid, cond in _children_map(nodes, edges).get(failed_id, [])
        if cond == DepCondition.FAILED.value
    ]


def _get_node(nodes: list[OrchestrationNode], node_id: str) -> OrchestrationNode:
    for n in nodes:
        if n.id == node_id:
            return n
    raise KeyError(f"node not found: {node_id}")


def begin_node_recovery(nodes: list[OrchestrationNode], node_id: str) -> dict:
    """FAILED → RECOVERING. Preserves node identity and job/task correlation.

    Idempotent: if already RECOVERING, reports transitioned=False.
    Returns {node_id, from, to, transitioned, job_or_task_id}.
    """
    node = _get_node(nodes, node_id)
    cur = NodeStatus(node.status)
    job_id = node.job_or_task_id
    if cur == NodeStatus.RECOVERING:
        return {
            "node_id": node_id,
            "from": cur.value,
            "to": cur.value,
            "transitioned": False,
            "job_or_task_id": job_id,
        }
    if cur != NodeStatus.FAILED:
        raise ValueError(f"begin_node_recovery requires FAILED, got {cur.value}")
    node.set_status(NodeStatus.RECOVERING)
    node.blocking_reason = None
    # Preserve job_or_task_id and verification_evidence (do not clear).
    assert node.job_or_task_id == job_id
    return {
        "node_id": node_id,
        "from": NodeStatus.FAILED.value,
        "to": NodeStatus.RECOVERING.value,
        "transitioned": True,
        "job_or_task_id": node.job_or_task_id,
    }


def begin_node_replanning(nodes: list[OrchestrationNode], node_id: str) -> dict:
    """RECOVERING → REPLANNING. Idempotent if already REPLANNING."""
    node = _get_node(nodes, node_id)
    cur = NodeStatus(node.status)
    job_id = node.job_or_task_id
    if cur == NodeStatus.REPLANNING:
        return {
            "node_id": node_id,
            "from": cur.value,
            "to": cur.value,
            "transitioned": False,
            "job_or_task_id": job_id,
        }
    if cur != NodeStatus.RECOVERING:
        raise ValueError(f"begin_node_replanning requires RECOVERING, got {cur.value}")
    node.set_status(NodeStatus.REPLANNING)
    assert node.job_or_task_id == job_id
    return {
        "node_id": node_id,
        "from": NodeStatus.RECOVERING.value,
        "to": NodeStatus.REPLANNING.value,
        "transitioned": True,
        "job_or_task_id": node.job_or_task_id,
    }


def apply_recovery_success(
    nodes: list[OrchestrationNode],
    node_id: str,
    recovery_plan: Optional[dict] = None,
) -> dict:
    """REPLANNING → READY. Stores recovery_plan in recovery_metadata only.

    MUST NOT mark VERIFIED — recovery success means eligible for re-execution.
    """
    node = _get_node(nodes, node_id)
    cur = NodeStatus(node.status)
    job_id = node.job_or_task_id
    if cur == NodeStatus.READY and node.recovery_metadata is not None:
        return {
            "node_id": node_id,
            "from": cur.value,
            "to": cur.value,
            "transitioned": False,
            "job_or_task_id": job_id,
            "status": cur.value,
        }
    if cur != NodeStatus.REPLANNING:
        raise ValueError(f"apply_recovery_success requires REPLANNING, got {cur.value}")
    if recovery_plan is not None:
        node.recovery_metadata = dict(recovery_plan)
    # Explicitly do not touch verification_evidence / VERIFIED status.
    node.set_status(NodeStatus.READY)
    node.blocking_reason = None
    assert node.job_or_task_id == job_id
    assert node.status != NodeStatus.VERIFIED.value
    return {
        "node_id": node_id,
        "from": NodeStatus.REPLANNING.value,
        "to": NodeStatus.READY.value,
        "transitioned": True,
        "job_or_task_id": node.job_or_task_id,
        "status": node.status,
    }


def apply_recovery_failure(
    nodes: list[OrchestrationNode],
    node_id: str,
    reason: Optional[str] = None,
) -> dict:
    """End a failed recovery attempt (no automatic infinite retry).

    RECOVERING → FAILED.
    REPLANNING → CANCELLED (no REPLANNING→FAILED in the transition table).
    """
    node = _get_node(nodes, node_id)
    cur = NodeStatus(node.status)
    job_id = node.job_or_task_id
    why = reason or "recovery_failed"
    if cur == NodeStatus.RECOVERING:
        node.set_status(NodeStatus.FAILED)
        node.blocking_reason = why
        return {
            "node_id": node_id,
            "from": NodeStatus.RECOVERING.value,
            "to": NodeStatus.FAILED.value,
            "transitioned": True,
            "job_or_task_id": job_id,
            "reason": why,
        }
    if cur == NodeStatus.REPLANNING:
        node.set_status(NodeStatus.CANCELLED)
        node.blocking_reason = why
        return {
            "node_id": node_id,
            "from": NodeStatus.REPLANNING.value,
            "to": NodeStatus.CANCELLED.value,
            "transitioned": True,
            "job_or_task_id": job_id,
            "reason": why,
        }
    if cur in (NodeStatus.FAILED, NodeStatus.CANCELLED) and node.blocking_reason == why:
        return {
            "node_id": node_id,
            "from": cur.value,
            "to": cur.value,
            "transitioned": False,
            "job_or_task_id": job_id,
            "reason": why,
        }
    raise ValueError(f"apply_recovery_failure requires RECOVERING|REPLANNING, got {cur.value}")


def mark_node_verified(
    nodes: list[OrchestrationNode],
    node_id: str,
    evidence: dict,
) -> dict:
    """VERIFYING → VERIFIED only with non-empty verification evidence.

    recovery_metadata alone must not satisfy this requirement.
    Node must be VERIFYING (i.e. has gone through re-execution path).
    """
    node = _get_node(nodes, node_id)
    if not evidence or not isinstance(evidence, dict):
        raise ValueError("mark_node_verified requires non-empty verification evidence")
    # Recovery plans are never verification evidence. Require explicit signals.
    has_verify_signal = any(
        k in evidence and evidence.get(k) not in (None, "", False, [], {})
        for k in ("ok", "passed", "checks", "files_changed", "status")
    )
    recovery_only_keys = {
        "recovery", "recovery_plan", "plan", "decision", "reason",
        "attempt", "failure_class", "has_failed_edge",
    }
    if not has_verify_signal or set(evidence.keys()) <= recovery_only_keys:
        raise ValueError("recovery metadata is not verification evidence")
    cur = NodeStatus(node.status)
    if cur == NodeStatus.VERIFIED and node.verification_evidence:
        return {
            "node_id": node_id,
            "from": cur.value,
            "to": cur.value,
            "transitioned": False,
            "job_or_task_id": node.job_or_task_id,
        }
    if cur != NodeStatus.VERIFYING:
        raise ValueError(f"mark_node_verified requires VERIFYING, got {cur.value}")
    node.verification_evidence = dict(evidence)
    node.set_status(NodeStatus.VERIFIED)
    return {
        "node_id": node_id,
        "from": NodeStatus.VERIFYING.value,
        "to": NodeStatus.VERIFIED.value,
        "transitioned": True,
        "job_or_task_id": node.job_or_task_id,
    }


def reconcile_after_recovery(
    nodes: list[OrchestrationNode],
    edges: list[OrchestrationEdge],
    recovered_id: str,
) -> list[str]:
    """Re-evaluate dependents after a recovered node is READY/VERIFIED/COMPLETED.

    Clears dependency_failed blocks when dependencies are again satisfiable.
    Idempotent: repeated calls do not duplicate edges or corrupt status.
    Returns node ids that became READY.
    """
    by_id = {n.id: n for n in nodes}
    incoming = _incoming_map(nodes, edges)
    newly_ready: list[str] = []

    for n in nodes:
        st = NodeStatus(n.status)
        if st not in (
            NodeStatus.BLOCKED_BY_DEPENDENCY, NodeStatus.PENDING, NodeStatus.READY, NodeStatus.REPLANNING,
        ):
            continue
        deps = incoming.get(n.id, [])
        all_ok = True
        for src, cond in deps:
            parent = by_id.get(src)
            if not parent or not _dep_satisfied(parent, cond):
                all_ok = False
                break
        if all_ok:
            if st == NodeStatus.BLOCKED_BY_DEPENDENCY:
                # Unblock: PENDING is not always valid from BLOCKED_BY_DEPENDENCY;
                # READY is allowed from BLOCKED_BY_DEPENDENCY.
                try:
                    n.set_status(NodeStatus.READY)
                except ValueError:
                    n.status = NodeStatus.READY.value
                n.blocking_reason = None
            newly_ready.append(n.id)
        else:
            if st in (NodeStatus.PENDING, NodeStatus.READY) and deps:
                # Still blocked
                if st == NodeStatus.PENDING:
                    try:
                        n.set_status(NodeStatus.BLOCKED_BY_DEPENDENCY)
                    except ValueError:
                        n.status = NodeStatus.BLOCKED_BY_DEPENDENCY.value
                failed_parents = [
                    src for src, cond in deps
                    if by_id.get(src) and NodeStatus(by_id[src].status) == NodeStatus.FAILED
                    and cond != DepCondition.FAILED.value
                ]
                if failed_parents:
                    n.blocking_reason = f"dependency_failed:{failed_parents[0]}"

    # Also surface readiness via canonical helper
    for rid in compute_readiness(nodes, edges):
        if rid not in newly_ready:
            newly_ready.append(rid)
    return newly_ready


def compute_readiness(
    nodes: list[OrchestrationNode],
    edges: list[OrchestrationEdge],
) -> list[str]:
    """Return node ids that are READY (deps satisfied, not terminal/cancelled)."""
    by_id = {n.id: n for n in nodes}
    incoming = _incoming_map(nodes, edges)

    ready_ids: list[str] = []
    for n in nodes:
        raw = (n.status or "").lower()
        # pending_review is investigation-only (not a NodeStatus enum member)
        if raw == "pending_review":
            continue
        try:
            st = NodeStatus(n.status)
        except ValueError:
            continue
        if st in (
            NodeStatus.COMPLETED, NodeStatus.VERIFIED, NodeStatus.CANCELLED,
            NodeStatus.BLOCKED, NodeStatus.RUNNING, NodeStatus.QUEUED,
            NodeStatus.AUTHORIZED, NodeStatus.VERIFYING,
            NodeStatus.FAILED, NodeStatus.RECOVERING,
        ):
            continue
        deps = incoming.get(n.id, [])
        if not deps:
            # root
            if st in (NodeStatus.PENDING, NodeStatus.BLOCKED_BY_DEPENDENCY, NodeStatus.READY, NodeStatus.REPLANNING):
                ready_ids.append(n.id)
            continue
        all_ok = True
        for src, cond in deps:
            parent = by_id.get(src)
            if not parent or not _dep_satisfied(parent, cond):
                all_ok = False
                break
        if all_ok:
            ready_ids.append(n.id)
        else:
            # mark blocked by dependency if still pending
            if st == NodeStatus.PENDING:
                try:
                    n.set_status(NodeStatus.BLOCKED_BY_DEPENDENCY)
                except ValueError:
                    n.status = NodeStatus.BLOCKED_BY_DEPENDENCY.value
    return ready_ids


def propagate_failure(
    nodes: list[OrchestrationNode],
    edges: list[OrchestrationEdge],
    failed_id: str,
) -> list[str]:
    """Propagate failure to ordinary dependents with recovery-edge awareness.

    - Ordinary VERIFIED/COMPLETED dependents become BLOCKED_BY_DEPENDENCY.
    - Explicit FAILED-condition recovery edges are NOT blocked (targets may
      become READY via compute_readiness while the parent is FAILED).
    - Does NOT invent automatic FAILED→RECOVERING transitions; Nuha/callers
      invoke begin_node_recovery explicitly when appropriate.
    - Idempotent: re-running does not duplicate blocks or edges.
    """
    by_id = {n.id: n for n in nodes}
    blocked: list[str] = []
    children = _children_map(nodes, edges)

    # BFS over ordinary (non-FAILED-condition) descendants only
    stack = [
        tid for tid, cond in children.get(failed_id, [])
        if cond != DepCondition.FAILED.value
    ]
    seen: set[str] = set()
    while stack:
        cid = stack.pop()
        if cid in seen:
            continue
        seen.add(cid)
        node = by_id.get(cid)
        if not node:
            continue
        st = NodeStatus(node.status)
        if st in (NodeStatus.COMPLETED, NodeStatus.VERIFIED, NodeStatus.CANCELLED, NodeStatus.BLOCKED):
            continue
        # Soft block — recoverable after parent recovers and verifies
        node.status = NodeStatus.BLOCKED_BY_DEPENDENCY.value
        node.blocking_reason = f"dependency_failed:{failed_id}"
        if cid not in blocked:
            blocked.append(cid)
        for tid, cond in children.get(cid, []):
            if cond != DepCondition.FAILED.value:
                stack.append(tid)
    return blocked


def check_node_invariants(node: OrchestrationNode) -> list[str]:
    """Machine-checkable node invariants."""
    issues = []
    st = NodeStatus(node.status)
    if st == NodeStatus.RUNNING:
        if not node.job_or_task_id:
            issues.append("running_without_job_ref")
        if not node.persona_id:
            issues.append("running_without_persona")
        if not node.authorization_decision:
            issues.append("running_without_authorization")
    if st == NodeStatus.AUTHORIZED:
        if node.authorization_decision != "allow":
            issues.append("authorized_without_allow_decision")
    if st == NodeStatus.VERIFIED:
        if not node.verification_evidence:
            issues.append("verified_without_evidence")
    if st == NodeStatus.COMPLETED:
        if not node.verification_evidence and NodeStatus.VERIFIED.value not in (node.status,):
            # completed should follow verified path
            if not node.verification_evidence:
                issues.append("completed_without_verification_evidence")
    if st == NodeStatus.BLOCKED and not node.blocking_reason:
        issues.append("blocked_without_reason")
    return issues


def nodes_from_steps(steps) -> tuple[list[OrchestrationNode], list[OrchestrationEdge]]:
    """Convert legacy OrchestrationStep list into formal nodes+edges (verified deps)."""
    nodes: list[OrchestrationNode] = []
    edges: list[OrchestrationEdge] = []
    for s in steps:
        nodes.append(OrchestrationNode(
            id=s.id,
            description=s.description,
            persona_id=s.persona_id,
            dependencies=list(s.dependencies or []),
            capabilities=list(getattr(s, "required_capabilities", None) or getattr(s, "capabilities", None) or []),
            workspace_scope=getattr(s, "workspace_scope", "project") or "project",
            expected_outputs=[getattr(s, "expected_output", "")] if getattr(s, "expected_output", None) else list(getattr(s, "expected_outputs", []) or []),
            verification_criteria=list(getattr(s, "verification_criteria", None) or []),
            risk="low",
            status=NodeStatus.PENDING.value,
            job_or_task_id=getattr(s, "job_or_task_id", None),
        ))
        for d in (s.dependencies or []):
            edges.append(OrchestrationEdge(source=d, target=s.id, condition=DepCondition.VERIFIED.value))
    return nodes, edges
