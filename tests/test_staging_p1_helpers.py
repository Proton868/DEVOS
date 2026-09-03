"""Unit tests for P1 staging helpers (not a live drill)."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "staging_p1_unknown_reconciliation.py"


def _load():
    spec = importlib.util.spec_from_file_location("staging_p1", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    assert spec.loader
    spec.loader.exec_module(mod)
    return mod


def test_classify_outcome():
    m = _load()
    assert m.classify_outcome(blocked=True) == "BLOCKED"
    assert m.classify_outcome(failed=True) == "FAIL"
    assert m.classify_outcome() == "PASS"


def test_timeout_never_pass():
    m = _load()
    # timeouts are FAIL or BLOCKED via exceptions, never classify_outcome alone as pass
    assert m.classify_outcome(failed=True) != "PASS"


def test_evaluate_p1_pass():
    m = _load()
    ok, errs = m.evaluate_p1_expected(
        provider_accepted=True,
        provider_side_effect_count=1,
        provider_execution_count_before=1,
        provider_execution_count_final=1,
        unknown_observed=True,
        blind_retry=False,
        final_operation_status="succeeded",
        final_job_status="succeeded",
        job_rows=1,
        operation_rows=1,
    )
    assert ok and errs == []


def test_evaluate_p1_fail_double_execution():
    m = _load()
    ok, errs = m.evaluate_p1_expected(
        provider_accepted=True,
        provider_side_effect_count=2,
        provider_execution_count_before=2,
        provider_execution_count_final=2,
        unknown_observed=True,
        blind_retry=True,
        final_operation_status="succeeded",
        final_job_status="succeeded",
        job_rows=1,
        operation_rows=1,
    )
    assert not ok


def test_evaluate_p1_fail_no_unknown():
    m = _load()
    ok, errs = m.evaluate_p1_expected(
        provider_accepted=True,
        provider_side_effect_count=1,
        provider_execution_count_before=1,
        provider_execution_count_final=1,
        unknown_observed=False,
        blind_retry=False,
        final_operation_status="succeeded",
        final_job_status="succeeded",
        job_rows=1,
        operation_rows=1,
    )
    assert not ok
    assert any("UNKNOWN" in e for e in errs)


def test_is_postgresql():
    m = _load()
    assert m.is_postgresql_url("postgresql+asyncpg://x") is True
    assert m.is_postgresql_url("sqlite:///x") is False


def test_phase_failure_schema():
    m = _load()
    f = m.PhaseFailure("NO_RETRY_WINDOW", "count=1", "count=2", 5.2, {"x": 1}, "blind retry")
    d = f.to_dict()
    assert d["phase"] == "NO_RETRY_WINDOW"
    assert d["elapsed_seconds"] == 5.2
