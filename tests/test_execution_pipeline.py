
"""Prove primary execution paths share the unified pipeline surface."""

def test_baseline_caps_naming():
    from governance.agency_evolution import BASELINE_CAPS, SUPERVISED_CAPS
    assert BASELINE_CAPS == SUPERVISED_CAPS
    assert "ucip:memory.read" in BASELINE_CAPS
    assert "ucip:execution.python" not in BASELINE_CAPS

def test_autonomy_level_stamp_is_derived_not_authority():
    from governance.agency_evolution import capability_autonomy_level
    from types import SimpleNamespace
    row = SimpleNamespace(
        autonomy="autonomous",
        promotion_expires_at=None,
        competency={
            "ucip:execution.python": {
                "competency": 0.1, "samples": 1,
                # forged stamp must NOT grant autonomy
                "autonomy_level": "full_autonomous",
                "autonomy_override": "full_autonomous",
            }
        },
    )
    level = capability_autonomy_level(row, "ucip:execution.python")
    assert level == "supervised"

def test_loop_uses_pipeline():
    src = open("api/routes/loop.py").read()
    assert "run_brain_loop" in src
    assert "execution_pipeline" in src

def test_workers_runtime_uses_snapshot_and_job():
    src = open("workers/runtime.py").read()
    assert "snapshot_trust" in src
    assert "enqueue" in src
    assert "WorkerTrustUnavailable" in src

def test_coordinator_requires_tenant_for_workers():
    src = open("cognitive/coordinator.py").read()
    assert "tenant_id" in src
    assert "WorkerRuntime().run" in src

def test_script_runner_evidence_for_sandbox():
    src = open("execution/script_runner.py").read()
    assert "record_path_evidence" in src or "use_sandbox" in src

def test_pipeline_module_exports():
    from governance import execution_pipeline as ep
    assert hasattr(ep, "run_brain_loop")
    assert hasattr(ep, "resolve_human_identity")
    assert hasattr(ep, "run_sandboxed_code")
    assert hasattr(ep, "record_path_evidence")
