"""Unit tests for P0 staging harness helpers (NOT a live staging drill)."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "staging_p0_worker_kill.py"


def _load():
    import sys
    spec = importlib.util.spec_from_file_location("staging_p0_worker_kill", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader
    sys.modules[spec.name] = mod  # required for @dataclass under importlib
    spec.loader.exec_module(mod)
    return mod


def test_is_postgresql_url_accepts_asyncpg():
    m = _load()
    assert m.is_postgresql_url("postgresql+asyncpg://u:p@localhost/db") is True
    assert m.is_postgresql_url("postgres://u:p@localhost/db") is True


def test_is_postgresql_url_rejects_sqlite():
    m = _load()
    assert m.is_postgresql_url("sqlite+aiosqlite:///./data/devos.db") is False
    assert m.is_postgresql_url("") is False


def test_require_postgresql_blocks_sqlite():
    m = _load()
    with pytest.raises(m.InfrastructureBlocked):
        m.require_postgresql("sqlite+aiosqlite:///tmp.db")


def test_results_dir_constant():
    m = _load()
    assert m.RESULTS_DIR.name == "staging-results"


def test_classify_outcome_blocked_fail_pass():
    m = _load()
    assert m.classify_outcome(blocked=True) == "BLOCKED"
    assert m.classify_outcome(failed=True) == "FAIL"
    assert m.classify_outcome() == "PASS"
    # blocked wins over failed
    assert m.classify_outcome(blocked=True, failed=True) == "BLOCKED"


def test_evaluate_expected_recovery_pass():
    m = _load()
    ok, errs = m.evaluate_expected_recovery(
        initial_attempt=1,
        recovery_attempt=2,
        recovery_worker="worker-b:xyz",
        killed_worker="worker-a",
        final_status="succeeded",
        operation_status="succeeded",
        job_rows=1,
        operation_rows=1,
        evidence_rows=0,
        blind_unknown_retry=False,
    )
    assert ok is True
    assert errs == []


def test_evaluate_expected_recovery_fail_same_worker():
    m = _load()
    ok, errs = m.evaluate_expected_recovery(
        initial_attempt=1,
        recovery_attempt=2,
        recovery_worker="worker-a:abc",
        killed_worker="worker-a",
        final_status="succeeded",
        operation_status="succeeded",
        job_rows=1,
        operation_rows=1,
        evidence_rows=0,
        blind_unknown_retry=False,
    )
    assert ok is False
    assert any("recovery_worker" in e for e in errs)


def test_evaluate_expected_recovery_fail_attempts():
    m = _load()
    ok, errs = m.evaluate_expected_recovery(
        initial_attempt=1,
        recovery_attempt=1,
        recovery_worker="worker-b",
        killed_worker="worker-a",
        final_status="succeeded",
        operation_status="succeeded",
        job_rows=1,
        operation_rows=1,
        evidence_rows=0,
        blind_unknown_retry=False,
    )
    assert ok is False
    assert any("recovery_attempt" in e for e in errs)


def test_evaluate_expected_recovery_reject_blind_unknown():
    m = _load()
    ok, errs = m.evaluate_expected_recovery(
        initial_attempt=1,
        recovery_attempt=2,
        recovery_worker="worker-b",
        killed_worker="worker-a",
        final_status="succeeded",
        operation_status="unknown",
        job_rows=1,
        operation_rows=1,
        evidence_rows=0,
        blind_unknown_retry=True,
    )
    assert ok is False


def test_phase_failure_to_dict():
    m = _load()
    f = m.PhaseFailure(
        phase=m.PHASE_CLAIM,
        expected="running",
        actual="queued",
        elapsed_seconds=1.5,
        last_observed_state={"status": "queued"},
        detail="timeout",
    )
    d = f.to_dict()
    assert d["phase"] == "CLAIM"
    assert d["elapsed_seconds"] == 1.5
    assert "last_observed_state" in d


def test_worker_label_from_id():
    m = _load()
    assert m.worker_label_from_id("worker-a:host:1") == "worker-a"
    assert m.worker_label_from_id("worker-b:host:2") == "worker-b"
