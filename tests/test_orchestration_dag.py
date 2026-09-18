"""DAG validation, readiness, node transitions, specialty policy."""
import pytest
from brain.orchestration_dag import (
    OrchestrationNode,
    OrchestrationEdge,
    NodeStatus,
    DepCondition,
    validate_dag,
    assert_valid_dag,
    compute_readiness,
    propagate_failure,
    can_node_transition,
    transition_node,
    check_node_invariants,
    DAGValidationError,
)
from brain.specialty_policy import evaluate_node_request, get_specialty_policy


def _nodes(*specs):
    out = []
    for s in specs:
        if len(s) == 3:
            nid, persona, deps = s
            caps = ["fs.read"]
        else:
            nid, persona, deps, caps = s
        out.append(OrchestrationNode(
            id=nid, description=nid, persona_id=persona,
            dependencies=list(deps), capabilities=list(caps),
        ))
    return out


def test_valid_linear_dag():
    nodes = _nodes(("a", "web", [], ["fs.read", "fs.write"]), ("b", "code", ["a"], ["fs.read"]))
    edges = [OrchestrationEdge("a", "b", DepCondition.VERIFIED.value)]
    assert validate_dag(nodes, edges) == []


def test_cycle_detected():
    nodes = _nodes(("a", "web", ["c"]), ("b", "code", ["a"]), ("c", "code", ["b"]))
    edges = [
        OrchestrationEdge("a", "b"),
        OrchestrationEdge("b", "c"),
        OrchestrationEdge("c", "a"),
    ]
    issues = validate_dag(nodes, edges)
    assert any("cycle" in i for i in issues)
    with pytest.raises(DAGValidationError):
        assert_valid_dag(nodes, edges)


def test_self_dependency():
    nodes = _nodes(("a", "web", ["a"]))
    edges = [OrchestrationEdge("a", "a")]
    issues = validate_dag(nodes, edges)
    assert any("self" in i for i in issues)


def test_missing_dependency():
    nodes = _nodes(("a", "web", ["z"]))
    issues = validate_dag(nodes, [])
    assert any("missing_dependency" in i for i in issues)


def test_duplicate_node():
    n = OrchestrationNode(id="a", description="x", persona_id="web", capabilities=["fs.read"])
    issues = validate_dag([n, n], [])
    assert "duplicate_node_id" in issues


def test_readiness_blocked_until_verified():
    a = OrchestrationNode(id="a", description="a", persona_id="web", capabilities=["fs.read"])
    b = OrchestrationNode(id="b", description="b", persona_id="code", dependencies=["a"], capabilities=["fs.read"])
    edges = [OrchestrationEdge("a", "b", DepCondition.VERIFIED.value)]
    ready = compute_readiness([a, b], edges)
    assert "a" in ready
    assert "b" not in ready
    a.status = NodeStatus.VERIFIED.value
    a.verification_evidence = {"ok": True}
    ready2 = compute_readiness([a, b], edges)
    assert "b" in ready2


def test_failure_propagation():
    a = OrchestrationNode(id="a", description="a", persona_id="web", capabilities=["fs.read"])
    b = OrchestrationNode(id="b", description="b", persona_id="code", dependencies=["a"], capabilities=["fs.read"])
    c = OrchestrationNode(id="c", description="c", persona_id="code", dependencies=["b"], capabilities=["fs.read"])
    edges = [OrchestrationEdge("a", "b"), OrchestrationEdge("b", "c")]
    a.status = NodeStatus.FAILED.value
    blocked = propagate_failure([a, b, c], edges, "a")
    assert "b" in blocked and "c" in blocked
    assert b.status == NodeStatus.BLOCKED_BY_DEPENDENCY.value


def test_invalid_node_transition():
    assert not can_node_transition(NodeStatus.COMPLETED, NodeStatus.RUNNING)
    with pytest.raises(ValueError):
        transition_node(NodeStatus.COMPLETED, NodeStatus.RUNNING)


def test_running_invariant_requires_auth():
    n = OrchestrationNode(id="a", description="a", persona_id="web", capabilities=["fs.read"])
    n.status = NodeStatus.RUNNING.value
    issues = check_node_invariants(n)
    assert "running_without_job_ref" in issues
    assert "running_without_authorization" in issues


def test_research_cannot_shell():
    d = evaluate_node_request(persona_id="research", requested_caps={"shell.exec", "fs.read"})
    assert "shell.exec" in d.denied_caps
    assert "fs.read" in d.effective_caps


def test_production_delete_denied():
    d = evaluate_node_request(persona_id="web", requested_caps={"production.delete"})
    assert d.allow is False
    assert "production.delete" in d.denied_caps


def test_web_allow_write():
    d = evaluate_node_request(persona_id="web", requested_caps={"fs.write", "fs.read"})
    assert d.allow is True
    assert "fs.write" in d.effective_caps


def test_policy_not_second_engine_note():
    p = get_specialty_policy("web")
    assert "second" in p.to_dict()["note"].lower() or "UCIP" in p.to_dict()["note"]


# --- Recovery / resume path ---

