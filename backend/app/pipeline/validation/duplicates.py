"""Duplicates: exact (supplier + number + amount), fuzzy (number + amount, or supplier + amount + date),
and perceptual-hash matches across channels. The candidate lookup is injected (a DB query)."""

from __future__ import annotations

from typing import Any

from app.schemas.invoice import Invoice, ValidationLevel, ValidationResult


def check(inv: Invoice, matches: list[dict[str, Any]]) -> list[ValidationResult]:
    """``matches``: rows with keys id, kind ('exact' | 'fuzzy' | 'image'), number, gross_total, issue_date."""
    if not matches:
        return [
            ValidationResult(
                check="duplicates.exact", level=ValidationLevel.passed, reason="No duplicate found"
            )
        ]
    out: list[ValidationResult] = []
    exact = [m for m in matches if m.get("kind") == "exact"]
    fuzzy = [m for m in matches if m.get("kind") == "fuzzy"]
    image = [m for m in matches if m.get("kind") == "image"]
    if exact:
        out.append(
            ValidationResult(
                check="duplicates.exact",
                level=ValidationLevel.error,
                reason=(
                    f"Same supplier, number and amount as document {exact[0]['id']}: looks like a duplicate"
                ),
                field="number",
                details={"documents": [str(m["id"]) for m in exact]},
            )
        )
    if fuzzy:
        out.append(
            ValidationResult(
                check="duplicates.fuzzy",
                level=ValidationLevel.warning,
                reason=(
                    f"Similar to document {fuzzy[0]['id']} (same number, or amount and date): "
                    "possible duplicate"
                ),
                details={"documents": [str(m["id"]) for m in fuzzy]},
            )
        )
    if image:
        out.append(
            ValidationResult(
                check="duplicates.image",
                level=ValidationLevel.warning,
                reason=f"The image looks like document {image[0]['id']} received through another channel",
                details={"documents": [str(m["id"]) for m in image]},
            )
        )
    return out
