"""
Communications — Email delivery.

Provides async email sending via aiosmtplib, reading SMTP configuration
from core/config.py's Settings (SMTP_HOST, SMTP_PORT, SMTP_USER,
SMTP_PASSWORD, SMTP_FROM). Follows the same global-env-vars pattern used
by all other provider credentials (OPENAI_API_KEY, TAVILY_API_KEY, etc.)
rather than per-user secrets — SMTP is a deployment-level configuration,
not a per-user credential.

When SMTP isn't configured, send_email raises NotConfiguredError so
callers (workflow NOTIFY steps, HITL alerts, automation templates) can
surface "email isn't configured" clearly rather than a confusing failure
or a silent no-op.
"""
import logging
import asyncio
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Optional

from core.config import settings

logger = logging.getLogger("devos.email")


class NotConfiguredError(Exception):
    """Raised when send_email is called without SMTP configuration."""


async def send_email(
    to: str,
    subject: str,
    body: str,
    html_body: Optional[str] = None,
    cc: Optional[list[str]] = None,
    bcc: Optional[list[str]] = None,
) -> dict:
    """Send an email asynchronously via aiosmtplib.

    Raises NotConfiguredError if SMTP_HOST is not set in config.

    Returns a dict with status and message_id for logging/audit.
    """
    if not settings.SMTP_HOST:
        raise NotConfiguredError(
            "SMTP is not configured. Set SMTP_HOST, SMTP_PORT, SMTP_USER, "
            "SMTP_PASSWORD, and SMTP_FROM in .env or environment variables."
        )

    # Build the message
    if html_body:
        msg = MIMEMultipart("alternative")
        msg.attach(MIMEText(body, "plain", "utf-8"))
        msg.attach(MIMEText(html_body, "html", "utf-8"))
    else:
        msg = MIMEText(body, "plain", "utf-8")

    msg["Subject"] = subject
    msg["From"] = settings.SMTP_FROM
    msg["To"] = to
    if cc:
        msg["Cc"] = ", ".join(cc)
    if bcc:
        msg["Bcc"] = ", ".join(bcc)

    all_recipients = [to]
    if cc:
        all_recipients.extend(cc)
    if bcc:
        all_recipients.extend(bcc)

    try:
        import aiosmtplib
        response = await aiosmtplib.send(
            msg,
            hostname=settings.SMTP_HOST,
            port=settings.SMTP_PORT,
            username=settings.SMTP_USER or None,
            password=settings.SMTP_PASSWORD or None,
            start_tls=settings.SMTP_PORT == 587,
            use_tls=settings.SMTP_PORT == 465,
        )
        message_id = response[1] if isinstance(response, tuple) and len(response) > 1 else ""
        logger.info(f"[email] sent to {to}: '{subject}' (id={message_id})")
        return {"status": "sent", "message_id": str(message_id) if message_id else ""}
    except Exception as e:
        logger.error(f"[email] failed to send to {to}: {e}")
        raise


# Synchronous convenience wrapper for callers that can't await (e.g. APScheduler
# jobs that aren't async, or sync code paths that need to fire-and-forget).
def send_email_sync(
    to: str,
    subject: str,
    body: str,
    html_body: Optional[str] = None,
) -> None:
    """Fire-and-forget synchronous wrapper. Logs errors; does not raise."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            loop.create_task(send_email(to, subject, body, html_body))
        else:
            asyncio.run(send_email(to, subject, body, html_body))
    except NotConfiguredError:
        logger.warning("[email] skipped: SMTP not configured")
    except Exception as e:
        logger.error(f"[email] sync send failed to {to}: {e}")