"""Ingestion channels: email, Telegram, WhatsApp."""

from app.channels.base import InboundFile, InboundMessage, Transport, normalise_identity
from app.channels.intake import IntakeDeps, IntakeResult, handle_inbound, new_pairing_code, send_question
from app.channels.registry import ChannelHub

__all__ = [
    "ChannelHub",
    "InboundFile",
    "InboundMessage",
    "IntakeDeps",
    "IntakeResult",
    "Transport",
    "handle_inbound",
    "new_pairing_code",
    "normalise_identity",
    "send_question",
]
