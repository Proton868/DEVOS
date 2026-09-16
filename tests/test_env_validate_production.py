"""Production env_validate fail-closed rules (no secrets printed)."""
from __future__ import annotations

import os
from ops.env_validate import validate


def _base_prod_env(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@localhost/db")
    monkeypatch.setenv("REQUIRE_POSTGRES", "true")
    monkeypatch.setenv("JWT_SECRET", "x" * 40)
    monkeypatch.setenv("DEBUG", "false")
    monkeypatch.setenv("AUTH_MODE", "dual")
    monkeypatch.setenv("DEFAULT_PROVIDER", "omniroute")
    monkeypatch.setenv("ADMIN_PASSWORD", "not-a-default-password-99")
    monkeypatch.delenv("DEVOS_ORCH_FAKE_RUNTIME", raising=False)
    monkeypatch.delenv("DEVOS_ALLOW_FAKE_RUNTIME", raising=False)
    monkeypatch.delenv("DEVOS_ALLOW_WEBSITE_SCAFFOLD_FALLBACK", raising=False)


def test_production_ok(monkeypatch):
    _base_prod_env(monkeypatch)
    errors, _ = validate(production=True)
    assert errors == [], errors


def test_production_rejects_sqlite(monkeypatch):
    _base_prod_env(monkeypatch)
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///./data/x.db")
    errors, _ = validate(production=True)
    assert any("SQLite" in e or "Postgres" in e for e in errors)


def test_production_rejects_fake_runtime(monkeypatch):
    _base_prod_env(monkeypatch)
    monkeypatch.setenv("DEVOS_ORCH_FAKE_RUNTIME", "1")
    errors, _ = validate(production=True)
    assert any("FAKE_RUNTIME" in e for e in errors)


def test_production_rejects_scaffold_fallback(monkeypatch):
    _base_prod_env(monkeypatch)
    monkeypatch.setenv("DEVOS_ALLOW_WEBSITE_SCAFFOLD_FALLBACK", "1")
    errors, _ = validate(production=True)
    assert any("SCAFFOLD" in e for e in errors)


def test_production_rejects_debug(monkeypatch):
    _base_prod_env(monkeypatch)
    monkeypatch.setenv("DEBUG", "true")
    errors, _ = validate(production=True)
    assert any("DEBUG" in e for e in errors)


def test_production_empty_admin_password_warns_not_errors(monkeypatch):
    """Empty ADMIN_PASSWORD is allowed — first boot auto-generates."""
    _base_prod_env(monkeypatch)
    monkeypatch.setenv("ADMIN_PASSWORD", "")
    errors, warnings = validate(production=True)
    assert errors == [], errors
    assert any("ADMIN_PASSWORD empty" in w for w in warnings)


def test_production_rejects_weak_explicit_admin_password(monkeypatch):
    _base_prod_env(monkeypatch)
    monkeypatch.setenv("ADMIN_PASSWORD", "admin")
    errors, _ = validate(production=True)
    assert any("ADMIN_PASSWORD" in e for e in errors)
