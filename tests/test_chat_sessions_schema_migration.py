"""Regression: chat_sessions.mode must exist in migrations matching ORM."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_chat_session_model_has_mode():
    src = (ROOT / "core" / "database.py").read_text()
    assert "class ChatSession" in src
    assert "mode: Mapped" in src
    assert 'default="chat"' in src


def test_migration_adds_chat_sessions_mode():
    dedicated = ROOT / "supabase" / "migrations" / "20260916180000_chat_sessions_mode_alignment.sql"
    assert dedicated.is_file()
    text = dedicated.read_text()
    assert "chat_sessions" in text
    assert "mode" in text
    assert "IF NOT EXISTS" in text
    assert "DROP TABLE" not in text.upper()
    assert "DELETE FROM" not in text.upper()
    assert "DEFAULT 'chat'" in text
