"""Shared channel types: what every adapter produces and what the intake needs from it."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol

from app.models.document import DocumentChannel


@dataclass(slots=True)
class InboundFile:
    name: str
    data: bytes
    mime: str | None = None  # the magic-byte check decides; this is only a hint


@dataclass(slots=True)
class InboundMessage:
    channel: DocumentChannel
    sender: str  # normalised identity (lowercase email, E.164 phone, telegram user id)
    sender_display: str = ""
    external_id: str | None = None  # provider message id (webhook de-duplication)
    text: str | None = None
    subject: str | None = None
    to_address: str | None = None  # email: the dedicated inbound address
    files: list[InboundFile] = field(default_factory=list)
    received_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    reply_to: str | None = None  # where replies go when it differs from ``sender`` (email)


class Transport(Protocol):
    """Sends a short text back on a channel. Returns True when the provider accepted it."""

    async def send_text(self, to: str, text: str, *, subject: str | None = None) -> bool: ...


def normalise_email(value: str) -> str:
    v = value.strip().lower()
    m = re.search(r"<([^>]+)>", v)
    return (m.group(1) if m else v).strip()


def normalise_phone(value: str) -> str:
    digits = re.sub(r"[^\d+]", "", value.strip())
    if digits.startswith("00"):
        digits = "+" + digits[2:]
    if not digits.startswith("+"):
        digits = "+" + digits
    return digits


def normalise_identity(channel: DocumentChannel | str, value: str) -> str:
    ch = channel.value if isinstance(channel, DocumentChannel) else channel
    if ch == "email":
        return normalise_email(value)
    if ch == "whatsapp":
        return normalise_phone(value)
    return value.strip()


PAIRING_RE = re.compile(r"(?:^|\s)/?(?:start|join)?\s*([A-Z0-9]{4}-?[A-Z0-9]{4})\s*$", re.IGNORECASE)


def pairing_code_in(text: str | None) -> str | None:
    """``/start JOIN-ABCD``, ``ABCD-EFGH`` or ``abcdefgh`` → ``ABCD-EFGH``; else None."""
    if not text:
        return None
    m = PAIRING_RE.search(text.strip())
    if not m:
        return None
    raw = m.group(1).upper().replace("-", "")
    return f"{raw[:4]}-{raw[4:]}"


def is_status_command(text: str | None) -> bool:
    if not text:
        return False
    return text.strip().lower().lstrip("/") in ("status", "state", "stand")
