from governance.agency_evolution import (
    evaluate_execution, filter_autonomous_caps, ALWAYS_HUMAN_GATED,
    CAP_AUTONOMY_THRESHOLD, EvaluationResult,
)

def test_evaluate_success_with_schema():
    e = evaluate_execution(success=True, expected_outcome_met=True, capabilities_used=["ucip:memory.read"])
    assert e.success and e.correctness == 1.0
    assert e.competency_delta > 0.5

def test_evaluate_unauthorized():
    e = evaluate_execution(success=True, unauthorized=True)
    assert e.unauthorized_attempt
    assert e.competency_delta == 0.0

def test_filter_always_gated():
    caps = {"ucip:memory.read", "ucip:system.shell", "financial.transfer"}
    f = filter_autonomous_caps(caps)
    assert "ucip:memory.read" in f
    assert "ucip:system.shell" not in f

def test_worker_request_source_has_no_trust_level():
    src = open("api/routes/workers.py").read()
    assert "trust_level: str" not in src
    assert "identity_from_worker" not in src or "Human API caller" in src
    assert "identity_from_user" in src
    assert "approve_promotion" in src
    assert "record_outcome" in src
