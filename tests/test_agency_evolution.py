from governance.agency_evolution import (
    evaluate_execution, filter_autonomous_caps, ALWAYS_HUMAN_GATED, SUPERVISED_CAPS,
    CAP_AUTONOMY_MIN_SAMPLES, CAP_AUTONOMY_THRESHOLD, COMPETENCY_PRIOR,
    _cap_earned, AutonomyProfile,
)
from types import SimpleNamespace

def test_evaluate_schema_authoritative():
    e = evaluate_execution(success=True, expected_outcome_met=True)
    assert e.correctness == 1.0
    e2 = evaluate_execution(success=True, expected_outcome_met=False)
    assert e2.success is False and e2.correctness <= 0.1

def test_evaluate_no_schema_is_partial_not_high():
    e = evaluate_execution(success=True, status="complete")
    assert e.correctness <= 0.55
    assert "partial" in " ".join(e.notes).lower() or "without_schema" in " ".join(e.notes)

def test_unauthorized_zero_delta():
    e = evaluate_execution(success=True, unauthorized=True)
    assert e.competency_delta == 0.0

def test_supervised_only_low_risk_caps():
    row = SimpleNamespace(
        autonomy="supervised", promotion_expires_at=None,
        competency={},
    )
    caps = {"ucip:memory.read", "ucip:execution.python", "ucip:system.shell", "ucip:filesystem.write"}
    f = filter_autonomous_caps(caps, row)
    assert "ucip:memory.read" in f
    assert "ucip:execution.python" not in f
    assert "ucip:system.shell" not in f
    assert f <= SUPERVISED_CAPS | {"ucip:general"}

def test_no_row_means_supervised_only():
    caps = {"ucip:memory.read", "ucip:execution.python"}
    f = filter_autonomous_caps(caps, None)
    assert f == {"ucip:memory.read"}

def test_autonomous_requires_samples():
    row = SimpleNamespace(
        autonomy="autonomous", promotion_expires_at=None,
        competency={"ucip:execution.python": {"competency": 0.95, "samples": 3}},
    )
    f = filter_autonomous_caps({"ucip:execution.python"}, row)
    # not enough samples
    assert "ucip:execution.python" not in f

def test_autonomous_earned_cap():
    row = SimpleNamespace(
        autonomy="autonomous", promotion_expires_at=None,
        competency={"ucip:execution.python": {
            "competency": 0.91, "samples": CAP_AUTONOMY_MIN_SAMPLES,
        }},
    )
    f = filter_autonomous_caps({"ucip:execution.python", "ucip:system.shell"}, row)
    assert "ucip:execution.python" in f
    assert "ucip:system.shell" not in f

def test_runtime_source_fail_closed():
    src = open("workers/runtime.py").read()
    assert "WorkerTrustUnavailable" in src
    assert "fail closed" in src.lower() or "FAIL CLOSED" in src
    assert "except Exception" in src
    # must re-raise, not continue
    assert "raise WorkerTrustUnavailable" in src

def test_prior_is_conservative():
    assert COMPETENCY_PRIOR <= 0.5

def test_min_samples_malformed_safe():
    from governance.agency_evolution import _min_samples, _min_competency, _avg_competency
    row = SimpleNamespace(competency=None)
    assert _min_samples(row) == 0
    assert _min_competency(row) == 0.0
    assert _avg_competency(row) == 0.0
    row = SimpleNamespace(competency={"a": "bad", "b": 1, "c": {"samples": "nope"}})
    assert _min_samples(row) == 0  # unparsable samples skipped → empty → 0
    row = SimpleNamespace(competency={"c": {"samples": 7, "competency": 0.8}, "d": {"samples": 2, "competency": 0.9}})
    assert _min_samples(row) == 2
    assert abs(_min_competency(row) - 0.8) < 1e-9  # only samples>=5

def test_per_capability_autonomy_levels():
    from governance.agency_evolution import capability_autonomy_level
    row = SimpleNamespace(
        autonomy="autonomous",
        promotion_expires_at=None,
        competency={
            "ucip:memory.read": {"competency": 0.95, "samples": 30},
            "ucip:execution.python": {"competency": 0.5, "samples": 5},
        },
    )
    assert capability_autonomy_level(row, "ucip:system.shell") == "supervised"
    # memory is supervised-caps and global autonomous → autonomous
    assert capability_autonomy_level(row, "ucip:memory.read") == "autonomous"
    # python not earned enough → supervised
    assert capability_autonomy_level(row, "ucip:execution.python") == "supervised"

def test_runtime_uses_job_snapshot():
    src = open("workers/runtime.py").read()
    assert "snapshot_trust" in src
    assert "execution_job_id" in src or "job_id" in src
    assert "enqueue" in src
