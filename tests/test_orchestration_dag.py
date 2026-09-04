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
