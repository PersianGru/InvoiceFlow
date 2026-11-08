"""Validation and sanity checks. Each check yields ``ValidationResult`` rows
(``pass`` | ``warning`` | ``error``) with a human-readable reason shown in the UI."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from app.pipeline.supplier import SupplierHistory
from app.pipeline.validation import arithmetic, currency, dates, duplicates, history, iban, mandatory, vat
from app.pipeline.validation.currency import RatesProvider
from app.pipeline.validation.vies import ViesClient
from app.schemas.invoice import Invoice, ValidationLevel, ValidationResult

log = logging.getLogger("invoiceflow.pipeline.validation")

DuplicateFinder = Callable[[Invoice, str | None], Awaitable[list[dict[str, Any]]]]


@dataclass(slots=True)
class ValidationContext:
    today: date
    firm_country: str
    base_currency: str = "EUR"
    supplier_history: SupplierHistory = field(default_factory=SupplierHistory.empty)
    vies: ViesClient | None = None
    rates: RatesProvider | None = None
    find_duplicates: DuplicateFinder | None = None
    phash: str | None = None
    rounding_tolerance: Decimal = Decimal("0.02")
    relative_tolerance: Decimal = Decimal("0.005")


async def run_validations(invoice: Invoice, ctx: ValidationContext) -> tuple[Invoice, list[ValidationResult]]:
    """Runs every check; returns the (possibly enriched: converted amounts) invoice and the results."""
    results: list[ValidationResult] = []
    results += arithmetic.check(invoice, ctx.rounding_tolerance, ctx.relative_tolerance)
    results += vat.check(invoice, ctx.firm_country)
    results += iban.check(invoice, ctx.supplier_history)
    results += mandatory.check(invoice, ctx.firm_country)
    results += dates.check(invoice, ctx.today)
    results += history.check(invoice, ctx.supplier_history)
    if ctx.vies is not None:
        results += await vat.check_vies(invoice, ctx.vies)
    if ctx.rates is not None:
        invoice, cur_results = await currency.check(invoice, ctx.rates, ctx.base_currency)
        results += cur_results
    else:
        results += currency.check_format(invoice)
    if ctx.find_duplicates is not None:
        try:
            matches = await ctx.find_duplicates(invoice, ctx.phash)
        except Exception as e:  # noqa: BLE001
            log.warning("duplicate lookup failed: %s", type(e).__name__)
            matches = []
        results += duplicates.check(invoice, matches)
    return invoice, results


def worst_level(results: list[ValidationResult]) -> ValidationLevel:
    if any(r.level == ValidationLevel.error for r in results):
        return ValidationLevel.error
    if any(r.level == ValidationLevel.warning for r in results):
        return ValidationLevel.warning
    return ValidationLevel.passed


__all__ = ["DuplicateFinder", "ValidationContext", "run_validations", "worst_level"]
