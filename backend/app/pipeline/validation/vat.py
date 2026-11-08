"""VAT: rate plausibility per country and date (rule pack), reverse-charge / intra-EU / export
detection with the legal note, VAT id format and VIES validation (cached, downtime-tolerant)."""

from __future__ import annotations

import re
from decimal import Decimal

from app.pipeline.validation.vies import ViesClient
from app.rules import plausible_rate, vat_rates
from app.schemas.invoice import Invoice, ValidationLevel, ValidationResult

EU = frozenset("AT BE BG CY CZ DE DK EE ES FI FR GR HR HU IE IT LT LU LV MT NL PL PT RO SE SI SK".split())
_REVERSE_NOTES = (
    "reverse charge",
    "reverse-charge",
    "steuerschuldnerschaft des leistungsempfängers",
    "umkehr der steuerschuldnerschaft",
    "autoliquidation",
    "btw verlegd",
    "verlegd",
    "inversión del sujeto pasivo",
    "inversione contabile",
    "odwrotne obciążenie",
    "article 196",
    "art. 196",
    "§ 13b",
    "13b ustg",
)
_EXEMPT_NOTES = ("exempt", "steuerfrei", "exonéré", "vrijgesteld", "exento", "esente", "zwolnion")
_FORMATS = {
    "AT": r"U\d{8}",
    "BE": r"[01]\d{9}",
    "BG": r"\d{9,10}",
    "CY": r"\d{8}[A-Z]",
    "CZ": r"\d{8,10}",
    "DE": r"\d{9}",
    "DK": r"\d{8}",
    "EE": r"\d{9}",
    "ES": r"[A-Z0-9]\d{7}[A-Z0-9]",
    "FI": r"\d{8}",
    "FR": r"[A-Z0-9]{2}\d{9}",
    "GR": r"\d{9}",
    "EL": r"\d{9}",
    "HR": r"\d{11}",
    "HU": r"\d{8}",
    "IE": r"\d[A-Z0-9+*]\d{5}[A-Z]{1,2}",
    "IT": r"\d{11}",
    "LT": r"(\d{9}|\d{12})",
    "LU": r"\d{8}",
    "LV": r"\d{11}",
    "MT": r"\d{8}",
    "NL": r"\d{9}B\d{2}",
    "PL": r"\d{10}",
    "PT": r"\d{9}",
    "RO": r"\d{2,10}",
    "SE": r"\d{12}",
    "SI": r"\d{8}",
    "SK": r"\d{10}",
    "GB": r"(\d{9}|\d{12}|GD\d{3}|HA\d{3})",
    "CH": r"E\d{9}",
}


def country_of(vat_id: str | None) -> str | None:
    if not vat_id or len(vat_id) < 3:
        return None
    cc = vat_id[:2].upper()
    return "GR" if cc == "EL" else cc


def format_ok(vat_id: str) -> bool:
    cc = vat_id[:2].upper()
    pat = _FORMATS.get(cc)
    if not pat:
        return False
    return re.fullmatch(pat, vat_id[2:].upper()) is not None


