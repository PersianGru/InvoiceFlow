"""Lexware Office (lexoffice) connector: bookkeeping vouchers through the public REST API
(``POST /v1/vouchers``, then the original as ``POST /v1/vouchers/{id}/files``)."""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any

import httpx

from app.connectors.base import ConfigField, PushItem, PushOutcome, PushResult
from app.schemas.invoice import Invoice

log = logging.getLogger("invoiceflow.connectors.lexoffice")

DEFAULT_BASE = "https://api.lexoffice.io/v1"
# lexoffice's fixed posting category for "Sonstige Betriebskosten"; firms may override per client later.
DEFAULT_CATEGORY = "8f8664a8-fd86-11e1-a21f-0800200c9a66"


def _amount(v: Decimal | None) -> float:
    return float(v or Decimal(0))


def voucher_payload(inv: Invoice, category_id: str) -> dict[str, Any]:
    is_sales = inv.direction is not None and inv.direction.value == "sales"
    vtype = "salesinvoice" if is_sales else "purchaseinvoice"
    if inv.document_type.value == "credit_note":
        vtype = "salescreditnote" if is_sales else "purchasecreditnote"
    items = []
    if inv.vat_breakdown:
        for b in inv.vat_breakdown:
            net, tax = _amount(b.taxable_amount), _amount(b.tax_amount)
            items.append(
                {
                    "amount": round(net + tax, 2),
                    "taxAmount": round(tax, 2),
                    "taxRatePercent": float(b.rate or 0),
                    "categoryId": category_id,
                }
            )
    else:
        items.append(
            {
                "amount": _amount(inv.totals.gross_total),
                "taxAmount": _amount(inv.totals.vat_total),
                "taxRatePercent": (
                    float(inv.lines[0].vat_rate) if inv.lines and inv.lines[0].vat_rate is not None else 0.0
                ),
                "categoryId": category_id,
            }
        )
    return {
        "type": vtype,
        "voucherNumber": (inv.number or "")[:50],
        "voucherDate": inv.issue_date.isoformat() if inv.issue_date else None,
        "dueDate": inv.payment.due_date.isoformat() if inv.payment.due_date else None,
        "totalGrossAmount": _amount(inv.totals.gross_total),
        "totalTaxAmount": _amount(inv.totals.vat_total),
        "taxType": "gross",
        "useCollectiveContact": True,
        "remark": f"InvoiceFlow {inv.id} · {inv.supplier.name or ''}"[:255],
        "voucherItems": items,
    }


class LexofficeConnector:
    key = "lexoffice"
    name = "Lexware Office (lexoffice)"
    description = "Creates bookkeeping vouchers in Lexware Office and attaches the original document."
    docs_url = "https://developers.lexoffice.io/docs/"
    fields: tuple[ConfigField, ...] = (
        ConfigField("api_key", "API key", secret=True, required=True, hint="Lexware Office → Public API"),
        ConfigField("category_id", "Posting category id", required=False, default=DEFAULT_CATEGORY),
        ConfigField("base_url", "API base URL", required=False, default=DEFAULT_BASE),
    )

    def __init__(self, http: httpx.AsyncClient | None = None) -> None:
        self.http = http or httpx.AsyncClient(timeout=30, follow_redirects=False)

    def _base(self, config: dict[str, Any]) -> str:
        return str(config.get("base_url") or DEFAULT_BASE).rstrip("/")

    def _headers(self, config: dict[str, Any]) -> dict[str, str]:
        return {"authorization": f"Bearer {config.get('api_key', '')}", "accept": "application/json"}

    async def test(self, config: dict[str, Any]) -> tuple[bool, str]:
        if not str(config.get("api_key") or ""):
            return False, "An API key is required."
        try:
            r = await self.http.get(
                f"{self._base(config)}/profile", headers=self._headers(config), timeout=15
            )
        except httpx.HTTPError as e:
            return False, f"Not reachable: {type(e).__name__}."
        if r.status_code == 200:
            name = r.json().get("companyName") or r.json().get("organizationId", "")
            return True, f"Connected to {name}."
        return False, f"Lexware Office answered {r.status_code}."

    async def push(self, items: list[PushItem], config: dict[str, Any]) -> PushResult:
        result = PushResult()
        base, headers = self._base(config), self._headers(config)
        category = str(config.get("category_id") or DEFAULT_CATEGORY)
        for item in items:
            try:
                r = await self.http.post(
                    f"{base}/vouchers",
                    headers={**headers, "content-type": "application/json"},
                    json=voucher_payload(item.invoice, category),
                    timeout=30,
                )
                if r.status_code not in (200, 201):
                    result.outcomes.append(
                        PushOutcome(
                            str(item.document.id),
                            False,
                            None,
                            f"voucher refused ({r.status_code}): {r.text[:120]}",
                        )
                    )
                    continue
                voucher_id = str(r.json().get("id", ""))
                msg = "voucher created"
                if item.original is not None and voucher_id:
                    f = await self.http.post(
                        f"{base}/vouchers/{voucher_id}/files",
                        headers=headers,
                        files={
                            "file": (
                                item.document.original_filename or "document",
                                item.original,
                                item.document.mime_type,
                            )
                        },
                        data={"type": "voucher"},
                        timeout=60,
                    )
                    msg += (
                        "; file attached"
                        if f.status_code in (200, 201, 202)
                        else f"; file refused ({f.status_code})"
                    )
                result.outcomes.append(PushOutcome(str(item.document.id), True, voucher_id or None, msg))
            except httpx.HTTPError as e:
                result.outcomes.append(
                    PushOutcome(str(item.document.id), False, None, f"request failed: {type(e).__name__}")
                )
        return result
