"""execution_operations must gain actor_id and related ORM columns via migration."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = (ROOT / "core" / "database.py").read_text()
BASE = ROOT / "supabase" / "migrations" / "20260915130000_devos_single_source_of_truth.sql"
ALIGN = ROOT / "supabase" / "migrations" / "20260916210000_execution_operations_orm_alignment.sql"


def test_orm_execution_operation_has_actor_id():
    block = DB.split("class ExecutionOperation", 1)[1].split("class WorkerTrustRecord", 1)[0]
    assert "actor_id:" in block
    assert "nullable=True" in block.split("actor_id", 1)[1].split("\n", 1)[0] or "Optional[str]" in block


def test_base_migration_execution_operations_lacks_actor_id():
    text = BASE.read_text()
    start = text.find("CREATE TABLE IF NOT EXISTS execution_operations")
    assert start >= 0
    end = text.find("CREATE TABLE IF NOT EXISTS", start + 10)
    block = text[start:end]
    assert "owner_id" in block
    assert "actor_id" not in block


def test_alignment_migration_adds_actor_id_and_related():
    assert ALIGN.is_file()
    text = ALIGN.read_text()
    assert "ADD COLUMN IF NOT EXISTS actor_id TEXT NULL" in text
    assert "ADD COLUMN IF NOT EXISTS task_id TEXT NULL" in text
    assert "ADD COLUMN IF NOT EXISTS tool_name TEXT NULL" in text
    assert "ADD COLUMN IF NOT EXISTS request_id TEXT NULL" in text
    assert "ADD COLUMN IF NOT EXISTS correlation_id TEXT NULL" in text
    assert "ADD COLUMN IF NOT EXISTS attempt INTEGER" in text
    assert "DROP TABLE" not in text.upper()


def test_skipped_base_migration_does_not_imply_actor_id():
    """schema_migrations listing base file ≠ ALTER; alignment migration required."""
    assert ALIGN.name > BASE.name


def test_idempotency_unique_migration_exists():
    mig = ROOT / "supabase" / "migrations" / "20260918160000_execution_operations_idempotency_unique.sql"
    assert mig.is_file()
    text = mig.read_text()
    assert "ux_execution_operations_idempotency" in text
    assert "CREATE UNIQUE INDEX" in text
    assert "COALESCE(tenant_id" in text
    assert "WHERE idempotency_key IS NOT NULL" in text
    assert "DROP TABLE" not in text.upper()
    # Fail closed on existing duplicates — no silent delete
    assert "duplicate" in text.lower()
    assert "RAISE EXCEPTION" in text


def test_orm_init_db_creates_idempotency_unique_index():
    """init_db bootstrap must create the same logical unique index."""
    assert "ux_execution_operations_idempotency" in DB
    assert "CREATE UNIQUE INDEX" in DB
