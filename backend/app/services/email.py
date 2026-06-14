"""Email delivery.

Sends via SMTP when configured (``SMTP_HOST`` set); otherwise logs the message so
flows like password reset stay testable in development without a mail server. SMTP
I/O is blocking, so it runs in a worker thread to avoid stalling the event loop.
"""

from __future__ import annotations

import asyncio
import logging
import smtplib
from email.message import EmailMessage

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


def _build_message(to: str, subject: str, body: str) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = settings.smtp_from
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    return msg


def _send_smtp(msg: EmailMessage) -> None:
    """Blocking SMTP send. Run via asyncio.to_thread from async callers."""
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30) as server:
        if settings.smtp_use_tls:
            server.starttls()
        if settings.smtp_username and settings.smtp_password:
            server.login(settings.smtp_username, settings.smtp_password)
        server.send_message(msg)


async def send_email(to: str, subject: str, body: str) -> None:
    """Send an email, or log it if SMTP isn't configured.

    Delivery failures are logged rather than raised, so callers (e.g. the
    non-enumerating forgot-password endpoint) can behave identically regardless of
    whether the message actually went out.
    """
    if not settings.smtp_host:
        logger.warning(
            "SMTP not configured (SMTP_HOST unset); email to %s was not sent.\n"
            "Subject: %s\n%s",
            to,
            subject,
            body,
        )
        return

    msg = _build_message(to, subject, body)
    try:
        await asyncio.to_thread(_send_smtp, msg)
    except Exception:  # noqa: BLE001 — delivery problems must not crash the request
        logger.exception("Failed to send email to %s", to)


async def send_password_reset_email(to: str, token: str, reset_url: str) -> None:
    """Compose and send (or log) a password-reset email."""
    minutes = settings.password_reset_expire_minutes
    subject = "Reset your AI Power BI Generator password"
    body = (
        "We received a request to reset the password for your account.\n\n"
        f"Open this link to choose a new password (valid for {minutes} minutes):\n\n"
        f"{reset_url}\n\n"
        "If the link doesn't open, go to the Reset Password page in the app and paste "
        f"this code:\n\n{token}\n\n"
        "If you didn't request this, you can safely ignore this email — your password "
        "won't be changed."
    )
    await send_email(to, subject, body)
