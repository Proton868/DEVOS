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

def test_ordinary_failure_blocks_ordinary_dependent():
    a = OrchestrationNode(id="a", description="a", persona_id="web", capabilities=["fs.read"])
    b = OrchestrationNode(id="b", description="b", persona_id="code", dependencies=["a"], capabilities=["fs.read"])
    edges = [OrchestrationEdge("a", "b", DepCondition.VERIFIED.value)]
    a.status = NodeStatus.FAILED.value
    blocked = propagate_failure([a, b], edges, "a")
    assert "b" in blocked
    assert b.status == NodeStatus.BLOCKED_BY_DEPENDENCY.value
    assert a.status == NodeStatus.FAILED.value


def test_failure_does_not_block_failed_recovery_target():
    a = OrchestrationNode(id="a", description="a", persona_id="web", capabilities=["fs.read"])
    b = OrchestrationNode(id="b", description="b", persona_id="code", dependencies=["a"], capabilities=["fs.read"])
    r = OrchestrationNode(id="r", description="recovery", persona_id="code", capabilities=["fs.read"])
    edges = [
        OrchestrationEdge("a", "b", DepCondition.VERIFIED.value),
        OrchestrationEdge("a", "r", DepCondition.FAILED.value),
    ]
    nodes = [a, b, r]
    a.status = NodeStatus.FAILED.value
    blocked = propagate_failure(nodes, edges, "a")
    assert "b" in blocked
    assert "r" not in blocked
    assert r.status != NodeStatus.BLOCKED_BY_DEPENDENCY.value
    ready = compute_readiness(nodes, edges)
    assert "r" in ready


def test_failed_to_recovering():
    from brain.orchestration_dag import begin_node_recovery
    a = OrchestrationNode(id="a", description="a", persona_id="web", capabilities=["fs.read"])
    a.status = NodeStatus.FAILED.value
    a.job_or_task_id = "job-1"
    r1 = begin_node_recovery([a], "a")
    assert r1["transitioned"] is True
    assert a.status == NodeStatus.RECOVERING.value
    assert a.job_or_task_id == "job-1"
    r2 = begin_node_recovery([a], "a")
    assert r2["transitioned"] is False
    assert a.job_or_task_id == "job-1"


def test_recovering_to_replanning():
    from brain.orchestration_dag import begin_node_recovery, begin_node_replanning
    a = OrchestrationNode(id="a", description="a", persona_id="web", capabilities=["fs.read"])
    a.status = NodeStatus.FAILED.value
    a.job_or_task_id = "job-2"
    begin_node_recovery([a], "a")
    r = begin_node_replanning([a], "a")
    assert r["transitioned"] is True
    assert a.status == NodeStatus.REPLANNING.value
    assert a.job_or_task_id == "job-2"
    r2 = begin_node_replanning([a], "a")
    assert r2["transitioned"] is False


def test_replanning_to_ready_not_verified():
    from brain.orchestration_dag import (
        begin_node_recovery, begin_node_replanning, apply_recovery_success,
    )
    a = OrchestrationNode(id="a", description="a", persona_id="web", capabilities=["fs.read"])
    a.status = NodeStatus.FAILED.value
    a.job_or_task_id = "job-3"
    begin_node_recovery([a], "a")
    begin_node_replanning([a], "a")
    r = apply_recovery_success([a], "a", recovery_plan={"strategy": "retry"})
    assert r["transitioned"] is True
    assert a.status == NodeStatus.READY.value
    assert a.status != NodeStatus.VERIFIED.value
    assert a.recovery_metadata == {"strategy": "retry"}
    assert a.verification_evidence is None
    assert a.job_or_task_id == "job-3"


def test_mark_verified_requires_evidence_after_reexec():
    from brain.orchestration_dag import mark_node_verified
    a = OrchestrationNode(id="a", description="a", persona_id="web", capabilities=["fs.read"])
    a.status = NodeStatus.READY.value
    with pytest.raises(ValueError):
        mark_node_verified([a], "a", {"ok": True})
    a.status = NodeStatus.VERIFYING.value
    with pytest.raises(ValueError):
        mark_node_verified([a], "a", {})
    r = mark_node_verified([a], "a", {"ok": True, "checks": ["unit"]})
    assert r["transitioned"] is True
    assert a.status == NodeStatus.VERIFIED.value
    assert a.verification_evidence["ok"] is True


