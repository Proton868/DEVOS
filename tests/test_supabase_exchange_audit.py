"""Regression: Supabase exchange uses canonical AuditEventType.AUTH."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from governance.audit import AuditEventType, AuditLogger

ROOT = Path(__file__).resolve().parents[1]


def test_audit_event_type_has_auth_not_login_success():
    assert hasattr(AuditEventType, "AUTH")
    assert AuditEventType.AUTH.value == "auth"
    assert not hasattr(AuditEventType, "LOGIN_SUCCESS")
    assert not hasattr(AuditEventType, "LOGIN_FAILURE")
    assert not hasattr(AuditEventType, "LOGOUT")


def test_supabase_exchange_source_uses_canonical_auth_event():
    src = (ROOT / "api" / "routes" / "auth.py").read_text()
    assert "async def supabase_exchange" in src
    body = src.split("async def supabase_exchange", 1)[1].split("@router", 1)[0]
    assert "AuditEventType.AUTH" in body
    assert "LOGIN_SUCCESS" not in body
    assert "LOGIN_FAILURE" not in body
    assert "action=\"supabase_exchange\"" in body
    assert "outcome=\"success\"" in body
    assert "make_jwt" in body
    assert "devos_token" in body
    assert "supabase_linked" in body


def test_auth_route_source_uses_only_canonical_auth_events():
    src = (ROOT / "api" / "routes" / "auth.py").read_text()
    assert "LOGIN_SUCCESS" not in src
    assert "LOGIN_FAILURE" not in src
    assert "AuditEventType.LOGOUT" not in src
    assert "AuditEventType.AUTH" in src


def test_audit_logger_accepts_auth_event_type_without_attribute_error():
    """Calling log with AUTH must not raise AttributeError (the production bug)."""
    mock_sess = MagicMock()
    mock_cm = MagicMock()
    mock_cm.__enter__.return_value = mock_sess
    mock_cm.__exit__.return_value = None
    with patch("core.sync_session.get_sync_session", return_value=mock_cm):
        AuditLogger().log(
            AuditEventType.AUTH,
            actor_id="user-1",
            tenant_id="default",
            action="supabase_exchange",
            outcome="success",
        )
