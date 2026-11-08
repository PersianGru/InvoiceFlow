"""DATEV connector: a Buchungsstapel EXTF CSV plus a ZIP of the originals named by invoice
number (Belegtransfer), delivered to the firm's storage area (download from the dashboard) or to
an HTTPS endpoint of the firm's choice (its DATEV upload bridge) with a bearer token."""

from __future__ import annotations

import io
import logging
import zipfile
from datetime import UTC, datetime
from typing import Any

import httpx

from app.connectors.base import ConfigField, PushItem, PushOutcome, PushResult
from app.exports.columns import ExportRow
from app.exports.datev import render_datev

log = logging.getLogger("invoiceflow.connectors.datev")


class DatevConnector:
    key = "datev"
    name = "DATEV (Buchungsstapel + Belege)"
    description = (
        "Produces a DATEV Buchungsstapel (EXTF CSV) and a ZIP of the originals for Belegtransfer. "
        "Delivered to your InvoiceFlow files or to an HTTPS endpoint you run."
    )
    docs_url = "https://developer.datev.de/"
    fields: tuple[ConfigField, ...] = (
        ConfigField("datev_consultant_number", "Consultant number (Beraternummer)", required=True),
        ConfigField("datev_client_number", "Client number (Mandantennummer)", required=True),
        ConfigField(
            "fiscal_year_start",
            "Fiscal year start (YYYYMMDD)",
            required=False,
            hint="Defaults to 1 January of the invoice year",
        ),
        ConfigField("account_length", "Account length", required=False, default="4"),
        ConfigField("payables_account", "Payables account (Kreditoren)", required=False, default="70000"),
        ConfigField("default_expense_account", "Default expense account", required=False, default="6800"),
        ConfigField("delivery", "Delivery: storage | https", required=False, default="storage"),
        ConfigField(
            "https_url",
            "HTTPS endpoint (delivery = https)",
            required=False,
            hint="Receives multipart/form-data with the CSV and the ZIP",
        ),
        ConfigField("https_token", "Bearer token for the endpoint", secret=True, required=False),
    )

    def __init__(self, http: httpx.AsyncClient | None = None) -> None:
        self.http = http or httpx.AsyncClient(timeout=30, follow_redirects=False)

    async def test(self, config: dict[str, Any]) -> tuple[bool, str]:
        if (
            not str(config.get("datev_consultant_number") or "").isdigit()
            or not str(config.get("datev_client_number") or "").isdigit()
        ):
            return False, "Consultant and client numbers must be digits."
        if str(config.get("delivery") or "storage") == "https":
            url = str(config.get("https_url") or "")
            if not url.startswith("https://"):
                return False, "The delivery endpoint must be an https:// URL."
            try:
                r = await self.http.request("OPTIONS", url, headers=self._headers(config), timeout=10)
                return r.status_code < 500, f"Endpoint answered {r.status_code}."
            except httpx.HTTPError as e:
                return False, f"Endpoint not reachable: {type(e).__name__}."
        return True, "Files will be stored in your InvoiceFlow exports."

    def _headers(self, config: dict[str, Any]) -> dict[str, str]:
        token = str(config.get("https_token") or "")
        return {"authorization": f"Bearer {token}"} if token else {}

    def build(
        self, items: list[PushItem], config: dict[str, Any], stamp: datetime
    ) -> tuple[str, bytes, str, bytes]:
        rows = [ExportRow(i.invoice, i.document, i.client_name) for i in items]
        csv = render_datev(rows, {k: v for k, v in config.items() if not k.startswith("https")}, now=stamp)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            used: set[str] = set()
            for i in items:
                if i.original is None:
                    continue
                stem = "".join(
                    ch for ch in (i.invoice.number or str(i.document.id)) if ch.isalnum() or ch in "._-"
                )[:60]
                ext = (
                    "pdf"
                    if i.document.mime_type == "application/pdf"
                    else (
                        i.document.original_filename.rsplit(".", 1)[-1]
                        if "." in i.document.original_filename
                        else "bin"
                    )
                )
                name = f"{stem}.{ext}"
                if name in used:
                    name = f"{stem}-{str(i.document.id)[:8]}.{ext}"
                used.add(name)
                zf.writestr(name, i.original)
        tag = stamp.strftime("%Y%m%d-%H%M%S")
        return f"EXTF_Buchungsstapel_{tag}.csv", csv, f"Belege_{tag}.zip", buf.getvalue()

    async def push(self, items: list[PushItem], config: dict[str, Any]) -> PushResult:
        stamp = datetime.now(UTC)
        csv_name, csv, zip_name, zip_bytes = self.build(items, config, stamp)
        result = PushResult(artifacts={csv_name: csv, zip_name: zip_bytes})
        delivery = str(config.get("delivery") or "storage")
        if delivery == "https":
            url = str(config.get("https_url") or "")
            try:
                r = await self.http.post(
                    url,
                    headers=self._headers(config),
                    files={
                        "buchungsstapel": (csv_name, csv, "text/csv"),
                        "belege": (zip_name, zip_bytes, "application/zip"),
                    },
                    timeout=60,
                )
                ok, msg = 200 <= r.status_code < 300, f"delivered to endpoint ({r.status_code})"
            except httpx.HTTPError as e:
                ok, msg = False, f"delivery failed: {type(e).__name__}"
        else:
            ok, msg = True, f"stored as {csv_name} and {zip_name}"
        for i in items:
            result.outcomes.append(PushOutcome(str(i.document.id), ok, csv_name if ok else None, msg))
        return result
