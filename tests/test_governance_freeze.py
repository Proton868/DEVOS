"""Governance v1 freeze — architectural invariant, not implementation trivia.

AI_EFFECT
    MUST_HAVE identity
    MUST_HAVE capability_authority
    MUST_HAVE isolation
    MUST_HAVE PathClass
    IF durable → MUST_HAVE ExecutionJob
    IF durable → MUST_HAVE Evidence
"""
import pathlib

from governance.reliability import AI_EFFECT_REQUIREMENTS, scrub_secrets, new_idempotency_key


def test_ai_effect_invariant_documented():
    assert set(AI_EFFECT_REQUIREMENTS) >= {
        "identity",
        "capability_authority",
        "isolation",
        "path_class",
        "execution_job_if_durable",
        "evidence_if_durable",
    }


def test_durable_paths_have_job_and_evidence():
    """Durable AI surfaces must reference job + evidence creation."""
    durable_sources = {
        "workers/runtime.py": ["enqueue", "snapshot_trust"],
        "governance/execution_pipeline.py": ["begin_execution_job", "record_path_evidence"],
        "execution/script_runner.py": ["begin_execution_job", "record_path_evidence", "require_authority"],
        "brain/autoresearch.py": ["begin_execution_job", "record_path_evidence"],
        "api/routes/marketplace.py": ["begin_execution_job", "record_path_evidence", "CAP_PACKAGE_INSTALL"],
    }
    for path, needles in durable_sources.items():
        src = open(path).read()
        for n in needles:
            assert n in src, f"{path} missing {n}"


def test_path_class_covers_exceptions():
    from governance.execution_pipeline import PathClass
    vals = {p.value for p in PathClass}
    assert "durable" in vals and "human_only" in vals
    assert "non_durable" in vals and "read_only" in vals


def test_package_install_in_registry():
    from governance.capability_registry import CapabilityRegistry
    desc = CapabilityRegistry().get("ucip:package.install")
    assert desc is not None
    assert getattr(desc, "requires_network", False) is True


def test_package_install_in_action_map_and_trust():
    from governance.ucip import ACTION_TO_CAP, TRUST_LEVEL_CAPS, TrustLevel
    assert ACTION_TO_CAP.get("install_packages") == "ucip:package.install"
    assert "ucip:package.install" in TRUST_LEVEL_CAPS[TrustLevel.OPERATOR]


def test_no_brain_loop_bypass():
    allowed = {
        "governance/execution_pipeline.py",
        "workers/runtime.py",
        "cognitive/coordinator.py",
        "core/loop.py",
    }
    for path in pathlib.Path(".").rglob("*.py"):
        if "test_" in path.name or "node_modules" in str(path):
            continue
        if "BrainExecutionLoop(" in path.read_text(errors="ignore"):
            assert any(str(path).endswith(a) or a in str(path) for a in allowed), path


def test_secrets_scrubbed_from_structures():
    dirty = {
        "goal": "ok",
        "api_key": "sk-abcdefghijklmnopqrstuvwxyz",
        "nested": {"password": "hunter2", "token": "ghp_XXXXXXXXXXXXXXXXXXXX"},
        "stdout": "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.abc",
    }
    clean = scrub_secrets(dirty)
    assert clean["goal"] == "ok"
    assert clean["api_key"] == "***REDACTED***"
    assert clean["nested"]["password"] == "***REDACTED***"
    assert "REDACTED" in clean["nested"]["token"]
    assert "REDACTED" in str(clean["stdout"])


def test_idempotency_key_stable():
    a = new_idempotency_key(
        tenant_id="t1", actor_id="u1", capability="ucip:execution.python",
        operation="script_run", body={"script_id": "s1"},
    )
    b = new_idempotency_key(
        tenant_id="t1", actor_id="u1", capability="ucip:execution.python",
        operation="script_run", body={"script_id": "s1"},
    )
    c = new_idempotency_key(
        tenant_id="t1", actor_id="u1", capability="ucip:execution.python",
        operation="script_run", body={"script_id": "s2"},
    )
    assert a == b and a != c


def test_evidence_and_jobs_import_scrub():
    assert "scrub_secrets" in open("governance/execution_pipeline.py").read()
    assert "scrub_secrets" in open("workers/job_queue.py").read()
