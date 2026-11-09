"""Outbound mail for account emails (invites, password resets) and channel replies.

The SMTP settings come from the dashboard (firm → platform). Not configured → nothing is
sent and callers fall back to the copy-link button. Message bodies are never logged."""

from __future__ import annotations

import asyncio
import logging
import smtplib
import uuid
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formataddr
from typing import Protocol

from app.settings_store import Section, SettingsStore

log = logging.getLogger("invoiceflow.mail")


@dataclass(slots=True)
class OutgoingMail:
    to: str
    subject: str
    text: str
    auto_submitted: bool = True


class Mailer(Protocol):
    async def configured(self, firm_id: uuid.UUID | None) -> bool: ...

    async def send(self, firm_id: uuid.UUID | None, mail: OutgoingMail) -> bool: ...


def _deliver(section: Section, mail: OutgoingMail) -> bool:
    msg = EmailMessage()
    msg["From"] = formataddr((str(section.get("from_name", "InvoiceFlow")), str(section.get("from_address"))))
    msg["To"] = mail.to
    msg["Subject"] = mail.subject
    if mail.auto_submitted:
        msg["Auto-Submitted"] = "auto-generated"
    msg.set_content(mail.text)
    try:
        port = int(section.get("port", 587))
        if port == 465:
            with smtplib.SMTP_SSL(str(section.get("host")), port, timeout=15) as smtp:
                if section.get("user"):
                    smtp.login(str(section.get("user")), str(section.get("password", "")))
                smtp.send_message(msg)
        else:
            with smtplib.SMTP(str(section.get("host")), port, timeout=15) as smtp:
                if section.get("starttls", True):
                    smtp.starttls()
                if section.get("user"):
                    smtp.login(str(section.get("user")), str(section.get("password", "")))
                smtp.send_message(msg)
        return True
    except (OSError, smtplib.SMTPException) as e:
        log.warning("smtp send failed: %s", type(e).__name__)
        return False


class SmtpMailer:
    def __init__(self, store: SettingsStore) -> None:
        self.store = store

    async def _section(self, firm_id: uuid.UUID | None) -> Section | None:
        sec = await self.store.effective("smtp", firm_id)
        if not sec.get("host") or not sec.get("from_address"):
            return None
        return sec

    async def configured(self, firm_id: uuid.UUID | None) -> bool:
        return await self._section(firm_id) is not None

    async def send(self, firm_id: uuid.UUID | None, mail: OutgoingMail) -> bool:
        sec = await self._section(firm_id)
        if sec is None:
            return False
        return await asyncio.to_thread(_deliver, sec, mail)

    async def test(self, section: Section, to: str) -> bool:
        return await asyncio.to_thread(
            _deliver,
            section,
            OutgoingMail(to, "InvoiceFlow test email", "This is a test email from InvoiceFlow. It works."),
        )


class RecordingMailer:
    """Test double: keeps every mail in memory."""

    def __init__(self, configured: bool = True) -> None:
        self.outbox: list[tuple[uuid.UUID | None, OutgoingMail]] = []
        self._configured = configured

    async def configured(self, firm_id: uuid.UUID | None) -> bool:
        return self._configured

    async def send(self, firm_id: uuid.UUID | None, mail: OutgoingMail) -> bool:
        if not self._configured:
            return False
        self.outbox.append((firm_id, mail))
        return True


class MailerTransport:
    """The email channel's reply transport through the dashboard SMTP (the firm's own, else the
    platform's)."""

    def __init__(self, mailer: Mailer, firm_id: uuid.UUID | None) -> None:
        self.mailer = mailer
        self.firm_id = firm_id

    async def send_text(self, to: str, text: str, *, subject: str | None = None) -> bool:
        return await self.mailer.send(self.firm_id, OutgoingMail(to, subject or "InvoiceFlow", text))
