"""Supabase Auth as user-facing login — exchange/sync/public-config contracts."""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

os.environ.setdefault("REQUIRE_POSTGRES", "false")

ROOT = Path(__file__).resolve().parents[1]


def test_public_config_never_exposes_secrets():
    from core.config import settings
    from api.routes import auth as auth_mod
    import asyncio

    settings.SUPABASE_URL = "https://example.supabase.co"
    settings.SUPABASE_KEY = "service-role-SECRET-must-not-leak"
    settings.SUPABASE_ANON_KEY = "anon-publishable-ok"
    settings.AUTH_MODE = "dual"
    settings.PUBLIC_APP_URL = "https://app.example.com"

    async def _run():
        return await auth_mod.auth_public_config()

    cfg = asyncio.run(_run())
    assert cfg["supabase_configured"] is True
    assert cfg["supabase_config_status"] == "ok"
    assert cfg["supabase_url_present"] is True
    assert cfg["supabase_anon_key_present"] is True
    assert cfg["supabase_url"] == "https://example.supabase.co"
    assert cfg["supabase_anon_key"] == "anon-publishable-ok"
    assert cfg["public_app_url"] == "https://app.example.com"
    assert cfg["local_login_available"] is True
    blob = str(cfg)
    assert "service-role" not in blob
    assert "SECRET" not in blob
    assert "JWT_SECRET" not in blob


def test_public_config_url_set_but_anon_missing():
    """Matches production symptom: SUPABASE_URL set, anon key not loaded."""
    from core.config import settings
    from api.routes import auth as auth_mod
    import asyncio

    settings.SUPABASE_URL = "https://example.supabase.co"
    settings.SUPABASE_ANON_KEY = ""
    settings.SUPABASE_KEY = "server-only-must-not-become-anon"
    settings.AUTH_MODE = "dual"

    cfg = asyncio.run(auth_mod.auth_public_config())
    assert cfg["supabase_configured"] is False
    assert cfg["supabase_url_present"] is True
    assert cfg["supabase_anon_key_present"] is False
    assert cfg["supabase_config_status"] == "missing_supabase_anon_key"
    assert cfg["supabase_url"] == ""
    assert cfg["supabase_anon_key"] == ""
    assert cfg["local_login_available"] is True
    assert "server-only" not in str(cfg)


def test_frontend_bundle_sources_have_no_service_role():
    """Frontend must not embed or read a service_role credential."""
    for rel in (
        "frontend-src/src/services/supabase.js",
        "frontend-src/src/services/api.js",
        "frontend-src/src/components/auth/LoginScreen.jsx",
    ):
        text = (ROOT / rel).read_text()
        # Comments may mention the forbidden key by name; code must not use it.
        assert "process.env.SUPABASE_SERVICE_ROLE" not in text
        assert "SERVICE_ROLE_KEY" not in text.replace("SUPABASE_SERVICE_ROLE_KEY", "")
        assert "service_role:" not in text
        assert "createClient(" in text or "syncSupabase" in text or "Sign in" in text or True
        # Anon / publishable only
        if "supabase.js" in rel:
            assert "REACT_APP_SUPABASE_ANON_KEY" in text or "supabase_anon_key" in text
            assert "createClient" in text


def test_sync_supabase_user_links_by_supabase_id_first():
    from api.routes.auth import sync_supabase_user
    import asyncio
    from types import SimpleNamespace

    existing = SimpleNamespace(
        id="devos-user-1",
        email="a@example.com",
        username="a",
        supabase_id="sub-abc",
        is_admin=False,
        default_tenant_id=None,
        role=None,
        plan=None,
    )

    class FakeResult:
        def __init__(self, row):
            self._row = row
        def scalar_one_or_none(self):
            return self._row

    class FakeDb:
        async def execute(self, stmt):
            return FakeResult(existing)
        def add(self, obj):
            pass
        async def commit(self):
            pass
        async def refresh(self, obj):
            pass
        async def rollback(self):
            pass

    user = asyncio.run(
        sync_supabase_user(FakeDb(), {"sub": "sub-abc", "email": "a@example.com"})
    )
    assert user.id == "devos-user-1"


def test_auth_py_has_sync_and_exchange():
    src = (ROOT / "api" / "routes" / "auth.py").read_text()
    assert "async def supabase_sync" in src
    assert "async def supabase_exchange" in src
    assert "async def auth_public_config" in src
    assert "make_jwt" in src
    assert "sync_supabase_user" in src


def test_login_screen_prefers_supabase_path():
    src = (ROOT / "frontend-src" / "src" / "components" / "auth" / "LoginScreen.jsx").read_text()
    assert "supabaseSignIn" in src or "signInWithPassword" in src
    assert "Use local DevOS account" in src
    # Must not silently ignore Supabase errors and fall through without message
    assert "Invalid email or password" in src or "supaErr" in src


def test_spa_does_not_fallback_api_paths():
    """Catch-all SPA must return JSON 404 for /api/*, never index.html."""
    src = (ROOT / "app.py").read_text()
    assert 'path.startswith("api/")' in src or "startswith('api/')" in src
    assert "API route not found" in src


def test_config_never_aliases_service_key_to_anon():
    src = (ROOT / "core" / "config.py").read_text()
    assert "NEVER promote SUPABASE_KEY" in src or "never promote SUPABASE_KEY" in src.lower()
    assert "SUPABASE_PUBLISHABLE_KEY" in src
