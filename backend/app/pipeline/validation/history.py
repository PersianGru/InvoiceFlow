"""Cross-checks against the supplier profile history: unusual amount, new address."""

from __future__ import annotations

from app.pipeline.supplier import SupplierHistory
from app.schemas.invoice import Invoice, ValidationLevel, ValidationResult


def check(inv: Invoice, hist: SupplierHistory) -> list[ValidationResult]:
    out: list[ValidationResult] = []
    if hist.document_count < 3:
        return out
    gross = inv.totals.gross_total
    if gross is not None and hist.average_gross and hist.average_gross > 0 and gross > hist.average_gross * 3:
        out.append(
            ValidationResult(
                check="history.unusual_amount",
                level=ValidationLevel.warning,
                reason=(
                    f"Amount {gross} is more than three times this supplier's average "
                    f"({hist.average_gross:.2f})"
                ),
                field="totals.gross_total",
            )
        )
    elif gross is not None:
        out.append(
            ValidationResult(
                check="history.unusual_amount",
                level=ValidationLevel.passed,
                reason="Amount is in line with this supplier's history",
            )
        )
    if hist.last_address and inv.supplier.address:
        now = {k: v for k, v in inv.supplier.address.model_dump().items() if v}
        keys = ("postal_code", "city", "country")
        before = {k: str(hist.last_address.get(k, "")).lower() for k in keys}
        after = {k: str(now.get(k, "")).lower() for k in keys}
        if any(before[k] and after[k] and before[k] != after[k] for k in keys):
            out.append(
                ValidationResult(
                    check="history.address_changed",
                    level=ValidationLevel.warning,
                    reason="The supplier's address differs from the one seen before",
                    field="supplier.address",
                    details={"before": hist.last_address},
                )
            )
    return out
