"""Bots and numbers per platform, firm or client company. Secrets (bot token, webhook
secret, WhatsApp access token, app secret, verify token) are sealed with the owner's data key and only
ever opened to build an adapter; the API returns none of them."""

from __future__ import annotations

import json
import secrets
import uuid
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.channels.telegram import TelegramAdapter
from app.channels.whatsapp import WhatsAppAdapter
from app.licensing.store import FirmSecrets
from app.models.bots import ChannelBot
from app.models.firm import Firm
from app.security.crypto import MasterKey

Adapter = TelegramAdapter | WhatsAppAdapter


def seal(master: MasterKey, owner: Firm, bot_id: uuid.UUID, values: dict[str, str]) -> bytes:
    return FirmSecrets(master, owner).seal(f"bot:{bot_id}", json.dumps(values, sort_keys=True))


def open_secrets(master: MasterKey, owner: Firm, bot: ChannelBot) -> dict[str, str]:
    opened = FirmSecrets(master, owner).open(f"bot:{bot.id}", bot.secrets_enc)
    return json.loads(opened) if opened else {}


def new_webhook_secret() -> str:
    return secrets.token_urlsafe(32)  # letters, digits, "-" and "_": what Telegram's secret_token allows


def adapter_for(master: MasterKey, owner: Firm, bot: ChannelBot, http: httpx.AsyncClient) -> Adapter:
    sec = open_secrets(master, owner, bot)
    if bot.channel == "telegram":
        return TelegramAdapter(sec.get("token", ""), sec.get("webhook_secret", ""), http)
    return WhatsAppAdapter(
        sec.get("access_token", ""),
        str(bot.public.get("phone_number_id", "")),
        sec.get("app_secret", ""),
        sec.get("verify_token", ""),
        http,
        str(bot.public.get("api_version") or "v21.0"),
    )


async def load(session: AsyncSession, bot_id: uuid.UUID) -> tuple[ChannelBot, Firm] | None:
    """Caller has the platform bypass."""
    bot = await session.get(ChannelBot, bot_id)
    if bot is None or not bot.active:
        return None
    owner = await session.get(Firm, bot.firm_id)
    if owner is None or not owner.is_active:
        return None
    return bot, owner


def bot_view(bot: ChannelBot, public_url: str) -> dict[str, Any]:
    base = public_url.rstrip("/")
    path = f"/api/v1/channels/{bot.channel}/webhook/{bot.id}"
    public = bot.public or {}
    return {
        "id": str(bot.id),
        "channel": bot.channel,
        "client_company_id": str(bot.client_company_id) if bot.client_company_id else None,
        "display_name": bot.display_name,
        "username": public.get("username"),
        "phone_number_id": public.get("phone_number_id"),
        "accept_from": bot.accept_from,
        "active": bot.active,
        "last_error": bot.last_error,
        "webhook_url": f"{base}{path}",
        "created_at": bot.created_at.isoformat() if bot.created_at else None,
    }
