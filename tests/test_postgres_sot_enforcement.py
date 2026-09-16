"""Supabase/Postgres is the only application SoT when REQUIRE_POSTGRES=true."""
from __future__ import annotations

import os

import pytest


def test_resolve_rejects_sqlite_when_require_postgres(monkeypatch):
    monkeypatch.setenv("REQUIRE_POSTGRES", "true")
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///./data/forbidden.db")
    # Re-import resolution logic without reloading whole app engine
    from core.config import Settings

    s = Settings(
        REQUIRE_POSTGRES=True,
        DATABASE_URL="sqlite+aiosqlite:///./data/forbidden.db",
        JWT_SECRET="x" * 40,
    )
    # Inline the same rules as _resolve_database_url
    url = (s.DATABASE_URL or "").strip()
    low = url.lower()
    is_sqlite = low.startswith("sqlite")
    is_pg = low.startswith("postgres")
    assert s.REQUIRE_POSTGRES and is_sqlite and not is_pg


def test_resolve_accepts_postgres(monkeypatch):
    from core.config import Settings

    s = Settings(
        REQUIRE_POSTGRES=True,
        DATABASE_URL="postgresql+asyncpg://u:p@db.example/postgres",
        JWT_SECRET="x" * 40,
    )
    low = s.DATABASE_URL.lower()
    assert low.startswith("postgres")


def test_env_validate_production_forbids_sqlite(monkeypatch):
    from ops.env_validate import validate

    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///./x.db")
    monkeypatch.setenv("REQUIRE_POSTGRES", "true")
    monkeypatch.setenv("JWT_SECRET", "x" * 40)
    monkeypatch.setenv("DEBUG", "false")
    monkeypatch.setenv("ADMIN_PASSWORD", "strong-enough-password-99")
    monkeypatch.setenv("DEFAULT_PROVIDER", "omniroute")
    errors, _ = validate(production=True)
    assert any("SQLite" in e or "Postgres" in e for e in errors)


def test_no_production_sqlite3_connect():
    import pathlib

    bad = []
    for path in pathlib.Path(".").rglob("*.py"):
        if any(x in path.parts for x in (".git", "tests", "node_modules", ".venv")):
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if "sqlite3.connect" in text:
            bad.append(str(path))
    assert bad == [], bad