def check(inv: Invoice, firm_country: str) -> list[ValidationResult]:
    out: list[ValidationResult] = []
    supplier_cc = (
        country_of(inv.supplier.vat_id)
        or (inv.supplier.address.country if inv.supplier.address else None)
        or firm_country
    )
    buyer_cc = country_of(inv.buyer.vat_id) or (inv.buyer.address.country if inv.buyer.address else None)
    # format
    for label, vid in (("supplier", inv.supplier.vat_id), ("buyer", inv.buyer.vat_id)):
        if vid:
            if format_ok(vid):
                out.append(
                    ValidationResult(
                        check=f"vat.{label}_id_format",
                        level=ValidationLevel.passed,
                        reason=f"{label.title()} VAT id has a valid format",
                    )
                )
            else:
                out.append(
                    ValidationResult(
                        check=f"vat.{label}_id_format",
                        level=ValidationLevel.warning,
                        reason=f"{label.title()} VAT id {vid} does not match the format of {vid[:2]}",
                        field=f"{label}.vat_id",
                    )
                )
    # rates
    rates = {b.rate for b in inv.vat_breakdown if b.rate is not None} | {
        ln.vat_rate for ln in inv.lines if ln.vat_rate is not None
    }
    if rates and supplier_cc:
        bad = [r for r in rates if not plausible_rate(supplier_cc, Decimal(r), inv.issue_date)]
        if bad and vat_rates(supplier_cc):
            out.append(
                ValidationResult(
                    check="vat.rate_plausible",
                    level=ValidationLevel.warning,
                    reason=f"VAT rate {', '.join(f'{r}%' for r in sorted(bad))} is not a rate used in "
                    f"{supplier_cc}" + (f" on {inv.issue_date}" if inv.issue_date else ""),
                    field="vat_breakdown",
                    details={"country": supplier_cc, "rates": [str(r) for r in bad]},
                )
            )
        elif vat_rates(supplier_cc):
            out.append(
                ValidationResult(
                    check="vat.rate_plausible",
                    level=ValidationLevel.passed,
                    reason=f"VAT rates are valid for {supplier_cc}",
                )
            )
    # reverse charge / intra-EU / export
    notes = " ".join(
        inv.notes + [ln.description or "" for ln in inv.lines] + [inv.payment.terms_text or ""]
    ).lower()
    zero_vat = (
        inv.totals.vat_total is not None and inv.totals.vat_total == 0 and (inv.totals.net_total or 0) > 0
    )
    cross_border = supplier_cc and buyer_cc and supplier_cc != buyer_cc
    if zero_vat and cross_border and supplier_cc in EU and buyer_cc in EU:
        has_note = any(n in notes for n in _REVERSE_NOTES)
        has_both_ids = bool(inv.supplier.vat_id and inv.buyer.vat_id)
        level = ValidationLevel.passed if has_note and has_both_ids else ValidationLevel.warning
        reason = (
            "Intra-EU reverse charge: VAT 0 with both VAT ids and the reverse-charge note"
            if level == ValidationLevel.passed
            else "Looks like an intra-EU reverse-charge invoice but "
            + ("the reverse-charge note is missing" if not has_note else "a VAT id is missing")
        )
        out.append(
            ValidationResult(
                check="vat.reverse_charge",
                level=level,
                reason=reason,
                field="notes",
                details={"intra_community": True},
            )
        )
    elif zero_vat and cross_border and buyer_cc not in EU:
        out.append(
            ValidationResult(
                check="vat.export",
                level=ValidationLevel.passed,
                reason=f"Export to {buyer_cc}: zero VAT is expected",
                details={"export": True},
            )
        )
    elif zero_vat and not cross_border:
        if any(n in notes for n in _EXEMPT_NOTES) or any(
            b.exemption_reason or b.exemption_reason_code for b in inv.vat_breakdown
        ):
            out.append(
                ValidationResult(
                    check="vat.zero_domestic",
                    level=ValidationLevel.passed,
                    reason="Zero VAT with an exemption reason",
                )
            )
        else:
            out.append(
                ValidationResult(
                    check="vat.zero_domestic",
                    level=ValidationLevel.warning,
                    reason="Domestic invoice with zero VAT and no exemption reason",
                    field="totals.vat_total",
                )
            )
    return out


async def check_vies(inv: Invoice, vies: ViesClient) -> list[ValidationResult]:
    out: list[ValidationResult] = []
    for label, vid in (("supplier", inv.supplier.vat_id), ("buyer", inv.buyer.vat_id)):
        if not vid or country_of(vid) not in EU or not format_ok(vid):
            continue
        res = await vies.check(vid)
        if res.valid is None:
            out.append(
                ValidationResult(
                    check=f"vat.{label}_vies",
                    level=ValidationLevel.warning,
                    reason=f"VIES could not be reached to verify the {label} VAT id; check it later",
                    field=f"{label}.vat_id",
                    details={"source": res.source},
                )
            )
        elif res.valid:
            out.append(
                ValidationResult(
                    check=f"vat.{label}_vies",
                    level=ValidationLevel.passed,
                    reason=f"{label.title()} VAT id is registered in VIES"
                    + (f" ({res.name})" if res.name else ""),
                    details={"name": res.name, "source": res.source},
                )
            )
        else:
            out.append(
                ValidationResult(
                    check=f"vat.{label}_vies",
                    level=ValidationLevel.error,
                    reason=f"{label.title()} VAT id {vid} is not valid according to VIES",
                    field=f"{label}.vat_id",
                    details={"source": res.source},
                )
            )
    return out
