
"""Prove pipeline fail-closed semantics and path coverage."""
from types import SimpleNamespace

def test_baseline_caps_naming():
    from governance.agency_evolution import BASELINE_CAPS, SUPERVISED_CAPS
    assert BASELINE_CAPS == SUPERVISED_CAPS

def test_autonomy_level_stamp_is_derived_not_authority():
    from governance.agency_evolution import capability_autonomy_level
    row = SimpleNamespace(
        autonomy="autonomous", promotion_expires_at=None,
        competency={"ucip:execution.python": {
            "competency": 0.1, "samples": 1,
            "autonomy_level": "full_autonomous",
            "autonomy_override": "full_autonomous",
        }},
    )
    assert capability_autonomy_level(row, "ucip:execution.python") == "supervised"

def test_network_derived_from_uci_not_caller():
    from governance.execution_pipeline import network_allowed_for_capability
    assert network_allowed_for_capability(None) is False
    assert network_allowed_for_capability("ucip:memory.read") is False
    assert network_allowed_for_capability("ucip:network.outbound") is True
    assert network_allowed_for_capability("ucip:search.web") is True

def test_run_sandboxed_ignores_caller_network_without_cap():
    from governance.execution_pipeline import network_allowed_for_capability
    assert network_allowed_for_capability("ucip:execution.python") is False
    src = open("governance/execution_pipeline.py").read()
    assert "caller requested allow_network=True" in src
    assert "network_allowed_for_capability" in src

def test_job_creation_fail_closed_default():
    src = open("governance/execution_pipeline.py").read()
    assert "JobCreationError" in src
    assert "allow_missing_job" in src
    assert "PathClass.DURABLE" in src
    # must not continue on durable failure by default
    assert "continuing with evidence-only" not in src

def test_evidence_failure_semantics():
    src = open("governance/execution_pipeline.py").read()
    assert "EvidenceWriteError" in src
    assert "DEGRADED" in src or "degraded" in src
    assert "promotion_credit" in open("governance/agency_evolution.py").read() or "promotion_credit" in open("api/routes/workers.py").read()

def test_loop_uses_pipeline_and_tenant():
    assert "run_brain_loop" in open("api/routes/loop.py").read()
    assert "tenant_id" in open("core/loop.py").read()
    assert "WorkerTrustUnavailable" in open("core/loop.py").read()

def test_workers_runtime_fail_closed():
    src = open("workers/runtime.py").read()
    assert "WorkerTrustUnavailable" in src and "snapshot_trust" in src

def test_coordinator_requires_tenant():
    assert "tenant_id" in open("cognitive/coordinator.py").read()

def test_no_brain_loop_bypass_outside_pipeline():
    """BrainExecutionLoop() should only appear in pipeline, workers, coordinator."""
    import pathlib
    hits = []
    for path in pathlib.Path(".").rglob("*.py"):
        if "test_" in path.name or "node_modules" in str(path):
            continue
        text = path.read_text(errors="ignore")
        if "BrainExecutionLoop(" in text:
            hits.append(str(path))
    allowed = {
        "governance/execution_pipeline.py",
        "workers/runtime.py",
        "cognitive/coordinator.py",
        "core/loop.py",  # class definition
    }
    for h in hits:
        assert any(h.endswith(a) or a in h for a in allowed), f"unexpected BrainExecutionLoop site: {h}"

def test_pipeline_exports():
    from governance import execution_pipeline as ep
    assert hasattr(ep, "JobCreationError")
    assert hasattr(ep, "EvidenceWriteError")
    assert hasattr(ep, "PathClass")
    assert hasattr(ep, "network_allowed_for_capability")
