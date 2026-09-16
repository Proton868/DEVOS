"""Migration runner must not require full Settings when DATABASE_URL is set.

Also: ALLOWED_ORIGINS parsing must work on pydantic-settings 2.3.0
(no NoDecode symbol).
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch


def test_declared_pydantic_settings_pin_is_2_3():
    """Authoritative pin: pydantic-settings==2.3.0 (no NoDecode)."""
    req = Path("requirements.txt").read_text(encoding="utf-8")
    lite = Path("requirements-lite.txt").read_text(encoding="utf-8")
    assert "pydantic-settings==2.3.0" in req
    assert "pydantic-settings==2.3.0" in lite


def test_core_config_source_does_not_import_nodecode():
    text = Path("core/config.py").read_text(encoding="utf-8")
    assert "from pydantic_settings import BaseSettings" in text
    assert "from pydantic_settings import BaseSettings, NoDecode" not in text
    assert "Annotated[List[str], NoDecode]" not in text


def test_core_config_imports_with_declared_pydantic_settings():
    import core.config as cfg
    assert cfg.Settings is not None
    assert isinstance(cfg.settings.ALLOWED_ORIGINS, list)
    assert cfg.settings.ALLOWED_ORIGINS


def test_allowed_origins_default_when_unset(monkeypatch):
    monkeypatch.delenv("ALLOWED_ORIGINS", raising=False)
    monkeypatch.setenv("JWT_SECRET", "x" * 32)
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@127.0.0.1:5432/db")
    from core.config import Settings

    s = Settings(_env_file=None)
    assert s.ALLOWED_ORIGINS == ["http://localhost:8000"]


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


def test_database_import_does_not_need_nodecode():
    """scripts/migrate_agency_schema.py imports core.database → core.config."""
    migrate = Path("scripts/migrate_agency_schema.py").read_text(encoding="utf-8")
    assert "from core.database import" in migrate
    import core.config as cfg
    assert cfg.settings is not None
    assert isinstance(cfg.settings.ALLOWED_ORIGINS, list)
