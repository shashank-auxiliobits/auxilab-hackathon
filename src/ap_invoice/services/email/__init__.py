"""Outbound email (OTP verification) with a pluggable backend.

``console`` (the default) logs the message — so a freshly-cloned project can run
the full signup/verify flow with no mail server. ``smtp`` sends via a real server
using the stdlib :mod:`smtplib` off the event loop (no extra dependency).
"""

from __future__ import annotations

import asyncio
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage as _MIMEMessage
from typing import Protocol, runtime_checkable

from ap_invoice.core.config import get_settings
from ap_invoice.core.logging import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class EmailMessage:
    """A plain-text email to send."""

    to: str
    subject: str
    body: str


@runtime_checkable
class EmailSender(Protocol):
    """Sends an :class:`EmailMessage`. Implementations must be safe to call from async code."""

    async def send(self, message: EmailMessage) -> None: ...


class ConsoleEmailSender:
    """Logs emails instead of sending them — the default for local/dev runs."""

    async def send(self, message: EmailMessage) -> None:
        logger.info(
            "email_console",
            to=message.to,
            subject=message.subject,
            body=message.body,
        )


class SMTPEmailSender:
    """Sends email via an SMTP server using the stdlib, off the event loop."""

    def __init__(
        self,
        *,
        host: str,
        port: int,
        username: str | None,
        password: str | None,
        use_tls: bool,
        sender: str,
    ) -> None:
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._use_tls = use_tls
        self._sender = sender

    async def send(self, message: EmailMessage) -> None:
        await asyncio.to_thread(self._send_sync, message)

    def _send_sync(self, message: EmailMessage) -> None:
        mime = _MIMEMessage()
        mime["From"] = self._sender
        mime["To"] = message.to
        mime["Subject"] = message.subject
        mime.set_content(message.body)
        with smtplib.SMTP(self._host, self._port, timeout=30) as smtp:
            if self._use_tls:
                smtp.starttls()
            if self._username and self._password:
                smtp.login(self._username, self._password)
            smtp.send_message(mime)


def get_email_sender() -> EmailSender:
    """Return the configured email backend (``console`` or ``smtp``)."""
    settings = get_settings()
    if settings.email_backend == "smtp":
        if not settings.smtp_host:
            raise RuntimeError("AP_EMAIL_BACKEND=smtp requires AP_SMTP_HOST to be set.")
        return SMTPEmailSender(
            host=settings.smtp_host,
            port=settings.smtp_port,
            username=settings.smtp_username,
            password=settings.smtp_password,
            use_tls=settings.smtp_use_tls,
            sender=settings.email_from,
        )
    return ConsoleEmailSender()


__all__ = [
    "ConsoleEmailSender",
    "EmailMessage",
    "EmailSender",
    "SMTPEmailSender",
    "get_email_sender",
]
