"""Email channel: inbound webhook JSON (Postmark/SendGrid-style) or raw MIME → InboundMessage;
bodies without attachments → one-page PDF; replies (questions) by SMTP."""

from __future__ import annotations

import asyncio
import base64
import html
import logging
import re
import smtplib
import unicodedata
from email import message_from_bytes, policy
from email.message import EmailMessage
from email.utils import getaddresses, parseaddr
from typing import Any

from app.channels.base import InboundFile, InboundMessage, normalise_email
from app.config import Settings
from app.models.document import DocumentChannel

log = logging.getLogger("invoiceflow.channels.email")

MAX_ATTACHMENT = 50 * 1024 * 1024
_TAG_RE = re.compile(
    r"<(script|style)[^>]*>.*?</\1>|<br\s*/?>|</p>|</div>|</tr>|<[^>]+>", re.IGNORECASE | re.DOTALL
)


def html_to_text(markup: str) -> str:
    def repl(m: re.Match[str]) -> str:
        tag = m.group(0).lower()
        if tag.startswith(("<br", "</p", "</div", "</tr")):
            return "\n"
        return ""

    text = _TAG_RE.sub(repl, markup)
    text = html.unescape(text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _first_to(value: Any) -> str | None:
    if isinstance(value, list):
        value = value[0] if value else None
    if isinstance(value, dict):
        value = value.get("email") or value.get("address")
    if not value:
        return None
    return parseaddr(str(value))[1].lower() or str(value).lower()


RECIPIENT_HEADERS = ("X-Original-To", "Delivered-To", "Envelope-To", "X-Forwarded-To", "To", "Cc")


def _addresses(values: list[str]) -> list[str]:
    out: list[str] = []
    for _name, addr in getaddresses([str(v) for v in values if v]):
        a = addr.strip().lower()
        if a and "@" in a and a not in out:
            out.append(a)
    return out


def _json_addresses(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        out: list[str] = []
        for v in value:
            out.extend(_json_addresses(v))
        return out
    if isinstance(value, dict):
        return _json_addresses(value.get("email") or value.get("address"))
    return _addresses([str(value)])


def _dedupe(values: list[str]) -> list[str]:
    out: list[str] = []
    for v in values:
        if v not in out:
            out.append(v)
    return out


def parse_inbound_json(payload: dict[str, Any]) -> InboundMessage:
    """A generic inbound-email webhook body: ``from``, ``to``, ``subject``, ``text``/``html``,
    ``attachments: [{name|filename, content_type, content (base64)}]``, ``message_id``."""
    sender_raw = payload.get("from") or payload.get("sender") or ""
    if isinstance(sender_raw, dict):
        display, sender_raw = str(sender_raw.get("name") or ""), str(sender_raw.get("email") or "")
    else:
        display, sender_raw = parseaddr(str(sender_raw))
    raw_envelope = payload.get("envelope")
    envelope: dict[str, Any] = raw_envelope if isinstance(raw_envelope, dict) else {}
    text = payload.get("text") or payload.get("text_body") or payload.get("plain")
    if not text and payload.get("html"):
        text = html_to_text(str(payload["html"]))
    files: list[InboundFile] = []
    for a in payload.get("attachments") or []:
        name = str(a.get("name") or a.get("filename") or "attachment")
        content = a.get("content") or a.get("data") or ""
        try:
            data = base64.b64decode(content)
        except (ValueError, TypeError):
            continue
        if 0 < len(data) <= MAX_ATTACHMENT:
            files.append(InboundFile(name, data, a.get("content_type") or a.get("type")))
    return InboundMessage(
        channel=DocumentChannel.email,
        sender=normalise_email(sender_raw),
        sender_display=display or "",
        external_id=str(payload.get("message_id") or payload.get("MessageID") or "") or None,
        text=str(text) if text else None,
        subject=str(payload.get("subject") or "") or None,
        to_address=_first_to(payload.get("to") or payload.get("recipient")),
        files=files,
        recipients=_dedupe(
            _json_addresses(envelope.get("to"))
            + _json_addresses(payload.get("recipient"))
            + _json_addresses(payload.get("to"))
            + _json_addresses(payload.get("cc"))
        ),
    )


def parse_mime(raw: bytes) -> InboundMessage:
    """RFC 5322 message → InboundMessage (attachments and inline files; text body or HTML → text)."""
    m = message_from_bytes(raw, policy=policy.default)
    display, addr = parseaddr(str(m.get("From", "")))
    text_body: str | None = None
    html_body: str | None = None
    files: list[InboundFile] = []
    for part in m.walk():
        if part.get_content_maintype() == "multipart":
            continue
        filename = part.get_filename()
        ctype = part.get_content_type()
        disposition = (part.get("Content-Disposition") or "").lower()
        if (
            filename
            or disposition.startswith("attachment")
            or ctype in ("application/pdf", "application/xml", "text/xml")
            or ctype.startswith("image/")
        ):
            payload = part.get_payload(decode=True)
            if isinstance(payload, bytes) and 0 < len(payload) <= MAX_ATTACHMENT:
                files.append(InboundFile(filename or f"part.{ctype.split('/')[-1]}", payload, ctype))
            continue
        if ctype == "text/plain" and text_body is None:
            text_body = part.get_content()
        elif ctype == "text/html" and html_body is None:
            html_body = part.get_content()
    text = text_body or (html_to_text(html_body) if html_body else None)
    to_value = str(m.get("X-Original-To") or m.get("Delivered-To") or m.get("To") or "")
    recipients: list[str] = []
    for header in RECIPIENT_HEADERS:
        for a in _addresses([str(v) for v in (m.get_all(header) or [])]):
            if a not in recipients:
                recipients.append(a)
    return InboundMessage(
        channel=DocumentChannel.email,
        sender=normalise_email(addr),
        sender_display=display,
        external_id=str(m.get("Message-ID") or "").strip() or None,
        text=text.strip() if text else None,
        subject=str(m.get("Subject") or "") or None,
        to_address=parseaddr(to_value)[1].lower() or None,
        files=files,
        recipients=recipients,
    )


def _latin1(s: str) -> str:
    return unicodedata.normalize("NFKD", s).encode("latin-1", "ignore").decode("latin-1")


def body_to_pdf(subject: str, sender: str, text: str) -> InboundFile:
    """An email body as a one-page (or more) PDF so pasted invoices run through the pipeline."""
    from fpdf import FPDF

    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(0, 8, _latin1(subject or "(no subject)"), new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", size=9)
    pdf.cell(0, 6, _latin1(f"From: {sender}"), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)
    pdf.set_font("Helvetica", size=10)
    for line in text.splitlines()[:400] or [""]:
        pdf.multi_cell(0, 5, _latin1(line) or " ", new_x="LMARGIN", new_y="NEXT")
    return InboundFile("email-body.pdf", bytes(pdf.output()), "application/pdf")


class SmtpTransport:
    """Outbound mail for questions (and nothing else). Not configured → sends nothing."""

    def __init__(self, settings: Settings) -> None:
        self.s = settings

    @property
    def configured(self) -> bool:
        return bool(self.s.smtp_host and self.s.smtp_from_address)

    async def send_text(self, to: str, text: str, *, subject: str | None = None) -> bool:
        if not self.configured:
            return False
        msg = EmailMessage()
        msg["From"] = self.s.smtp_from_address
        msg["To"] = to
        msg["Subject"] = subject or "InvoiceFlow"
        msg["Auto-Submitted"] = "auto-generated"
        msg.set_content(text)
        return await asyncio.to_thread(self._send, msg)

    def _send(self, msg: EmailMessage) -> bool:
        try:
            with smtplib.SMTP(self.s.smtp_host or "", self.s.smtp_port, timeout=15) as smtp:
                if self.s.smtp_starttls:
                    smtp.starttls()
                if self.s.smtp_user:
                    smtp.login(self.s.smtp_user, self.s.smtp_password or "")
                smtp.send_message(msg)
            return True
        except (OSError, smtplib.SMTPException) as e:
            log.warning("smtp send failed: %s", type(e).__name__)
            return False
