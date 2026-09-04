"""Parallel DAG readiness, conflicts, failure classification, revision."""
import os
os.environ["DEVOS_ORCH_FAKE_RUNTIME"] = "1"

from brain.orchestration_dag import OrchestrationNode, OrchestrationEdge, NodeStatus, DepCondition
from brain.mission_engine import (
    get_ready_nodes,
    path_conflict,
    partition_ready_for_parallel,
    classify_failure,
    FailureClass,
    create_repair_nodes,
    apply_revision,
)


def _n(i, deps=None, outs=None, status="pending"):
    return OrchestrationNode(
        id=i, description=i, persona_id="web",
        dependencies=list(deps or []),
        capabilities=["fs.write"],
        expected_outputs=list(outs or []),
        status=status,
    )


def test_multiple_ready_after_root_verified():
    a = _n("a", status=NodeStatus.VERIFIED.value)
    a.verification_evidence = {"ok": True}
    b = _n("b", deps=["a"], outs=["src/b.ts"])
    c = _n("c", deps=["a"], outs=["src/c.ts"])
    d = _n("d", deps=["a"], outs=["src/d.ts"])
    e = _n("e", deps=["b", "c", "d"])
    edges = [
        OrchestrationEdge("a", "b"), OrchestrationEdge("a", "c"), OrchestrationEdge("a", "d"),
        OrchestrationEdge("b", "e"), OrchestrationEdge("c", "e"), OrchestrationEdge("d", "e"),
    ]
    ready = get_ready_nodes([a, b, c, d, e], edges)
    ids = {n.id for n in ready}
    assert ids == {"b", "c", "d"}


def test_path_conflict_same_file():
    assert path_conflict(["src/app.ts"], ["src/app.ts"]) is True
    assert path_conflict(["src/a.ts"], ["src/b.ts"]) is False


def test_partition_serializes_conflicts():
    b = _n("b", outs=["src/app.ts"])
    c = _n("c", outs=["src/app.ts"])
    d = _n("d", outs=["src/other.ts"])
    batches = partition_ready_for_parallel([b, c, d])
    # b and c conflict — not same batch
    for batch in batches:
        ids = {n.id for n in batch}
        assert not ({"b", "c"} <= ids)


def test_classify_auth():
    assert classify_failure("authorization denied") == FailureClass.AUTHORIZATION_FAILURE


def test_repair_nodes_preserve_failed_identity():
    failed = _n("build", outs=["dist/"])
    repairs = create_repair_nodes(type("P", (), {})(), failed, FailureClass.VERIFICATION_FAILURE)
    assert len(repairs) == 2
    assert "repair" in repairs[0].id


def test_revision_preserves_verified():
    class P:
        id = "plan1"
        nodes = []
        edges = []
        revision = 1
        revisions = []
        dag_valid = True
        dag_issues = []
        def emit(self, *a, **k):
            pass
    a = _n("a", status=NodeStatus.VERIFIED.value)
    a.verification_evidence = {"ok": True}
    b = _n("b", status=NodeStatus.FAILED.value)
    p = P()
    p.nodes = [a, b]
    p.edges = [OrchestrationEdge("a", "b")]
    rev = apply_revision(p, b, FailureClass.EXECUTION_ERROR)
    assert "a" in rev.preserved_verified
    assert p.revision == 2
    assert len(rev.new_node_ids) == 2
