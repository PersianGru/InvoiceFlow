"""The flat column registry over the canonical invoice, shared by CSV, XLSX and the template editor.

A template is an ordered list of ``{"key": <column key>, "header": <label>}``; unknown keys are
refused at save time. Values are plain Python (str, Decimal, date, int) and formatted per writer.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from app.models.document import Document
from app.schemas.invoice import Invoice

Kind = Literal["text", "date", "money", "number", "int", "datetime"]


@dataclass(frozen=True, slots=True)
class ColumnSpec:
    key: str
    label: str
    kind: Kind
    group: str


@dataclass(slots=True)
class ExportRow:
    invoice: Invoice
    document: Document
    client_company_name: str | None = None
    line_index: int | None = None  # set when the writer emits one row per line item


def _col(key: str, label: str, kind: Kind, group: str) -> ColumnSpec:
    return ColumnSpec(key, label, kind, group)


COLUMNS: tuple[ColumnSpec, ...] = (
    _col("document_id", "Document id", "text", "document"),
    _col("client_company_name", "Client", "text", "document"),
    _col("status", "Status", "text", "document"),
    _col("review_status", "Review status", "text", "document"),
    _col("channel", "Channel", "text", "document"),
    _col("received_at", "Received", "datetime", "document"),
    _col("original_filename", "File", "text", "document"),
    _col("sha256", "SHA-256", "text", "document"),
    _col("structured_format", "E-invoice format", "text", "document"),
    _col("payment_status", "Payment status", "text", "document"),
    _col("paid_amount", "Paid amount", "money", "document"),
    _col("paid_at", "Paid on", "date", "document"),
    _col("document_type", "Document type", "text", "header"),
    _col("direction", "Direction", "text", "header"),
    _col("number", "Invoice number", "text", "header"),
    _col("issue_date", "Issue date", "date", "header"),
    _col("delivery_date", "Delivery date", "date", "header"),
    _col("period_start", "Period start", "date", "header"),
    _col("period_end", "Period end", "date", "header"),
    _col("currency", "Currency", "text", "header"),
    _col("language", "Language", "text", "header"),
    _col("supplier.name", "Supplier", "text", "supplier"),
    _col("supplier.vat_id", "Supplier VAT id", "text", "supplier"),
    _col("supplier.tax_id", "Supplier tax id", "text", "supplier"),
    _col("supplier.registration_id", "Supplier registration id", "text", "supplier"),
    _col("supplier.iban", "Supplier IBAN", "text", "supplier"),
    _col("supplier.bic", "Supplier BIC", "text", "supplier"),
    _col("supplier.email", "Supplier email", "text", "supplier"),
    _col("supplier.address.line1", "Supplier street", "text", "supplier"),
    _col("supplier.address.postal_code", "Supplier postal code", "text", "supplier"),
    _col("supplier.address.city", "Supplier city", "text", "supplier"),
    _col("supplier.address.country", "Supplier country", "text", "supplier"),
    _col("buyer.name", "Buyer", "text", "buyer"),
    _col("buyer.vat_id", "Buyer VAT id", "text", "buyer"),
    _col("buyer.address.country", "Buyer country", "text", "buyer"),
    _col("totals.line_net_total", "Lines net", "money", "totals"),
    _col("totals.allowances_total", "Allowances", "money", "totals"),
    _col("totals.charges_total", "Charges", "money", "totals"),
    _col("totals.net_total", "Net total", "money", "totals"),
    _col("totals.vat_total", "VAT total", "money", "totals"),
    _col("totals.gross_total", "Gross total", "money", "totals"),
    _col("totals.prepaid", "Prepaid", "money", "totals"),
    _col("totals.rounding", "Rounding", "money", "totals"),
    _col("allowances_text", "Document discounts and charges", "text", "totals"),
    _col("totals.amount_due", "Amount due", "money", "totals"),
    _col("vat_rate_main", "Main VAT rate %", "number", "totals"),
    _col("exchange_rate", "Exchange rate", "number", "totals"),
    _col("converted_currency", "Converted currency", "text", "totals"),
    _col("converted_gross_total", "Converted gross", "money", "totals"),
    _col("payment.due_date", "Due date", "date", "payment"),
    _col("payment.payment_reference", "Payment reference", "text", "payment"),
    _col("payment.payment_means_code", "Payment means", "text", "payment"),
    _col("payment.terms_text", "Payment terms", "text", "payment"),
    _col("payment.discount_percent", "Early-payment discount %", "number", "payment"),
    _col("payment.discount_until", "Early-payment discount until", "date", "payment"),
    _col("references.purchase_order", "Purchase order", "text", "references"),
    _col("references.contract", "Contract", "text", "references"),
    _col("references.buyer_reference", "Buyer reference", "text", "references"),
    _col("references.preceding_invoice", "Preceding invoice", "text", "references"),
    _col("reverse_charge", "Reverse charge", "text", "tax"),
    _col("intra_community", "Intra-community", "text", "tax"),
    _col("export", "Export", "text", "tax"),
    _col("suggested_gl_account", "GL account", "text", "accounting"),
    _col("suggested_cost_category", "Cost category", "text", "accounting"),
    _col("suggested_vat_code", "VAT code", "text", "accounting"),
    _col("line_count", "Lines", "int", "lines"),
    _col("line.id", "Line id", "text", "lines"),
    _col("line.description", "Line description", "text", "lines"),
    _col("line.quantity", "Line quantity", "number", "lines"),
    _col("line.unit", "Line unit", "text", "lines"),
    _col("line.unit_price", "Line unit price", "money", "lines"),
    _col("line.allowance_amount", "Line discount", "money", "lines"),
    _col("line.allowance_reason", "Line discount reason", "text", "lines"),
    _col("line.charge_amount", "Line charge", "money", "lines"),
    _col("line.net_amount", "Line net", "money", "lines"),
    _col("line.vat_rate", "Line VAT %", "number", "lines"),
    _col("line.vat_category", "Line VAT category", "text", "lines"),
    _col("line.gl_account", "Line GL account", "text", "lines"),
    _col("validation_worst", "Validation", "text", "review"),
    _col("validation_errors", "Validation errors", "int", "review"),
    _col("min_confidence", "Min. confidence", "number", "review"),
)
COLUMN_INDEX: dict[str, ColumnSpec] = {c.key: c for c in COLUMNS}

DEFAULT_COLUMNS: tuple[str, ...] = (
    "number",
    "issue_date",
    "supplier.name",
    "supplier.vat_id",
    "supplier.iban",
    "currency",
    "totals.net_total",
    "totals.vat_total",
    "totals.gross_total",
    "vat_rate_main",
    "payment.due_date",
    "payment.payment_reference",
    "document_type",
    "suggested_gl_account",
    "suggested_vat_code",
    "client_company_name",
    "status",
    "document_id",
)


def default_template_columns() -> list[dict[str, str]]:
    return [{"key": k, "header": COLUMN_INDEX[k].label} for k in DEFAULT_COLUMNS]


def validate_columns(columns: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Normalise a template's column list; raises ``ValueError`` naming the first unknown key."""
    out: list[dict[str, str]] = []
    for c in columns:
        key = str(c.get("key", "")).strip()
        if key not in COLUMN_INDEX:
            raise ValueError(f"unknown column '{key}'")
        header = str(c.get("header") or COLUMN_INDEX[key].label).strip()[:80]
        out.append({"key": key, "header": header})
    if not out:
        raise ValueError("a template needs at least one column")
    return out


