"""Regression: messages.metadata must exist in migrations matching ORM Message."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = (ROOT / "core" / "database.py").read_text()
BASE_SQL = ROOT / "supabase" / "migrations" / "20260915130000_devos_single_source_of_truth.sql"
META_SQL = ROOT / "supabase" / "migrations" / "20260916200000_messages_metadata_alignment.sql"
NODE_SQL = ROOT / "supabase" / "migrations" / "20260916190000_chat_sessions_node_workflow_alignment.sql"


def test_message_model_maps_metadata_column():
    assert "class Message" in DB
    assert 'mapped_column("metadata", JSON)' in DB
    assert "metadata_: Mapped[Optional[dict]]" in DB
    block = DB.split("class Message", 1)[1].split("class Script", 1)[0]
    assert "__tablename__ = \"messages\"" in block
    assert "metadata_" in block


def test_base_migration_messages_table_lacks_metadata():
    """Documents historical gap: base CREATE TABLE messages has no metadata."""
    assert BASE_SQL.is_file()
    text = BASE_SQL.read_text()
    # Isolate messages CREATE TABLE body
    start = text.find("CREATE TABLE IF NOT EXISTS messages")
    assert start >= 0
    end = text.find("CREATE TABLE IF NOT EXISTS", start + 10)
    block = text[start:end]
    assert "session_id" in block
    assert "metadata" not in block


def test_alignment_migration_adds_messages_metadata():
    assert META_SQL.is_file()
    assert META_SQL.name > NODE_SQL.name
    text = META_SQL.read_text()
    assert "messages" in text
    assert "ADD COLUMN IF NOT EXISTS metadata JSONB NULL" in text
    assert "DROP TABLE" not in text.upper()
    assert "DELETE FROM" not in text.upper()
    assert "DROP COLUMN" not in text.upper()


def test_apply_migrations_script_includes_supabase_runner():
    script = (ROOT / "ops" / "apply_migrations.sh").read_text()
    assert "apply_supabase_migrations.py" in script
    assert "supabase/migrations" in script or "MIG" in (ROOT / "scripts" / "apply_supabase_migrations.py").read_text()
    runner = (ROOT / "scripts" / "apply_supabase_migrations.py").read_text()
    assert "schema_migrations" in runner
    assert "MIG_DIR" in runner or "migrations" in runner
