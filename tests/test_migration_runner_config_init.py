"""Migration runner must not require full Settings when DATABASE_URL is set."""
from __future__ import annotations

from unittest.mock import MagicMock, patch


def test_get_sync_engine_uses_env_url_without_settings(monkeypatch):
    import core.sync_session as ss

    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@127.0.0.1:5432/db")
    monkeypatch.setenv("REQUIRE_POSTGRES", "true")
    ss.dispose_sync_engine()

    with patch.object(ss, "create_engine") as ce:
        fake = MagicMock()
        ce.return_value = fake
        eng = ss.get_sync_engine()
        assert eng is fake
        ce.assert_called_once()
        url_arg = ce.call_args[0][0]
        assert "127.0.0.1" in url_arg
        assert "postgresql" in url_arg
    ss.dispose_sync_engine()


def test_allowed_origins_accepts_comma_separated(monkeypatch):
    monkeypatch.setenv("ALLOWED_ORIGINS", "https://a.example,https://b.example")
    monkeypatch.setenv("JWT_SECRET", "x" * 32)
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@127.0.0.1:5432/db")
    # Fresh Settings instance (module-level settings already constructed)
    from core.config import Settings

    s = Settings(_env_file=None)
    assert s.ALLOWED_ORIGINS == ["https://a.example", "https://b.example"]


def test_allowed_origins_accepts_json_array(monkeypatch):
    monkeypatch.setenv("ALLOWED_ORIGINS", "[\"https://json.example\"]")
    monkeypatch.setenv("JWT_SECRET", "x" * 32)
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@127.0.0.1:5432/db")
    from core.config import Settings

    s = Settings(_env_file=None)
    assert s.ALLOWED_ORIGINS == ["https://json.example"]
