"""Regression: chat_sessions columns must exist in migrations matching ORM."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = (ROOT / "core" / "database.py").read_text()
MODE_SQL = ROOT / "supabase" / "migrations" / "20260916180000_chat_sessions_mode_alignment.sql"
NODE_SQL = ROOT / "supabase" / "migrations" / "20260916190000_chat_sessions_node_workflow_alignment.sql"


def test_chat_session_model_has_mode():
    assert "class ChatSession" in DB
    assert "mode: Mapped" in DB
    assert 'default="chat"' in DB


def test_chat_session_model_has_node_and_workflow_columns():
    assert "class ChatSession" in DB
    assert "node_id: Mapped[Optional[str]]" in DB
    assert "workflow_id: Mapped[Optional[str]]" in DB
    assert "mapped_column(String, nullable=True)" in DB
    # Unbounded String (not String(n)) — no length on either column
    chat_block = DB.split("class ChatSession", 1)[1].split("class Message", 1)[0]
    assert "node_id:" in chat_block
    assert "workflow_id:" in chat_block
    assert "nullable=True" in chat_block
    assert "index=True" not in chat_block.split("node_id", 1)[1].split("\n", 1)[0]
    assert "ForeignKey" not in chat_block.split("node_id", 1)[1].split("\n", 1)[0]
    assert "ForeignKey" not in chat_block.split("workflow_id", 1)[1].split("\n", 1)[0]


def test_migration_adds_chat_sessions_mode():
    assert MODE_SQL.is_file()
    text = MODE_SQL.read_text()
    assert "chat_sessions" in text
    assert "mode" in text
    assert "IF NOT EXISTS" in text
    assert "DROP TABLE" not in text.upper()
    assert "DELETE FROM" not in text.upper()
    assert "DEFAULT 'chat'" in text


def test_migration_adds_chat_sessions_node_id_and_workflow_id():
    assert NODE_SQL.is_file()
    assert NODE_SQL.name > MODE_SQL.name
    text = NODE_SQL.read_text()
    assert "chat_sessions" in text
    assert "ADD COLUMN IF NOT EXISTS node_id TEXT NULL" in text
    assert "ADD COLUMN IF NOT EXISTS workflow_id TEXT NULL" in text
    assert "DROP TABLE" not in text.upper()
    assert "DELETE FROM" not in text.upper()
    assert "DROP COLUMN" not in text.upper()


def test_mode_alignment_migration_not_rewritten_for_node_columns():
    text = MODE_SQL.read_text()
    assert "node_id" not in text
    assert "workflow_id" not in text
