"""Classification: document type sanity, purchase vs sales, suggestions from the
supplier profile (GL account, cost category, VAT code)."""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.models.pipeline import SupplierProfile
from app.schemas.invoice import Direction, DocumentType, Invoice, ValueSource


def _clean(v: str | None) -> str:
    return re.sub(r"[\s.\-]", "", v or "").upper()


@dataclass(frozen=True, slots=True)
class Classification:
    document_type: DocumentType
    direction: Direction | None
    suggested_gl_account: str | None
    suggested_cost_category: str | None
    suggested_vat_code: str | None


def classify(invoice: Invoice, own_vat_ids: list[str], profile: SupplierProfile | None) -> Classification:
    own = {_clean(v) for v in own_vat_ids if v}
    doc_type = invoice.document_type
    if doc_type == DocumentType.non_invoice and (invoice.number or invoice.totals.gross_total is not None):
        doc_type = DocumentType.invoice  # it has the shape of an invoice; the review queue decides
    sup, buy = _clean(invoice.supplier.vat_id), _clean(invoice.buyer.vat_id)
    direction: Direction | None
    if sup and sup in own:
        direction = Direction.sales
    elif buy and buy in own:
        direction = Direction.purchase
    elif sup or invoice.supplier.name:
        direction = Direction.purchase  # documents received by a client are purchases by default
    else:
        direction = None
    gl = cost = vat_code = None
    if profile is not None:
        gl, cost, vat_code = (
            profile.default_gl_account,
            profile.default_cost_category,
            profile.default_vat_code,
        )
    if vat_code is None and invoice.vat_breakdown:
        rates = {b.rate for b in invoice.vat_breakdown if b.rate is not None}
        if invoice.reverse_charge:
            vat_code = "RC"
        elif len(rates) == 1:
            vat_code = f"S{next(iter(rates)):.0f}"
    return Classification(doc_type, direction, gl, cost, vat_code)


def apply_classification(invoice: Invoice, c: Classification) -> Invoice:
    updates = {
        "document_type": c.document_type,
        "direction": c.direction,
        "suggested_gl_account": c.suggested_gl_account,
        "suggested_cost_category": c.suggested_cost_category,
        "suggested_vat_code": c.suggested_vat_code,
    }
    inv = invoice.model_copy(update={k: v for k, v in updates.items() if v is not None})
    if c.direction is not None and "direction" not in inv.meta:
        inv.meta["direction"] = inv.meta.get("supplier.vat_id") or _meta(0.6)
    return inv


def _meta(conf: float):  # type: ignore[no-untyped-def]
    from app.schemas.invoice import FieldMeta

    return FieldMeta(confidence=conf, source=ValueSource.supplier_rule, ai_generated=True)
