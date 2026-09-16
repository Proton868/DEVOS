"""ops/env_validate.py — secret-safe production rules."""
from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load():
    path = ROOT / "ops" / "env_validate.py"
    spec = importlib.util.spec_from_file_location("ops_env_validate", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def test_production_rejects_sqlite(monkeypatch):
    mod = _load()
    monkeypatch.setenv("DATABASE_URL", "sqlite:///./data/x.db")
    monkeypatch.setenv("REQUIRE_POSTGRES", "true")
    monkeypatch.setenv("JWT_SECRET", "x" * 40)
    monkeypatch.setenv("DEBUG", "false")
    monkeypatch.setenv("ADMIN_PASSWORD", "strong-password-here")
    errors, _ = mod.validate(production=True)
    assert any("SQLite" in e or "sqlite" in e.lower() or "forbids" in e for e in errors)


def test_production_rejects_debug(monkeypatch):
    mod = _load()
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@localhost/db")
    monkeypatch.setenv("REQUIRE_POSTGRES", "true")
    monkeypatch.setenv("JWT_SECRET", "x" * 40)
    monkeypatch.setenv("DEBUG", "true")
    monkeypatch.setenv("ADMIN_PASSWORD", "strong-password-here")
    errors, _ = mod.validate(production=True)
    assert any("DEBUG" in e for e in errors)


def test_production_ok_postgres(monkeypatch):
    mod = _load()
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@localhost/db")
    monkeypatch.setenv("REQUIRE_POSTGRES", "true")
    monkeypatch.setenv("JWT_SECRET", "x" * 40)
    monkeypatch.setenv("DEBUG", "false")
    monkeypatch.setenv("ADMIN_PASSWORD", "strong-password-here")
    monkeypatch.setenv("DEFAULT_PROVIDER", "omniroute")
    errors, warnings = mod.validate(production=True)
    assert errors == []


def test_dev_allows_sqlite_with_warning(monkeypatch):
    mod = _load()
    monkeypatch.setenv("DATABASE_URL", "sqlite:///./data/x.db")
    monkeypatch.setenv("REQUIRE_POSTGRES", "false")
    monkeypatch.setenv("JWT_SECRET", "weak")
    monkeypatch.setenv("DEBUG", "true")
    errors, warnings = mod.validate(production=False)
    # may still warn; should not hard-fail solely on sqlite when require_pg false
    assert not any("Production must not use SQLite" in e for e in errors)
