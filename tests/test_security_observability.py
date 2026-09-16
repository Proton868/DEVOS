"""Secrets normalization + observability error tracking (isolated)."""
from __future__ import annotations

from pathlib import Path

from governance.observability import ObservabilityStore
from api.routes.secrets import normalize_secret_name


def test_secret_name_normalizes_and_rejects_dangerous_values():
    assert normalize_secret_name("stripe api key") == "STRIPE_API_KEY"
    assert normalize_secret_name("api-key") == "API_KEY"
    assert normalize_secret_name("hello.world") == "HELLO_WORLD"
    assert normalize_secret_name("secret;rm -rf /") == "RM_RF"


def test_observability_store_tracks_errors(tmp_path, monkeypatch):
    url = f"sqlite+aiosqlite:///{tmp_path}/observability.db"
    monkeypatch.setenv("REQUIRE_POSTGRES", "false")
    monkeypatch.setenv("DATABASE_URL", url)
    from core.config import settings
    from core.sync_session import dispose_sync_engine

    monkeypatch.setattr(settings, "DATABASE_URL", url)
    monkeypatch.setattr(settings, "REQUIRE_POSTGRES", False)
    dispose_sync_engine()
    ObservabilityStore._instance = None
    store = ObservabilityStore()

    entry = store.record_error("builder", "build failed", trace_id="trace-1", user_id="u-1")
    assert entry["component"] == "builder"
    assert entry["message"] == "build failed"

    errors = store.list_errors(limit=5)
    assert any(item["trace_id"] == "trace-1" for item in errors)
    dispose_sync_engine()
    ObservabilityStore._instance = None
