"""Combine the results of several providers into one invoice with per-field confidence.

Rule: for each header field, take the value with the highest confidence; when two providers agree,
raise the confidence (bounded); when they disagree, lower the winner's confidence so the review
queue sees it. Lines and VAT breakdown come from the provider with the most line detail; document
discounts and charges from the provider that found the most of them.
"""

from __future__ import annotations

from typing import Any

from app.pipeline.types import ExtractionResult
from app.schemas.invoice import Invoice

HEADER_PATHS = (
    "document_type",
    "number",
    "issue_date",
    "delivery_date",
    "currency",
    "language",
    "reverse_charge",
    "supplier.name",
    "supplier.vat_id",
    "supplier.tax_id",
    "supplier.registration_id",
    "supplier.iban",
    "supplier.bic",
    "supplier.email",
    "buyer.name",
    "buyer.vat_id",
    "totals.net_total",
    "totals.vat_total",
    "totals.gross_total",
    "totals.amount_due",
    "totals.allowances_total",
    "totals.charges_total",
    "totals.prepaid",
    "totals.rounding",
    "payment.due_date",
    "payment.discount_percent",
    "payment.discount_until",
    "payment.terms_text",
    "payment.payment_reference",
    "references.purchase_order",
)


def _get(d: dict[str, Any], path: str) -> Any:
    cur: Any = d
    for p in path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(p)
    return cur


def _set(d: dict[str, Any], path: str, value: Any) -> None:
    parts = path.split(".")
    cur = d
    for p in parts[:-1]:
        cur = cur.setdefault(p, {})
    cur[parts[-1]] = value


def _same(a: Any, b: Any) -> bool:
    return str(a).strip().lower().replace(" ", "") == str(b).strip().lower().replace(" ", "")


def merge_results(results: list[ExtractionResult]) -> ExtractionResult:
    if len(results) == 1:
        return results[0]
    dumps = [(r, r.invoice.model_dump(mode="json")) for r in results]
    base_result, base = max(dumps, key=lambda rd: len(rd[1].get("lines") or []))
    out: dict[str, Any] = {k: v for k, v in base.items() if k not in ("meta",)}
    meta: dict[str, Any] = {}
    for path in HEADER_PATHS:
        candidates = [
            (d["meta"][path].get("confidence", 0.0), _get(d, path), d["meta"][path])
            for _, d in dumps
            if _get(d, path) not in (None, "", {}) and path in d.get("meta", {})
        ]
        if not candidates:
            continue
        candidates.sort(key=lambda c: c[0], reverse=True)
        conf, value, m = candidates[0]
        agree = sum(1 for c in candidates[1:] if _same(c[1], value))
        disagree = len(candidates) - 1 - agree
        if agree:
            conf = min(0.99, conf + 0.1 * agree)
        if disagree:
            conf = max(0.05, conf - 0.25 * disagree)
        _set(out, path, value)
        meta[path] = {**(m or {}), "confidence": round(conf, 3), "agreed_by": agree + 1}
    for key in ("lines", "vat_breakdown"):
        if base.get("meta", {}).get(key):
            meta[key] = base["meta"][key]
    _, ac_src = max(dumps, key=lambda rd: len(rd[1].get("allowances_charges") or []))
    out["allowances_charges"] = ac_src.get("allowances_charges") or []
    if ac_src.get("meta", {}).get("allowances_charges"):
        meta["allowances_charges"] = ac_src["meta"]["allowances_charges"]
    out["meta"] = meta
    invoice = Invoice.model_validate(out)
    providers = "+".join(r.provider for r in results)
    return ExtractionResult(
        invoice,
        providers,
        base_result.model,
        base_result.prompt_version,
        sum(r.tokens_in or 0 for r in results) or None,
        sum(r.tokens_out or 0 for r in results) or None,
        sum(r.duration_ms or 0 for r in results) or None,
    )


def min_header_confidence(
    invoice: Invoice,
    required: tuple[str, ...] = ("number", "issue_date", "supplier.name", "totals.gross_total", "currency"),
) -> float:
    meta = invoice.meta
    confs = [meta[p].confidence if p in meta else 0.0 for p in required]
    return min(confs) if confs else 0.0
