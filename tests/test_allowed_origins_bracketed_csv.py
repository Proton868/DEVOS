"""ALLOWED_ORIGINS must accept production-common bracketed non-JSON lists."""
from __future__ import annotations


def test_parse_json_array():
    from core.config import _parse_allowed_origins
    assert _parse_allowed_origins('["https://a.example","http://127.0.0.1:8000"]') == [
        "https://a.example",
        "http://127.0.0.1:8000",
    ]


def test_parse_csv():
    from core.config import _parse_allowed_origins
    assert _parse_allowed_origins("https://a.example,http://127.0.0.1:8000") == [
        "https://a.example",
        "http://127.0.0.1:8000",
    ]


def test_parse_bracketed_non_json_csv():
    """Prime .env form that previously crashed Settings load."""
    from core.config import _parse_allowed_origins
    v = "[https://dev.carai.agency,http://127.0.0.1:8000]"
    assert _parse_allowed_origins(v) == [
        "https://dev.carai.agency",
        "http://127.0.0.1:8000",
    ]


def test_settings_accepts_bracketed_csv(monkeypatch):
    monkeypatch.setenv("ALLOWED_ORIGINS", "[https://dev.carai.agency,http://127.0.0.1:8000]")
    monkeypatch.setenv("JWT_SECRET", "x" * 32)
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@127.0.0.1:5432/db")
    monkeypatch.setenv("REQUIRE_POSTGRES", "false")
    from core.config import Settings
    s = Settings(_env_file=None)
    assert "https://dev.carai.agency" in s.ALLOWED_ORIGINS
    assert "http://127.0.0.1:8000" in s.ALLOWED_ORIGINS
