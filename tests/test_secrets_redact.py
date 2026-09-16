"""Sensitive configuration must not leak into logs/exception text."""
from __future__ import annotations

import logging

from core.secrets_redact import RedactingFilter, redact_text, redact_mapping


def test_redact_database_url_credentials():
    raw = "postgresql+psycopg://myuser:SuperSecretPass@db.example.com:6543/postgres"
    out = redact_text(raw)
    assert "SuperSecretPass" not in out
    assert "myuser" not in out or "***" in out
    assert "db.example.com" in out


def test_redact_api_key_assignment():
    raw = "OPENROUTER_API_KEY=sk-or-v1-abcdefghijklmnop"
    out = redact_text(raw)
    assert "sk-or-v1-abcdefghijklmnop" not in out
    assert "***" in out


def test_redact_jwt_and_password():
    raw = "JWT_SECRET=this-is-a-long-secret-value password=hunter2"
    out = redact_text(raw)
    assert "this-is-a-long-secret-value" not in out
    assert "hunter2" not in out


def test_redact_bearer():
    raw = "Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.aaa.bbb"
    out = redact_text(raw)
    assert "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9" not in out


def test_redact_mapping():
    d = redact_mapping({"DATABASE_URL": "postgresql://u:p@h/db", "ok": "fine"})
    assert "p@" not in str(d.get("DATABASE_URL", ""))
    assert d["ok"] == "fine"


def test_logging_filter_redacts(caplog):
    logger = logging.getLogger("devos.test.redact")
    logger.addFilter(RedactingFilter())
    logger.setLevel(logging.INFO)
    with caplog.at_level(logging.INFO, logger="devos.test.redact"):
        logger.info("connect %s", "postgresql://user:sekrit@host/db")
    text = " ".join(r.message for r in caplog.records)
    assert "sekrit" not in text
