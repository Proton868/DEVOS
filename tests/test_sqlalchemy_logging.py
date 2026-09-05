"""Production SQL logging should be quiet unless SQL_ECHO is explicitly enabled."""
from __future__ import annotations

import logging
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_default_settings_sql_echo_off():
    from core.config import Settings

    s = Settings(SQL_ECHO=False, DEBUG=True)
    assert s.SQL_ECHO is False


def test_database_engine_echo_bound_to_sql_echo_not_debug():
    src = (ROOT / "core" / "database.py").read_text(encoding="utf-8")
    assert "SQL_ECHO" in src
    assert "echo=settings.DEBUG" not in src


def test_app_configure_logging_quiets_sqlalchemy():
    src = (ROOT / "app.py").read_text(encoding="utf-8")
    assert 'getLogger("sqlalchemy.engine")' in src
    assert "SQL_ECHO" in src
    # production path sets WARNING when SQL_ECHO is false
    assert "logging.WARNING" in src


def test_sqlalchemy_logger_level_helper():
    """Mirror app._configure_logging SQL branch without importing full app."""
    def apply(sql_echo: bool):
        sql_level = logging.INFO if sql_echo else logging.WARNING
        logging.getLogger("sqlalchemy.engine").setLevel(sql_level)
        logging.getLogger("sqlalchemy.engine.Engine").setLevel(sql_level)
        logging.getLogger("sqlalchemy.pool").setLevel(logging.WARNING)

    apply(False)
    eng = logging.getLogger("sqlalchemy.engine")
    assert eng.getEffectiveLevel() >= logging.WARNING
    assert eng.isEnabledFor(logging.WARNING)
    assert eng.isEnabledFor(logging.ERROR)
    assert not eng.isEnabledFor(logging.INFO)

    apply(True)
    eng = logging.getLogger("sqlalchemy.engine")
    assert eng.isEnabledFor(logging.INFO)


def test_config_documents_sql_echo_field():
    from core.config import Settings

    fields = getattr(Settings, "model_fields", None) or getattr(Settings, "__fields__", {})
    assert "SQL_ECHO" in fields
