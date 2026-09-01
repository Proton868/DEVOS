"""Tests for communications.email — SMTP delivery with NotConfiguredError guard."""
import pytest
from communications.email import send_email, send_email_sync, NotConfiguredError


@pytest.mark.asyncio
async def test_send_email_raises_not_configured_when_smtp_host_is_empty(monkeypatch):
    monkeypatch.setattr("communications.email.settings.SMTP_HOST", "")
    with pytest.raises(NotConfiguredError, match="SMTP is not configured"):
        await send_email(to="test@example.com", subject="Test", body="Hello")


@pytest.mark.asyncio
async def test_send_email_calls_aiosmtplib_when_configured(monkeypatch):
    monkeypatch.setattr("communications.email.settings.SMTP_HOST", "smtp.example.com")
    monkeypatch.setattr("communications.email.settings.SMTP_PORT", 587)
    monkeypatch.setattr("communications.email.settings.SMTP_USER", "user")
    monkeypatch.setattr("communications.email.settings.SMTP_PASSWORD", "pass")
    monkeypatch.setattr("communications.email.settings.SMTP_FROM", "from@example.com")

    called_with = {}

    async def fake_send(msg, **kwargs):
        called_with["hostname"] = kwargs.get("hostname")
        called_with["port"] = kwargs.get("port")
        called_with["username"] = kwargs.get("username")
        called_with["password"] = kwargs.get("password")
        return (250, "msg-123")

    monkeypatch.setattr("aiosmtplib.send", fake_send)

    result = await send_email(
        to="to@example.com",
        subject="Test Subject",
        body="Test body",
        cc=["cc@example.com"],
        bcc=["bcc@example.com"],
    )

    assert result["status"] == "sent"
    assert result["message_id"] == "msg-123"
    assert called_with["hostname"] == "smtp.example.com"
    assert called_with["port"] == 587
    assert called_with["username"] == "user"
    assert called_with["password"] == "pass"


def test_send_email_sync_no_smtp_host_does_not_raise(monkeypatch):
    monkeypatch.setattr("communications.email.settings.SMTP_HOST", "")
    # Should not raise — logs a warning instead
    send_email_sync(to="test@example.com", subject="Test", body="Hello")


@pytest.mark.asyncio
async def test_send_email_html_body(monkeypatch):
    monkeypatch.setattr("communications.email.settings.SMTP_HOST", "smtp.example.com")
    monkeypatch.setattr("communications.email.settings.SMTP_PORT", 587)
    monkeypatch.setattr("communications.email.settings.SMTP_USER", "")
    monkeypatch.setattr("communications.email.settings.SMTP_PASSWORD", "")
    monkeypatch.setattr("communications.email.settings.SMTP_FROM", "from@example.com")

    async def fake_send(msg, **kwargs):
        return (250, "html-msg")

    monkeypatch.setattr("aiosmtplib.send", fake_send)

    result = await send_email(
        to="to@example.com",
        subject="HTML Test",
        body="Plain text fallback",
        html_body="<h1>Hello</h1>",
    )

    assert result["status"] == "sent"