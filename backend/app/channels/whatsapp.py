"""WhatsApp Business Cloud API adapter (official API only): webhook verification, signed
notifications → InboundMessage (documents, images, text), media download, text replies."""

from __future__ import annotations

import hashlib
import hmac
import logging
from typing import Any

import httpx

from app.channels.base import InboundFile, InboundMessage, normalise_phone
from app.models.document import DocumentChannel

log = logging.getLogger("invoiceflow.channels.whatsapp")

GRAPH = "https://graph.facebook.com"
MAX_MEDIA = 100 * 1024 * 1024


class WhatsAppAdapter:
    def __init__(
        self,
        access_token: str,
        phone_number_id: str,
        app_secret: str | None,
        verify_token: str | None,
        http: httpx.AsyncClient,
        api_version: str = "v21.0",
    ) -> None:
        self._token = access_token
        self.phone_number_id = phone_number_id
        self._app_secret = app_secret or ""
        self._verify_token = verify_token or ""
        self.http = http
        self.version = api_version

    def verify_challenge(self, mode: str | None, token: str | None, challenge: str | None) -> str | None:
        if (
            mode == "subscribe"
            and self._verify_token
            and hmac.compare_digest(self._verify_token, token or "")
        ):
            return challenge or ""
        return None

    def signature_valid(self, header: str | None, raw_body: bytes) -> bool:
        if not self._app_secret or not header or not header.startswith("sha256="):
            return False
        want = hmac.new(self._app_secret.encode(), raw_body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(want, header[7:].strip())

    def parse_notification(self, payload: dict[str, Any]) -> list[InboundMessage]:
        out: list[InboundMessage] = []
        for entry in payload.get("entry") or []:
            for change in entry.get("changes") or []:
                value = change.get("value") or {}
                number = str((value.get("metadata") or {}).get("phone_number_id") or "")
                names = {
                    c.get("wa_id"): (c.get("profile") or {}).get("name", "")
                    for c in value.get("contacts") or []
                }
                for m in value.get("messages") or []:
                    sender = m.get("from")
                    if not sender:
                        continue
                    kind = m.get("type")
                    files: list[InboundFile] = []
                    text = None
                    if kind == "text":
                        text = (m.get("text") or {}).get("body")
                    elif kind in ("document", "image"):
                        media = m.get(kind) or {}
                        if media.get("id"):
                            name = media.get("filename") or ("photo.jpg" if kind == "image" else "document")
                            files.append(InboundFile(f"wa:{media['id']}|{name}", b"", media.get("mime_type")))
                            text = media.get("caption")
                    else:
                        continue  # audio, stickers, reactions… are not documents
                    out.append(
                        InboundMessage(
                            channel=DocumentChannel.whatsapp,
                            sender=normalise_phone(str(sender)),
                            sender_display=str(names.get(sender, "")),
                            external_id=str(m.get("id") or "") or None,
                            text=text,
                            files=files,
                            reply_to=str(sender),
                            chat_type="private",
                            chat_title=number,  # the receiving number (dedicated numbers route by it)
                            transport=self,
                        )
                    )
        return out

    async def fetch_files(self, msg: InboundMessage) -> None:
        resolved: list[InboundFile] = []
        headers = {"authorization": f"Bearer {self._token}"}
        for f in msg.files:
            if not f.name.startswith("wa:"):
                resolved.append(f)
                continue
            media_id, _, name = f.name[3:].partition("|")
            try:
                meta = await self.http.get(f"{GRAPH}/{self.version}/{media_id}", headers=headers, timeout=20)
                url = meta.json().get("url") if meta.status_code == 200 else None
                if not url or not str(url).startswith("https://"):
                    continue
                blob = await self.http.get(url, headers=headers, timeout=60)
                if blob.status_code == 200 and 0 < len(blob.content) <= MAX_MEDIA:
                    resolved.append(InboundFile(name, blob.content, f.mime))
            except httpx.HTTPError as e:
                log.warning("whatsapp download failed: %s", type(e).__name__)
        msg.files = resolved

    async def send_text(self, to: str, text: str, *, subject: str | None = None) -> bool:
        try:
            r = await self.http.post(
                f"{GRAPH}/{self.version}/{self.phone_number_id}/messages",
                headers={"authorization": f"Bearer {self._token}"},
                json={
                    "messaging_product": "whatsapp",
                    "to": to.lstrip("+"),
                    "type": "text",
                    "text": {"body": text[:4000]},
                },
                timeout=10,
            )
            return r.status_code == 200
        except httpx.HTTPError as e:
            log.warning("whatsapp send failed: %s", type(e).__name__)
            return False
