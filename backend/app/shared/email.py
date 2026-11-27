"""Outgoing email. One interface; the backend is chosen by `EMAIL_BACKEND`.

- unset: nothing can be sent. Features that depend on email say so instead of pretending.
- `log`: development only. The whole message, links included, is written to the log.
- `smtp`: a real mail server.

Messages carry one-time links, so they are never logged outside development.
"""

import logging
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage

from app.core.config import settings

logger = logging.getLogger("app.email")


@dataclass(frozen=True)
class Message:
    to: str
    subject: str
    body: str


def can_send() -> bool:
    return settings.email_backend is not None


def deliver(message: Message) -> None:
    """Send now. Raises if it cannot be sent. Requests use `jobs.service.queue_email`, so a
    slow or failing mail server never holds up, or half-completes, a request."""
    if settings.email_backend == "log":
        logger.info("Email to %s: %s\n%s", message.to, message.subject, message.body)
        return
    if settings.email_backend != "smtp" or not settings.smtp_host:
        raise RuntimeError("Email is not configured.")
    mail = EmailMessage()
    mail["From"] = settings.email_from
    mail["To"] = message.to
    mail["Subject"] = message.subject
    mail.set_content(message.body)
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as server:
        server.starttls()
        if settings.smtp_username and settings.smtp_password:
            server.login(settings.smtp_username, settings.smtp_password.get_secret_value())
        server.send_message(mail)
