"""Database lifecycle: migrations authority, no production create_all leak."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MIG = ROOT / "supabase" / "migrations"


def test_migrations_are_ordered_and_unique():
    files = sorted(p.name for p in MIG.glob("*.sql"))
    assert files, "expected supabase/migrations/*.sql"
    assert files == sorted(files)
    assert len(files) == len(set(files))
    # timestamps prefix
    for name in files:
        assert name[:8].isdigit(), name


def test_outbox_idempotency_migration_present():
    text = "\n".join(p.read_text() for p in MIG.glob("*.sql"))
    assert "idempotency_key" in text
    assert "outbox_events" in text


def test_no_drop_table_in_forward_migrations():
    """Forward migrations must not DROP TABLE (destructive)."""
    for p in MIG.glob("*.sql"):
        body = p.read_text().upper()
        # allow comments mentioning DROP
        for line in p.read_text().splitlines():
            s = line.strip()
            if s.startswith("--"):
                continue
            assert "DROP TABLE" not in s.upper(), f"{p.name}: {s}"


@pytest.mark.asyncio
async def test_init_db_postgres_skips_create_all(monkeypatch):
    """When dialect is postgresql, init_db must not call create_all unless allowed."""
    # This test only runs logic if we can monkeypatch engine dialect
    from core import database as dbmod

    class _FakeResult:
        def fetchone(self):
            return (1,)

    class _FakeConn:
        dialect = type("D", (), {"name": "postgresql"})()

        async def execute(self, *a, **k):
            return _FakeResult()

        async def run_sync(self, fn):
            raise AssertionError("create_all/run_sync must not run on postgres without allow")

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    class _FakeEngine:
        dialect = type("D", (), {"name": "postgresql"})()

        def begin(self):
            return _FakeConn()

    monkeypatch.setattr(dbmod, "engine", _FakeEngine())
    monkeypatch.delenv("DEVOS_SCHEMA_CREATE_ALL", raising=False)
    await dbmod.init_db()


def test_apply_supabase_script_exists():
    assert (ROOT / "scripts" / "apply_supabase_migrations.py").is_file()
