"""Telegram Bot API adapter: webhook updates → InboundMessage (documents, photos, text), file
download through getFile, replies through sendMessage. Task-specific bot only."""

from __future__ import annotations

import hmac
import logging
from typing import Any

import httpx

from app.channels.base import InboundFile, InboundMessage
from app.models.document import DocumentChannel

log = logging.getLogger("invoiceflow.channels.telegram")

API = "https://api.telegram.org"
MAX_FILE = 20 * 1024 * 1024  # Bot API download limit


class TelegramAdapter:
    def __init__(self, token: str, webhook_secret: str | None, http: httpx.AsyncClient) -> None:
        self._token = token
        self._secret = webhook_secret or ""
        self.http = http

    def webhook_authentic(self, header_value: str | None) -> bool:
        if not self._secret:
            return False  # a bot without a configured secret accepts nothing
        return hmac.compare_digest(self._secret, header_value or "")

    def parse_update(self, update: dict[str, Any]) -> InboundMessage | None:
        """``message`` (private chats and groups) and ``channel_post`` (channels, where the bot must be an
        admin). Group → supergroup upgrades come as ``migrate_to_chat_id``. ``my_chat_member`` (the bot
        was added or removed) carries no document and is not turned into a message."""
        m = update.get("message") or update.get("channel_post")
        if not isinstance(m, dict):
            return None
        chat = m.get("chat") or {}
        chat_type = str(chat.get("type") or "private")
        sender = m.get("from") or {}
        if chat_type == "channel":
            # posts in a channel have no person behind them; the channel itself is the sender
            sender = {"id": chat.get("id"), "first_name": chat.get("title") or "channel"}
        if not sender.get("id") or sender.get("is_bot"):
            return None
        display = " ".join(x for x in (sender.get("first_name"), sender.get("last_name")) if x) or (
            sender.get("username") or ""
        )
        files: list[InboundFile] = []
        doc = m.get("document")
        if isinstance(doc, dict) and doc.get("file_id"):
            files.append(InboundFile(str(doc.get("file_name") or "document"), b"", doc.get("mime_type")))
            files[-1].name = f"tg:{doc['file_id']}|{files[-1].name}"
        photos = m.get("photo")
        if isinstance(photos, list) and photos:
            best = max(photos, key=lambda p: int(p.get("file_size") or 0))
            files.append(InboundFile(f"tg:{best['file_id']}|photo.jpg", b"", "image/jpeg"))
        text = m.get("text") or m.get("caption")
        if isinstance(text, str) and chat_type != "private":
            text = text.split("@", 1)[0] if text.startswith("/") and " " not in text else text
        migrate = m.get("migrate_to_chat_id")
        return InboundMessage(
            channel=DocumentChannel.telegram,
            sender=str(sender["id"]),
            sender_display=display,
            external_id=f"{chat.get('id')}:{m.get('message_id')}" if m.get("message_id") else None,
            text=text,
            files=files,
            reply_to=str(chat.get("id") or sender["id"]),
            chat_id=str(chat.get("id")) if chat.get("id") is not None else None,
            chat_type=chat_type,
            chat_title=str(chat.get("title") or ""),
            migrate_to_chat_id=str(migrate) if migrate is not None else None,
            transport=self,
        )

    async def get_me(self) -> dict[str, Any] | None:
        try:
            r = await self.http.get(f"{API}/bot{self._token}/getMe", timeout=10)
            data = r.json() if r.status_code == 200 else {}
            return data.get("result") if data.get("ok") else None
        except (httpx.HTTPError, ValueError):
            return None

    async def set_webhook(self, url: str) -> bool:
        """Webhook with our secret header and only the updates the intake reads (Bot API 10.x)."""
        try:
            r = await self.http.post(
                f"{API}/bot{self._token}/setWebhook",
                json={
                    "url": url,
                    "secret_token": self._secret,
                    "allowed_updates": ["message", "channel_post", "my_chat_member"],
                    "drop_pending_updates": False,
                },
                timeout=15,
            )
            return r.status_code == 200 and bool(r.json().get("ok"))
        except (httpx.HTTPError, ValueError):
            return False

    async def delete_webhook(self) -> None:
        try:
            await self.http.post(f"{API}/bot{self._token}/deleteWebhook", json={}, timeout=10)
        except httpx.HTTPError:
            pass

    async def fetch_files(self, msg: InboundMessage) -> None:
        """Replace the ``tg:<file_id>|<name>`` placeholders with the downloaded bytes."""
        resolved: list[InboundFile] = []
        for f in msg.files:
            if not f.name.startswith("tg:"):
                resolved.append(f)
                continue
            file_id, _, name = f.name[3:].partition("|")
            try:
                r = await self.http.get(
                    f"{API}/bot{self._token}/getFile", params={"file_id": file_id}, timeout=20
                )
                path = r.json().get("result", {}).get("file_path") if r.status_code == 200 else None
                if not path:
                    continue
                blob = await self.http.get(f"{API}/file/bot{self._token}/{path}", timeout=60)
                if blob.status_code == 200 and 0 < len(blob.content) <= MAX_FILE:
                    resolved.append(InboundFile(name, blob.content, f.mime))
            except httpx.HTTPError as e:
                log.warning("telegram download failed: %s", type(e).__name__)
        msg.files = resolved

    async def send_text(self, to: str, text: str, *, subject: str | None = None) -> bool:
        try:
            r = await self.http.post(
                f"{API}/bot{self._token}/sendMessage",
                json={"chat_id": to, "text": text[:4000], "disable_web_page_preview": True},
                timeout=10,
            )
            return r.status_code == 200
        except httpx.HTTPError as e:
            log.warning("telegram send failed: %s", type(e).__name__)
            return False