def _dotted(obj: Any, path: str) -> Any:
    cur = obj
    for part in path.split("."):
        if cur is None:
            return None
        cur = getattr(cur, part, None) if not isinstance(cur, dict) else cur.get(part)
    return cur


def _bool_text(v: bool | None) -> str | None:
    if v is None:
        return None
    return "yes" if v else "no"


def main_vat_rate(inv: Invoice) -> Decimal | None:
    best: tuple[Decimal, Decimal] | None = None
    for b in inv.vat_breakdown:
        if b.rate is None:
            continue
        base = b.taxable_amount or Decimal(0)
        if best is None or base > best[0]:
            best = (base, b.rate)
    if best is not None:
        return best[1]
    rates = [ln.vat_rate for ln in inv.lines if ln.vat_rate is not None]
    return max(rates) if rates else None


def value_of(row: ExportRow, key: str) -> Any:
    inv, doc = row.invoice, row.document
    if key == "document_id":
        return str(doc.id)
    if key == "client_company_name":
        return row.client_company_name
    if key == "status":
        return doc.status.value
    if key == "review_status":
        return inv.review_status.value
    if key == "channel":
        return doc.channel.value
    if key == "received_at":
        return doc.received_at
    if key == "original_filename":
        return doc.original_filename
    if key == "sha256":
        return doc.sha256
    if key == "structured_format":
        return doc.structured_format
    if key == "payment_status":
        return doc.payment_status
    if key == "paid_amount":
        return doc.paid_amount
    if key == "paid_at":
        return doc.paid_at
    if key == "document_type":
        return inv.document_type.value
    if key == "direction":
        return inv.direction.value if inv.direction else None
    if key == "vat_rate_main":
        return main_vat_rate(inv)
    if key == "line_count":
        return len(inv.lines)
    if key == "allowances_text":  # "-15.00 Volume discount; +9.90 Freight"
        return (
            "; ".join(
                f"{'+' if a.charge else '-'}{a.amount} {a.reason or a.reason_code or ''}".strip()
                for a in inv.allowances_charges
                if a.amount is not None
            )
            or None
        )
    if key in ("reverse_charge", "intra_community", "export"):
        return _bool_text(getattr(inv, key))
    if key == "validation_worst":
        levels = [v.level.value for v in inv.validation]
        if "error" in levels:
            return "error"
        return "warning" if "warning" in levels else ("pass" if levels else None)
    if key == "validation_errors":
        return sum(1 for v in inv.validation if v.level.value == "error")
    if key == "min_confidence":
        return doc.min_confidence
    if key.startswith("line."):
        if row.line_index is None or row.line_index >= len(inv.lines):
            return None
        return _dotted(inv.lines[row.line_index], key[5:])
    return _dotted(inv, key)


def format_value(
    value: Any, kind: Kind, *, decimal_separator: str = ".", date_format: str = "%Y-%m-%d"
) -> str:
    """Text form for CSV/DATEV; XLSX keeps native types."""
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.strftime(date_format + " %H:%M")
    if isinstance(value, date):
        return value.strftime(date_format)
    if isinstance(value, Decimal | float | int) and not isinstance(value, bool):
        if kind == "money":
            text = f"{Decimal(str(value)).quantize(Decimal('0.01'))}"
        else:
            text = f"{Decimal(str(value)).normalize():f}"
        return text.replace(".", decimal_separator) if decimal_separator != "." else text
    return str(value)
