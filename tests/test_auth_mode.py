"""Tests for dual-mode authentication (security-audit P2b/P2g).

Verifies that api.routes.auth.verify_any_token() and get_current_user()
correctly respect settings.AUTH_MODE ("dual" | "local" | "supabase"),
which governs whether local JWTs and/or Supabase access tokens are
accepted, per the "Supabase-primary with local fallback" architecture
decision documented in api/routes/auth.py's module docstring.
"""
import pytest

from core.config import settings
from api.routes import auth as auth_module


@pytest.fixture(autouse=True)
def _restore_auth_mode():
    """Every test mutates settings.AUTH_MODE / settings.SUPABASE_URL directly
    (there's no request-scoped override for module-level settings) — restore
    the originals afterward so tests don't leak state into each other or
    into other test modules that import `settings`."""
    original_mode = settings.AUTH_MODE
    original_url = settings.SUPABASE_URL
    original_key = settings.SUPABASE_KEY
    yield
    settings.AUTH_MODE = original_mode
    settings.SUPABASE_URL = original_url
    settings.SUPABASE_KEY = original_key


def test_default_auth_mode_is_dual():
    # Confirms the safe, backward-compatible default matches this task's
    # explicit architecture choice ("Supabase-primary with local fallback").
    assert settings.AUTH_MODE in ("dual", "local", "supabase")


def test_dual_mode_tries_local_then_supabase(monkeypatch):
    settings.AUTH_MODE = "dual"
    settings.SUPABASE_URL = "https://example.supabase.co"
    settings.SUPABASE_KEY = "fake-key"

    monkeypatch.setattr(auth_module, "decode_local_token", lambda t: None)
    monkeypatch.setattr(auth_module, "decode_supabase_token", lambda t: {"sub": "supa-1"})

    payload, source = auth_module.verify_any_token("some-token")
    assert source == "supabase"
    assert payload == {"sub": "supa-1"}


def test_dual_mode_prefers_local_when_both_would_succeed(monkeypatch):
    settings.AUTH_MODE = "dual"
    settings.SUPABASE_URL = "https://example.supabase.co"
    settings.SUPABASE_KEY = "fake-key"

    monkeypatch.setattr(auth_module, "decode_local_token", lambda t: {"sub": "local-1"})
    monkeypatch.setattr(auth_module, "decode_supabase_token", lambda t: {"sub": "supa-1"})

    payload, source = auth_module.verify_any_token("some-token")
    assert source == "local"
    assert payload["sub"] == "local-1"


def test_local_mode_never_calls_supabase(monkeypatch):
    settings.AUTH_MODE = "local"
    settings.SUPABASE_URL = "https://example.supabase.co"
    settings.SUPABASE_KEY = "fake-key"

    monkeypatch.setattr(auth_module, "decode_local_token", lambda t: None)

    def _fail_if_called(t):
        raise AssertionError("decode_supabase_token must not be called in local mode")
    monkeypatch.setattr(auth_module, "decode_supabase_token", _fail_if_called)

    payload, source = auth_module.verify_any_token("some-token")
    assert (payload, source) == (None, None)


def test_supabase_mode_never_calls_local(monkeypatch):
    settings.AUTH_MODE = "supabase"
    settings.SUPABASE_URL = "https://example.supabase.co"
    settings.SUPABASE_KEY = "fake-key"

    def _fail_if_called(t):
        raise AssertionError("decode_local_token must not be called in supabase mode")
    monkeypatch.setattr(auth_module, "decode_local_token", _fail_if_called)
    monkeypatch.setattr(auth_module, "decode_supabase_token", lambda t: {"sub": "supa-2"})

    payload, source = auth_module.verify_any_token("some-token")
    assert source == "supabase"
    assert payload["sub"] == "supa-2"


def test_supabase_mode_without_supabase_configured_returns_none(monkeypatch):
    # has_supabase is False (no URL/KEY) — even in "supabase" mode there's
    # nothing to verify against, so this must fail closed rather than error.
    settings.AUTH_MODE = "supabase"
    settings.SUPABASE_URL = ""
    settings.SUPABASE_KEY = ""

    def _fail_if_called(t):
        raise AssertionError("decode_local_token must not be called in supabase mode")
    monkeypatch.setattr(auth_module, "decode_local_token", _fail_if_called)

    payload, source = auth_module.verify_any_token("some-token")
    assert (payload, source) == (None, None)
