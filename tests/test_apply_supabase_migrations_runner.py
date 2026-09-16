"""Unit tests for scripts/apply_supabase_migrations.py (direct psycopg runner)."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "apply_supabase_migrations.py"


def _load():
    spec = importlib.util.spec_from_file_location("apply_supabase_migrations", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_module_imports_and_compiles():
    mod = _load()
    assert callable(mod.normalize_database_url)
    assert callable(mod.apply_migrations)
    assert callable(mod._split_sql)
    assert callable(mod.main)


@pytest.mark.parametrize(
    "raw,expected_prefix",
    [
        ("postgresql+psycopg://u:p@h/db", "postgresql://u:p@h/db"),
        ("postgresql+psycopg2://u:p@h/db", "postgresql://u:p@h/db"),
        ("postgresql+asyncpg://u:p@h/db", "postgresql://u:p@h/db"),
        ("POSTGRESQL+ASYNCPG://u:p@h/db", "postgresql://u:p@h/db"),
        ("postgresql://u:p@h/db", "postgresql://u:p@h/db"),
        ("postgres://u:p@h/db", "postgres://u:p@h/db"),
    ],
)
def test_normalize_database_url(raw, expected_prefix):
    mod = _load()
    assert mod.normalize_database_url(raw) == expected_prefix


def test_split_sql_respects_dollar_quotes():
    mod = _load()
    sql = "SELECT 1; CREATE FUNCTION f() RETURNS void AS $$ BEGIN SELECT 2; END; $$ LANGUAGE plpgsql;"
    parts = mod._split_sql(sql)
    assert any("SELECT 1" in p for p in parts)
    assert any("$$" in p for p in parts)


def test_apply_migrations_skips_already_recorded(tmp_path):
    mod = _load()
    mig = tmp_path / "migrations"
    mig.mkdir()
    (mig / "20200101000000_a.sql").write_text("SELECT 1;")

    cur = MagicMock()
    # first SELECT finds existing row → skip
    cur.fetchone.return_value = (1,)
    conn = MagicMock()
    conn.cursor.return_value.__enter__.return_value = cur
    conn.cursor.return_value.__exit__.return_value = None

    fake_psycopg = MagicMock()
    fake_psycopg.connect.return_value = conn

    with patch.dict(sys.modules, {"psycopg": fake_psycopg}):
        applied, skipped = mod.apply_migrations(
            "postgresql+asyncpg://u:p@localhost/db",
            mig_dir=mig,
        )
    assert applied == 0
    assert skipped == 1
    # INSERT into schema_migrations must not run for skipped files
    exec_sqls = [c.args[0] for c in cur.execute.call_args_list if c.args]
    assert not any(
        isinstance(s, str) and "INSERT INTO schema_migrations" in s for s in exec_sqls
    )
    conn.close.assert_called()


def test_apply_migrations_records_on_success(tmp_path):
    mod = _load()
    mig = tmp_path / "migrations"
    mig.mkdir()
    (mig / "20200101000000_b.sql").write_text(
        "ALTER TABLE public.t ADD COLUMN IF NOT EXISTS x TEXT NULL;"
    )

    cur = MagicMock()
    cur.fetchone.return_value = None  # not yet applied

    conn = MagicMock()
    conn.cursor.return_value.__enter__.return_value = cur
    conn.cursor.return_value.__exit__.return_value = None

    fake_psycopg = MagicMock()
    fake_psycopg.connect.return_value = conn

    with patch.dict(sys.modules, {"psycopg": fake_psycopg}):
        applied, skipped = mod.apply_migrations(
            "postgresql+psycopg://u:p@localhost/db",
            mig_dir=mig,
        )
    assert applied == 1
    assert skipped == 0
    exec_sqls = [c.args[0] for c in cur.execute.call_args_list if c.args]
    assert any(
        isinstance(s, str) and "INSERT INTO schema_migrations" in s for s in exec_sqls
    )
    assert any(
        isinstance(s, str) and "schema_migrations" in s and "CREATE TABLE" in s
        for s in exec_sqls
    )
    # commit after success
    assert conn.commit.call_count >= 2  # tracking + migration
    conn.close.assert_called()


def test_apply_migrations_failure_rolls_back_and_does_not_record(tmp_path):
    mod = _load()
    mig = tmp_path / "migrations"
    mig.mkdir()
    (mig / "20200101000000_fail.sql").write_text("SELECT boom;")

    cur = MagicMock()
    cur.fetchone.return_value = None

    def execute_side_effect(sql, params=None):
        if isinstance(sql, str) and "CREATE TABLE IF NOT EXISTS schema_migrations" in sql:
            return None
        if isinstance(sql, str) and "SELECT 1 FROM schema_migrations" in sql:
            return None
        if isinstance(sql, str) and "SELECT boom" in sql:
            raise RuntimeError("ddl failed")
        if isinstance(sql, str) and "INSERT INTO schema_migrations" in sql:
            raise AssertionError("must not record failed migration")
        # whole-file execute of migration content
        if isinstance(sql, str) and "boom" in sql:
            raise RuntimeError("ddl failed")
        return None

    cur.execute.side_effect = execute_side_effect

    conn = MagicMock()
    conn.cursor.return_value.__enter__.return_value = cur
    conn.cursor.return_value.__exit__.return_value = None

    fake_psycopg = MagicMock()
    fake_psycopg.connect.return_value = conn

    with patch.dict(sys.modules, {"psycopg": fake_psycopg}):
        with pytest.raises(RuntimeError, match="ddl failed"):
            mod.apply_migrations(
                "postgresql://u:p@localhost/db",
                mig_dir=mig,
            )
    conn.rollback.assert_called()
    conn.close.assert_called()
    # ensure no successful INSERT recorded
    for c in cur.execute.call_args_list:
        if c.args and isinstance(c.args[0], str) and "INSERT INTO schema_migrations" in c.args[0]:
            pytest.fail("failed migration was recorded")


def test_no_sqlalchemy_imports_in_source():
    text = SCRIPT.read_text(encoding="utf-8")
    # No runtime dependency on SQLAlchemy / sync_session
    assert "from sqlalchemy" not in text
    assert "import sqlalchemy" not in text
    assert "core.sync_session" not in text
    assert "sync_engine" not in text
    assert "async def" not in text
    assert "import psycopg" in text
    assert "%s" in text
