"""Nuha/chat provider resolution — DEFAULT_PROVIDER, never hard-coded ollama."""
from __future__ import annotations

import pytest
from brain.llm import resolve_chat_provider
from core.config import settings


def test_resolve_empty_uses_default_provider():
    assert resolve_chat_provider(None, "", None) == settings.DEFAULT_PROVIDER


def test_default_provider_is_omniroute():
    assert settings.DEFAULT_PROVIDER == "omniroute"


def test_explicit_provider_wins():
    assert resolve_chat_provider("openrouter", "ollama") == "openrouter"
    assert resolve_chat_provider(None, "deepseek") == "deepseek"


def test_session_provider_wins_over_default():
    assert resolve_chat_provider(None, "gemini") == "gemini"


def test_whitespace_treated_as_empty():
    assert resolve_chat_provider("  ", None) == settings.DEFAULT_PROVIDER


def test_no_ollama_hidden_fallback():
    # When DEFAULT is omniroute, empty chain must not become ollama
    assert resolve_chat_provider() != "ollama"
    assert resolve_chat_provider(None, "") == "omniroute"


def test_chat_py_has_no_ollama_string_fallback():
    from pathlib import Path
    src = Path(__file__).resolve().parents[1] / "api" / "routes" / "chat.py"
    text = src.read_text()
    assert 'or "ollama"' not in text
    assert "provider=\"ollama\"" not in text
    assert "resolve_chat_provider" in text
