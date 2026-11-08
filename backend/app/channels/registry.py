"""What is configured for this deployment and the transports the intake replies through."""

from __future__ import annotations

import httpx

from app.channels.base import Transport
from app.channels.email import SmtpTransport
from app.channels.telegram import TelegramAdapter
from app.channels.whatsapp import WhatsAppAdapter
from app.config import Settings


class ChannelHub:
    def __init__(self, settings: Settings, http: httpx.AsyncClient | None = None) -> None:
        self.settings = settings
        self.http = http or httpx.AsyncClient(follow_redirects=False)
        self.telegram: TelegramAdapter | None = (
            TelegramAdapter(settings.telegram_bot_token, settings.telegram_webhook_secret, self.http)
            if settings.telegram_bot_token
            else None
        )
        self.whatsapp: WhatsAppAdapter | None = (
            WhatsAppAdapter(
                settings.whatsapp_access_token,
                settings.whatsapp_phone_number_id,
                settings.whatsapp_app_secret,
                settings.whatsapp_verify_token,
                self.http,
                settings.whatsapp_api_version,
            )
            if settings.whatsapp_access_token and settings.whatsapp_phone_number_id
            else None
        )
        self.email = SmtpTransport(settings)

    def transports(self) -> dict[str, Transport]:
        out: dict[str, Transport] = {}
        if self.telegram is not None:
            out["telegram"] = self.telegram
        if self.whatsapp is not None:
            out["whatsapp"] = self.whatsapp
        if self.email.configured:
            out["email"] = self.email
        return out

    async def aclose(self) -> None:
        await self.http.aclose()
