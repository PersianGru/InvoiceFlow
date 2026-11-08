"""Mandatory invoice fields (EU VAT Directive art. 226 + country extras) from the rule pack."""

from __future__ import annotations

from typing import Any

from app.rules import mandatory_fields, pack_version
from app.schemas.invoice import DocumentType, Invoice, ValidationLevel, ValidationResult


def _present(inv: Invoice, path: str) -> bool:
    d: Any = inv.model_dump(mode="json")
    if path == "supplier.tax_id_or_vat_id":
        return bool(d["supplier"].get("tax_id") or d["supplier"].get("vat_id"))
    if path == "delivery_date_or_period":
        return bool(d.get("delivery_date") or d.get("period_start") or d.get("period_end"))
    for p in path.split("."):
        if not isinstance(d, dict):
            return False
        d = d.get(p)
    return d not in (None, "", [], {})


def check(inv: Invoice, firm_country: str) -> list[ValidationResult]:
    if inv.document_type in (DocumentType.receipt, DocumentType.non_invoice, DocumentType.reminder):
        return []
    country = (
        inv.supplier.address.country if inv.supplier.address and inv.supplier.address.country else None
    ) or firm_country
    missing: list[dict[str, Any]] = []
    reverse_or_exempt = bool(inv.reverse_charge) or (
        inv.totals.vat_total == 0
        and any(b.exemption_reason or b.exemption_reason_code for b in inv.vat_breakdown)
    )
    for rule in mandatory_fields(country):
        if rule.get("unless") == "reverse_charge_or_exempt" and reverse_or_exempt:
            continue
        if not _present(inv, rule["field"]):
            missing.append(rule)
    out: list[ValidationResult] = []
    for rule in missing:
        level = ValidationLevel.warning if rule.get("level") == "warning" else ValidationLevel.error
        out.append(
            ValidationResult(
                check="mandatory.field",
                level=level,
                reason=f"Missing {rule['field']}: {rule['reason']}",
                field=rule["field"],
                details={"bt": rule.get("bt"), "pack": pack_version("mandatory_fields"), "country": country},
            )
        )
    if not missing:
        out.append(
            ValidationResult(
                check="mandatory.field",
                level=ValidationLevel.passed,
                reason=f"All mandatory fields for {country} are present",
            )
        )
    return out