def test_recovery_linear_unblocks_dependents():
    """A→B→C: A fails, recovery succeeds with evidence, B then C become READY."""
    from brain.orchestration_dag import (
        begin_node_recovery, begin_node_replanning, apply_recovery_success,
        reconcile_after_recovery, mark_node_verified,
    )
    a = OrchestrationNode(id="a", description="a", persona_id="web", capabilities=["fs.read"])
    b = OrchestrationNode(id="b", description="b", persona_id="code", dependencies=["a"], capabilities=["fs.read"])
    c = OrchestrationNode(id="c", description="c", persona_id="code", dependencies=["b"], capabilities=["fs.read"])
    edges = [
        OrchestrationEdge("a", "b", DepCondition.VERIFIED.value),
        OrchestrationEdge("b", "c", DepCondition.VERIFIED.value),
        # explicit recovery topology from A
        OrchestrationEdge("a", "a_recovery", DepCondition.FAILED.value),
    ]
    a_recovery = OrchestrationNode(
        id="a_recovery", description="recover a", persona_id="code",
        capabilities=["fs.read"],
    )
    nodes = [a, b, c, a_recovery]
    a.status = NodeStatus.FAILED.value
    a.job_or_task_id = "job-a-1"
    blocked = propagate_failure(nodes, edges, "a")
    assert "b" in blocked and "c" in blocked
    assert a.status == NodeStatus.RECOVERING.value  # auto-start via recovery path
    assert a.job_or_task_id == "job-a-1"  # lineage preserved
    begin_node_replanning(a)
    apply_recovery_success(a, evidence={"plan": "retry"})
    assert a.status == NodeStatus.READY.value
    # Re-execution path: authorize/queue/run/verify (simplified)
    a.status = NodeStatus.VERIFYING.value
    mark_node_verified(a, {"ok": True, "checks": ["unit"]})
    assert a.status == NodeStatus.VERIFIED.value
    assert a.verification_evidence and a.verification_evidence.get("ok") is True
    ready = reconcile_after_recovery(nodes, edges, "a")
    assert "b" in ready
    assert b.status == NodeStatus.READY.value
    b.status = NodeStatus.VERIFIED.value
    b.verification_evidence = {"ok": True}
    ready2 = compute_readiness(nodes, edges)
    assert "c" in ready2


def test_recovery_failure_keeps_dependents_blocked():
    from brain.orchestration_dag import (
        begin_node_recovery, begin_node_replanning, apply_recovery_failure,
    )
    a = OrchestrationNode(id="a", description="a", persona_id="web", capabilities=["fs.read"])
    b = OrchestrationNode(id="b", description="b", persona_id="code", dependencies=["a"], capabilities=["fs.read"])
    edges = [
        OrchestrationEdge("a", "b", DepCondition.VERIFIED.value),
        OrchestrationEdge("a", "a_recovery", DepCondition.FAILED.value),
    ]
    a_recovery = OrchestrationNode(id="a_recovery", description="r", persona_id="code", capabilities=["fs.read"])
    nodes = [a, b, a_recovery]
    a.status = NodeStatus.FAILED.value
    propagate_failure(nodes, edges, "a")
    assert b.status == NodeStatus.BLOCKED_BY_DEPENDENCY.value
    # recovery attempt fails while still RECOVERING
    apply_recovery_failure(a, reason="recovery_exhausted")
    assert a.status == NodeStatus.FAILED.value
    ready = compute_readiness(nodes, edges)
    assert "b" not in ready
    assert b.status == NodeStatus.BLOCKED_BY_DEPENDENCY.value


def test_no_recovery_path_blocks_dependents():
    a = OrchestrationNode(id="a", description="a", persona_id="web", capabilities=["fs.read"])
    b = OrchestrationNode(id="b", description="b", persona_id="code", dependencies=["a"], capabilities=["fs.read"])
    edges = [OrchestrationEdge("a", "b", DepCondition.VERIFIED.value)]
    a.status = NodeStatus.FAILED.value
    blocked = propagate_failure([a, b], edges, "a")
    assert "b" in blocked
    assert a.status == NodeStatus.FAILED.value  # no auto RECOVERING without FAILED edge
    assert b.status == NodeStatus.BLOCKED_BY_DEPENDENCY.value
    assert "b" not in compute_readiness([a, b], edges)


def test_recovery_multiple_dependents():
    from brain.orchestration_dag import (
        begin_node_replanning, apply_recovery_success, reconcile_after_recovery,
        mark_node_verified,
    )
    a = OrchestrationNode(id="a", description="a", persona_id="web", capabilities=["fs.read"])
    b = OrchestrationNode(id="b", description="b", persona_id="code", dependencies=["a"], capabilities=["fs.read"])
    c = OrchestrationNode(id="c", description="c", persona_id="code", dependencies=["a"], capabilities=["fs.read"])
    rec = OrchestrationNode(id="rec", description="rec", persona_id="code", capabilities=["fs.read"])
    edges = [
        OrchestrationEdge("a", "b", DepCondition.VERIFIED.value),
        OrchestrationEdge("a", "c", DepCondition.VERIFIED.value),
        OrchestrationEdge("a", "rec", DepCondition.FAILED.value),
    ]
    nodes = [a, b, c, rec]
    a.status = NodeStatus.FAILED.value
    propagate_failure(nodes, edges, "a")
    begin_node_replanning(a)
    apply_recovery_success(a, evidence={"retry": 1})
    a.status = NodeStatus.VERIFYING.value
    mark_node_verified(a, {"ok": True})
    ready = reconcile_after_recovery(nodes, edges, "a")
    assert "b" in ready and "c" in ready
    assert b.status == NodeStatus.READY.value
    assert c.status == NodeStatus.READY.value


