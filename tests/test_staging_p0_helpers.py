"""Unit tests for P0 staging drill helpers (NOT a live staging drill)."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "staging_p0_worker_kill.py"


def _load():
    spec = importlib.util.spec_from_file_location("staging_p0_worker_kill", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader
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
