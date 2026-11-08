"""E-invoicing networks behind the bridge contract: a Peppol access point
provider and a French certified platform (PDP). Receiving pulls the provider's inbox since a
cursor; sending posts outgoing documents to its outbox. The contract is documented in
``docs/integrations/peppol.md`` and ``docs/integrations/france-pdp.md``.

Bridge contract (all under ``base_url``, bearer ``api_token``):

* ``GET  /health``                     → 2xx
* ``GET  /inbox?since=<cursor>``       → ``{"documents": [{id, receiver, sender, filename, mime,
  document_type, created_at}], "cursor": "<opaque>"}``
* ``GET  /inbox/{id}``                 → the document bytes (UBL/CII XML, Factur-X PDF)
* ``POST /inbox/{id}/ack``             → 2xx (the provider may now drop it)
* ``POST /outbox`` multipart ``document`` + fields ``receiver``, ``document_type``
  → ``{"id": "<provider id>"}``
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.connectors.base import (
    ConfigField,
    PushItem,
    PushOutcome,
    PushResult,
    ReceivedItem,
    ReceiveResult,
)
from app.exports.ubl_writer import to_ubl

log = logging.getLogger("invoiceflow.connectors.networks")

MAX_DOCUMENT_BYTES = 25 * 1024 * 1024
MAX_INBOX_PER_PULL = 100


class BridgeConnector:
    """Shared implementation; the subclasses only differ in naming, routing scheme and formats."""

    key = "bridge"
    name = "Network bridge"
    description = ""
    docs_url = ""
    receive_scope = "firm"
    client_fields: tuple[ConfigField, ...] = ()
    outgoing_mime = "application/xml"
    fields: tuple[ConfigField, ...] = (
        ConfigField("base_url", "Bridge base URL (https://…)", required=True),
        ConfigField("api_token", "API token", secret=True, required=True),
        ConfigField(
            "sender_id",
            "Your sender identifier",
            required=False,
            hint="Participant id / SIREN the provider registered for sending",
        ),
    )

    def __init__(self, http: httpx.AsyncClient | None = None) -> None:
        self.http = http or httpx.AsyncClient(timeout=30, follow_redirects=False)

    # -- helpers -------------------------------------------------------------------------------
    @staticmethod
    def _base(config: dict[str, Any]) -> str:
        return str(config.get("base_url") or "").rstrip("/")

    @staticmethod
    def _headers(config: dict[str, Any]) -> dict[str, str]:
        return {"authorization": f"Bearer {config.get('api_token', '')}", "accept": "application/json"}

    def _check_url(self, config: dict[str, Any]) -> str | None:
        base = self._base(config)
        if not base.startswith("https://"):
            return "The bridge URL must start with https://."
        return None

    # -- protocol ------------------------------------------------------------------------------
    async def test(self, config: dict[str, Any]) -> tuple[bool, str]:
        problem = self._check_url(config)
        if problem:
            return False, problem
        try:
            r = await self.http.get(f"{self._base(config)}/health", headers=self._headers(config), timeout=10)
        except httpx.HTTPError as e:
            return False, f"Bridge not reachable: {type(e).__name__}."
        if r.status_code == 401 or r.status_code == 403:
            return False, "The bridge refused the API token."
        return 200 <= r.status_code < 300, f"Bridge answered {r.status_code}."

    async def receive(self, config: dict[str, Any], cursor: str | None) -> ReceiveResult:
        if self._check_url(config):
            return ReceiveResult()
        base, headers = self._base(config), self._headers(config)
        params = {"since": cursor} if cursor else {}
        r = await self.http.get(f"{base}/inbox", headers=headers, params=params, timeout=30)
        r.raise_for_status()
        body = r.json()
        docs = body.get("documents") if isinstance(body, dict) else None
        out = ReceiveResult(
            cursor=str(body.get("cursor")) if isinstance(body, dict) and body.get("cursor") else None
        )
        for entry in (docs or [])[:MAX_INBOX_PER_PULL]:
            if not isinstance(entry, dict) or not entry.get("id"):
                continue
            ext_id = str(entry["id"])[:200]
            dr = await self.http.get(f"{base}/inbox/{ext_id}", headers=headers, timeout=60)
            if dr.status_code != 200 or len(dr.content) > MAX_DOCUMENT_BYTES or not dr.content:
                log.warning("%s: inbox document %s not fetched (%s)", self.key, ext_id, dr.status_code)
                continue
            mime = str(entry.get("mime") or dr.headers.get("content-type", "application/xml")).split(";")[0]
            out.items.append(
                ReceivedItem(
                    external_id=ext_id,
                    filename=str(entry.get("filename") or f"{ext_id}.xml")[:255],
                    mime=mime[:127],
                    data=dr.content,
                    receiver_id=(str(entry.get("receiver")) if entry.get("receiver") else None),
                    sender_identity=str(entry.get("sender") or "")[:320],
                )
            )
        return out

    async def acknowledge(self, config: dict[str, Any], external_ids: list[str]) -> None:
        base, headers = self._base(config), self._headers(config)
        for ext_id in external_ids:
            try:
                r = await self.http.post(f"{base}/inbox/{ext_id}/ack", headers=headers, timeout=15)
                if r.status_code >= 300:
                    log.warning("%s: ack of %s answered %s", self.key, ext_id, r.status_code)
            except httpx.HTTPError as e:
                log.warning("%s: ack of %s failed: %s", self.key, ext_id, type(e).__name__)

    def outgoing_document(self, item: PushItem) -> tuple[str, bytes, str] | None:
        """(filename, bytes, mime) to send, or None when the document is not sendable."""
        if item.invoice.direction is None or item.invoice.direction.value != "sales":
            return None
        fmt = item.document.structured_format
        if item.original is not None and fmt in ("ubl", "cii"):
            return (
                item.document.original_filename or f"{item.document.id}.xml",
                item.original,
                "application/xml",
            )
        if item.original is not None and fmt == "facturx" and self.key == "france_pdp":
            return (
                item.document.original_filename or f"{item.document.id}.pdf",
                item.original,
                "application/pdf",
            )
        return f"{item.invoice.number or item.document.id}.xml", to_ubl(item.invoice), "application/xml"

    async def push(self, items: list[PushItem], config: dict[str, Any]) -> PushResult:
        result = PushResult()
        base, headers = self._base(config), self._headers(config)
        for item in items:
            payload = self.outgoing_document(item)
            if payload is None:
                result.outcomes.append(
                    PushOutcome(str(item.document.id), False, None, "only sales invoices are sent")
                )
                continue
            receiver = item.invoice.buyer.electronic_address or item.invoice.buyer.vat_id or ""
            name, data, mime = payload
            try:
                r = await self.http.post(
                    f"{base}/outbox",
                    headers=headers,
                    data={
                        "receiver": receiver,
                        "document_type": item.invoice.document_type.value,
                        "sender": str(config.get("sender_id") or ""),
                    },
                    files={"document": (name, data, mime)},
                    timeout=60,
                )
            except httpx.HTTPError as e:
                result.outcomes.append(
                    PushOutcome(str(item.document.id), False, None, f"send failed: {type(e).__name__}")
                )
                continue
            if 200 <= r.status_code < 300:
                try:
                    ext = str(r.json().get("id") or "")
                except ValueError:
                    ext = ""
                result.outcomes.append(
                    PushOutcome(str(item.document.id), True, ext or None, f"sent ({r.status_code})")
                )
            else:
                result.outcomes.append(
                    PushOutcome(str(item.document.id), False, None, f"provider answered {r.status_code}")
                )
        return result


class PeppolConnector(BridgeConnector):
    key = "peppol"
    name = "Peppol (access point)"
    description = (
        "Receive e-invoices addressed to your clients' Peppol participant ids and send outgoing "
        "UBL invoices, through your access point provider's bridge."
    )
    docs_url = "https://docs.peppol.eu/poacc/billing/3.0/"
    routing_field = "peppol_id"


class FrancePdpConnector(BridgeConnector):
    key = "france_pdp"
    name = "France — certified platform (PDP)"
    description = (
        "Receive Factur-X / UBL / CII invoices your clients' SIREN receive on the French platform "
        "and deposit outgoing invoices, through the platform's bridge."
    )
    docs_url = "https://www.impots.gouv.fr/facturation-electronique-entre-entreprises"
    routing_field = "tax_number"