def test_recovery_diamond_d_waits():
    from brain.orchestration_dag import (
        begin_node_replanning, apply_recovery_success, reconcile_after_recovery,
        mark_node_verified,
    )
    a = OrchestrationNode(id="a", description="a", persona_id="web", capabilities=["fs.read"])
    b = OrchestrationNode(id="b", description="b", persona_id="code", dependencies=["a"], capabilities=["fs.read"])
    c = OrchestrationNode(id="c", description="c", persona_id="code", dependencies=["a"], capabilities=["fs.read"])
    d = OrchestrationNode(id="d", description="d", persona_id="code", dependencies=["b", "c"], capabilities=["fs.read"])
    rec = OrchestrationNode(id="rec", description="rec", persona_id="code", capabilities=["fs.read"])
    edges = [
        OrchestrationEdge("a", "b", DepCondition.VERIFIED.value),
        OrchestrationEdge("a", "c", DepCondition.VERIFIED.value),
        OrchestrationEdge("b", "d", DepCondition.VERIFIED.value),
        OrchestrationEdge("c", "d", DepCondition.VERIFIED.value),
        OrchestrationEdge("a", "rec", DepCondition.FAILED.value),
    ]
    nodes = [a, b, c, d, rec]
    a.status = NodeStatus.FAILED.value
    propagate_failure(nodes, edges, "a")
    begin_node_replanning(a)
    apply_recovery_success(a, evidence={"ok": 1})
    a.status = NodeStatus.VERIFYING.value
    mark_node_verified(a, {"ok": True})
    ready = reconcile_after_recovery(nodes, edges, "a")
    assert "b" in ready and "c" in ready
    assert "d" not in ready
    b.status = NodeStatus.VERIFIED.value
    b.verification_evidence = {"ok": True}
    c.status = NodeStatus.VERIFIED.value
    c.verification_evidence = {"ok": True}
    ready2 = compute_readiness(nodes, edges)
    assert "d" in ready2


def test_recovery_cannot_mark_verified_without_evidence():
    from brain.orchestration_dag import mark_node_verified
    a = OrchestrationNode(id="a", description="a", persona_id="web", capabilities=["fs.read"])
    a.status = NodeStatus.VERIFYING.value
    with pytest.raises(ValueError):
        mark_node_verified(a, {})
    with pytest.raises(ValueError):
        mark_node_verified(a, None)  # type: ignore
    assert a.status == NodeStatus.VERIFYING.value


def test_propagate_failure_idempotent():
    a = OrchestrationNode(id="a", description="a", persona_id="web", capabilities=["fs.read"])
    b = OrchestrationNode(id="b", description="b", persona_id="code", dependencies=["a"], capabilities=["fs.read"])
    edges = [OrchestrationEdge("a", "b", DepCondition.VERIFIED.value)]
    a.status = NodeStatus.FAILED.value
    b1 = propagate_failure([a, b], edges, "a")
    b2 = propagate_failure([a, b], edges, "a")
    assert b1 == b2 or set(b1) == set(b2)
    assert b.status == NodeStatus.BLOCKED_BY_DEPENDENCY.value
    assert len([e for e in edges if e.source == "a"]) == 1


def test_recovery_invalid_transitions():
    from brain.orchestration_dag import begin_node_recovery, begin_node_replanning, apply_recovery_success
    a = OrchestrationNode(id="a", description="a", persona_id="web", capabilities=["fs.read"])
    a.status = NodeStatus.READY.value
    with pytest.raises(ValueError):
        begin_node_recovery(a)
    a.status = NodeStatus.FAILED.value
    begin_node_recovery(a)
    with pytest.raises(ValueError):
        apply_recovery_success(a)  # still RECOVERING, not REPLANNING
    begin_node_replanning(a)
    apply_recovery_success(a, evidence={"plan": True})
    assert a.status == NodeStatus.READY.value


def test_failed_condition_edge_readiness():
    """FAILED-condition recovery node becomes ready when parent is FAILED."""
    a = OrchestrationNode(id="a", description="a", persona_id="web", capabilities=["fs.read"])
    r = OrchestrationNode(id="r", description="r", persona_id="code", capabilities=["fs.read"])
    edges = [OrchestrationEdge("a", "r", DepCondition.FAILED.value)]
    a.status = NodeStatus.FAILED.value
    ready = compute_readiness([a, r], edges)
    assert "r" in ready
    a.status = NodeStatus.VERIFIED.value
    a.verification_evidence = {"ok": True}
    ready2 = compute_readiness([a, r], edges)
    assert "r" not in ready2