def test_verification_reconciles_downstream():
    from brain.orchestration_dag import (
        begin_node_recovery, begin_node_replanning, apply_recovery_success,
        mark_node_verified, reconcile_after_recovery,
    )
    a = OrchestrationNode(id="a", description="a", persona_id="web", capabilities=["fs.read"])
    b = OrchestrationNode(id="b", description="b", persona_id="code", dependencies=["a"], capabilities=["fs.read"])
    r = OrchestrationNode(id="r", description="r", persona_id="code", capabilities=["fs.read"])
    edges = [
        OrchestrationEdge("a", "b", DepCondition.VERIFIED.value),
        OrchestrationEdge("a", "r", DepCondition.FAILED.value),
    ]
    nodes = [a, b, r]
    a.status = NodeStatus.FAILED.value
    a.job_or_task_id = "job-stable"
    propagate_failure(nodes, edges, "a")
    assert b.status == NodeStatus.BLOCKED_BY_DEPENDENCY.value
    # recover A through state machine
    begin_node_recovery(nodes, "a")
    begin_node_replanning(nodes, "a")
    apply_recovery_success(nodes, "a", recovery_plan={"retry": 1})
    a.status = NodeStatus.VERIFYING.value
    mark_node_verified(nodes, "a", {"ok": True})
    ready = reconcile_after_recovery(nodes, edges, "a")
    assert "b" in ready
    assert b.status == NodeStatus.READY.value
    assert a.job_or_task_id == "job-stable"
    # idempotent
    ready2 = reconcile_after_recovery(nodes, edges, "a")
    assert "b" in ready2


def test_recovery_failure_no_infinite_loop():
    from brain.orchestration_dag import begin_node_recovery, apply_recovery_failure
    a = OrchestrationNode(id="a", description="a", persona_id="web", capabilities=["fs.read"])
    b = OrchestrationNode(id="b", description="b", persona_id="code", dependencies=["a"], capabilities=["fs.read"])
    edges = [OrchestrationEdge("a", "b", DepCondition.VERIFIED.value)]
    a.status = NodeStatus.FAILED.value
    propagate_failure([a, b], edges, "a")
    begin_node_recovery([a], "a")
    apply_recovery_failure([a], "a", reason="exhausted")
    assert a.status == NodeStatus.FAILED.value
    assert b.status == NodeStatus.BLOCKED_BY_DEPENDENCY.value
    assert "b" not in compute_readiness([a, b], edges)
    # second failure apply is idempotent-ish when already FAILED with same reason
    r = apply_recovery_failure([a], "a", reason="exhausted")
    assert r["transitioned"] is False


def test_recovery_correlation_stable():
    from brain.orchestration_dag import (
        begin_node_recovery, begin_node_replanning, apply_recovery_success,
    )
    a = OrchestrationNode(id="a", description="a", persona_id="web", capabilities=["fs.read"])
    a.status = NodeStatus.FAILED.value
    a.job_or_task_id = "corr-99"
    begin_node_recovery([a], "a")
    begin_node_replanning([a], "a")
    apply_recovery_success([a], "a", recovery_plan={"x": 1})
    assert a.id == "a"
    assert a.job_or_task_id == "corr-99"


def test_recovery_idempotent_propagate():
    a = OrchestrationNode(id="a", description="a", persona_id="web", capabilities=["fs.read"])
    b = OrchestrationNode(id="b", description="b", persona_id="code", dependencies=["a"], capabilities=["fs.read"])
    edges = [OrchestrationEdge("a", "b", DepCondition.VERIFIED.value)]
    a.status = NodeStatus.FAILED.value
    b1 = propagate_failure([a, b], edges, "a")
    b2 = propagate_failure([a, b], edges, "a")
    assert set(b1) == set(b2)
    assert b.status == NodeStatus.BLOCKED_BY_DEPENDENCY.value
