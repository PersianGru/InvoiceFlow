"""IBAN checksum (ISO 13616 mod 97), BIC format, and the fraud signal: a known supplier's IBAN changed."""

from __future__ import annotations

import re

from app.pipeline.supplier import SupplierHistory
from app.schemas.invoice import Invoice, ValidationLevel, ValidationResult

_IBAN_LENGTHS = {
    "AT": 20,
    "BE": 16,
    "BG": 22,
    "CH": 21,
    "CY": 28,
    "CZ": 24,
    "DE": 22,
    "DK": 18,
    "EE": 20,
    "ES": 24,
    "FI": 18,
    "FR": 27,
    "GB": 22,
    "GR": 27,
    "HR": 21,
    "HU": 28,
    "IE": 22,
    "IT": 27,
    "LI": 21,
    "LT": 20,
    "LU": 20,
    "LV": 21,
    "MT": 31,
    "NL": 18,
    "NO": 15,
    "PL": 28,
    "PT": 25,
    "RO": 24,
    "SE": 24,
    "SI": 19,
    "SK": 24,
}
_BIC_RE = re.compile(r"^[A-Z]{6}[A-Z0-9]{2}([A-Z0-9]{3})?$")


def iban_valid(iban: str) -> bool:
    s = re.sub(r"\s", "", iban or "").upper()
    if len(s) < 15 or len(s) > 34 or not s[:2].isalpha() or not s[2:4].isdigit():
        return False
    expected = _IBAN_LENGTHS.get(s[:2])
    if expected and len(s) != expected:
        return False
    rearranged = s[4:] + s[:4]
    digits = "".join(str(int(ch, 36)) for ch in rearranged)
    return int(digits) % 97 == 1


def bic_valid(bic: str) -> bool:
    return _BIC_RE.fullmatch((bic or "").upper()) is not None


def check(inv: Invoice, hist: SupplierHistory) -> list[ValidationResult]:
    out: list[ValidationResult] = []
    iban = inv.supplier.iban
    if iban:
        if iban_valid(iban):
            out.append(
                ValidationResult(
                    check="iban.checksum", level=ValidationLevel.passed, reason="IBAN checksum is valid"
                )
            )
        else:
            out.append(
                ValidationResult(
                    check="iban.checksum",
                    level=ValidationLevel.error,
                    reason=f"IBAN {iban} has an invalid checksum",
                    field="supplier.iban",
                )
            )
        if hist.known_ibans and iban.upper() not in [k.upper() for k in hist.known_ibans]:
            out.append(
                ValidationResult(
                    check="iban.changed",
                    level=ValidationLevel.error,
                    reason=(
                        "This supplier's bank account differs from the accounts seen before: "
                        "verify before paying (possible invoice fraud)"
                    ),
                    field="supplier.iban",
                    details={"known": hist.known_ibans},
                )
            )
        elif hist.known_ibans:
            out.append(
                ValidationResult(
                    check="iban.changed",
                    level=ValidationLevel.passed,
                    reason="Bank account matches the supplier's known accounts",
                )
            )
    if inv.supplier.bic:
        if bic_valid(inv.supplier.bic):
            out.append(
                ValidationResult(
                    check="iban.bic_format", level=ValidationLevel.passed, reason="BIC format is valid"
                )
            )
        else:
            out.append(
                ValidationResult(
                    check="iban.bic_format",
                    level=ValidationLevel.warning,
                    reason=f"BIC {inv.supplier.bic} does not look valid",
                    field="supplier.bic",
                )
            )
    return out
