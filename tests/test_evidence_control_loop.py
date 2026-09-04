"""Evidence-driven diagnosis and NuhaDecision — no graph mutation by decision itself."""
from brain.mission_engine import (
    FailureClass,
    DecisionType,
    ExecutionEvidence,
    classify_failure,
    diagnose_evidence,
    decide_recovery,
    apply_revision,
)
from brain.orchestration_dag import OrchestrationNode, OrchestrationEdge, NodeStatus
from brain.specialty_policy import evaluate_node_request


def test_build_failure_class():
    assert classify_failure("npm ERR! build failed") == FailureClass.BUILD_FAILURE


def test_code_error_class():
    assert classify_failure("SyntaxError: unexpected token") == FailureClass.CODE_ERROR


def test_missing_credential_asks_user():
    ev = ExecutionEvidence(node_id="n1", error="missing API key credential", status="failed")
    d = decide_recovery(ev, attempt_count=0)
    assert d.decision_type == DecisionType.ASK_USER.value
    assert d.requires_user is True


def test_build_failure_repairs():
    ev = ExecutionEvidence(node_id="build", error="npm ERR! cannot find module", status="failed")
    d = decide_recovery(ev, attempt_count=0)
    assert d.decision_type == DecisionType.REPAIR.value
    assert d.requires_user is False


def test_bounded_attempts_escalate():
    ev = ExecutionEvidence(node_id="x", error="npm ERR! build failed", status="failed")
    d = decide_recovery(ev, attempt_count=5, max_attempts=2)
    assert d.decision_type == DecisionType.ASK_USER.value


def test_auth_failure_never_auto_retry():
    ev = ExecutionEvidence(node_id="x", error="authorization denied", status="failed")
    d = decide_recovery(ev, attempt_count=0)
    assert d.decision_type == DecisionType.ASK_USER.value


def test_revision_does_not_grant_extra_caps():
    class P:
        id = "p"
        nodes = []
        edges = []
        revision = 1
        revisions = []
        dag_valid = True
        dag_issues = []
        def emit(self, *a, **k):
            pass
    failed = OrchestrationNode(
        id="c", description="c", persona_id="research",
        capabilities=["fs.read"], status=NodeStatus.FAILED.value,
    )
    a = OrchestrationNode(
        id="a", description="a", persona_id="web",
        capabilities=["fs.write"], status=NodeStatus.VERIFIED.value,
        verification_evidence={"ok": True},
    )
    p = P()
    p.nodes = [a, failed]
    p.edges = [OrchestrationEdge("a", "c")]
    rev = apply_revision(p, failed, FailureClass.BUILD_FAILURE)
    assert "a" in rev.preserved_verified
    # New repair nodes still must pass specialty policy separately
    for nid in rev.new_node_ids:
        node = next(n for n in p.nodes if n.id == nid)
        # research would deny shell — repair uses failed persona
        assert node.persona_id == "research"


def test_evidence_no_fabricated_fields():
    ev = ExecutionEvidence(node_id="n1", unavailable=["stdout"])
    d = ev.to_dict()
    assert d["unavailable"] == ["stdout"]
    assert d.get("stdout") is None
