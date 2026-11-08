"""Arithmetic: line totals = qty × unit price; Σ lines = net; net + VAT = gross; per-rate VAT breakdown."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from app.schemas.invoice import Invoice, ValidationLevel, ValidationResult


def _within(a: Decimal, b: Decimal, abs_tol: Decimal, rel_tol: Decimal) -> bool:
    diff = abs(a - b)
    return diff <= abs_tol or diff <= abs(b) * rel_tol


def _q(x: Decimal) -> Decimal:
    return x.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def check(inv: Invoice, abs_tol: Decimal, rel_tol: Decimal) -> list[ValidationResult]:
    out: list[ValidationResult] = []
    t = inv.totals
    # line totals
    bad_lines = []
    line_sum = Decimal(0)
    have_line_amounts = False
    for i, ln in enumerate(inv.lines, start=1):
        if ln.net_amount is not None:
            have_line_amounts = True
            line_sum += ln.net_amount
        if ln.quantity is not None and ln.unit_price is not None and ln.net_amount is not None:
            expected = _q(ln.quantity * ln.unit_price)
            if not _within(expected, ln.net_amount, abs_tol, rel_tol):
                bad_lines.append((i, expected, ln.net_amount))
    if bad_lines:
        i, expected, got = bad_lines[0]
        out.append(
            ValidationResult(
                check="arithmetic.line_totals",
                level=ValidationLevel.warning,
                reason=f"Line {i}: quantity × unit price = {expected}, but the line amount is {got}"
                + (f" (+{len(bad_lines) - 1} more)" if len(bad_lines) > 1 else ""),
                field=f"lines[{i - 1}].net_amount",
                details={"lines": [[i, str(e), str(g)] for i, e, g in bad_lines]},
            )
        )
    elif inv.lines:
        out.append(
            ValidationResult(
                check="arithmetic.line_totals",
                level=ValidationLevel.passed,
                reason="Line amounts match quantity × unit price",
            )
        )
    # sum of lines = net
    net_ref = t.line_net_total if t.line_net_total is not None else t.net_total
    if have_line_amounts and net_ref is not None:
        adjusted = net_ref
        if t.line_net_total is None and (t.allowances_total or t.charges_total):
            adjusted = net_ref + (t.allowances_total or 0) - (t.charges_total or 0)
        if _within(_q(line_sum), adjusted, abs_tol, rel_tol):
            out.append(
                ValidationResult(
                    check="arithmetic.lines_sum",
                    level=ValidationLevel.passed,
                    reason="Sum of lines equals the net total",
                )
            )
        else:
            out.append(
                ValidationResult(
                    check="arithmetic.lines_sum",
                    level=ValidationLevel.error,
                    reason=f"Sum of lines is {_q(line_sum)} but the net total is {adjusted}",
                    field="totals.net_total",
                    details={"lines_sum": str(_q(line_sum)), "net": str(adjusted)},
                )
            )
    # net + vat = gross
    if t.net_total is not None and t.vat_total is not None and t.gross_total is not None:
        if _within(t.net_total + t.vat_total, t.gross_total, abs_tol, rel_tol):
            out.append(
                ValidationResult(
                    check="arithmetic.net_vat_gross",
                    level=ValidationLevel.passed,
                    reason="Net + VAT equals gross",
                )
            )
        else:
            out.append(
                ValidationResult(
                    check="arithmetic.net_vat_gross",
                    level=ValidationLevel.error,
                    reason=(
                        f"Net {t.net_total} + VAT {t.vat_total} = {_q(t.net_total + t.vat_total)}, "
                        f"but the gross total is {t.gross_total}"
                    ),
                    field="totals.gross_total",
                )
            )
    elif t.gross_total is None:
        out.append(
            ValidationResult(
                check="arithmetic.net_vat_gross",
                level=ValidationLevel.warning,
                reason="No gross total found",
                field="totals.gross_total",
            )
        )
    # per-rate breakdown
    if inv.vat_breakdown:
        vat_sum = Decimal(0)
        base_sum = Decimal(0)
        rate_issues = []
        for b in inv.vat_breakdown:
            if b.taxable_amount is not None:
                base_sum += b.taxable_amount
            if b.tax_amount is not None:
                vat_sum += b.tax_amount
            if b.rate is not None and b.taxable_amount is not None and b.tax_amount is not None:
                expected = _q(b.taxable_amount * b.rate / 100)
                if not _within(expected, b.tax_amount, abs_tol, rel_tol):
                    rate_issues.append((b.rate, expected, b.tax_amount))
        if rate_issues:
            r, e, g = rate_issues[0]
            out.append(
                ValidationResult(
                    check="arithmetic.vat_breakdown",
                    level=ValidationLevel.error,
                    reason=f"VAT at {r}% should be {e} on the taxable amount, but {g} is stated",
                    field="vat_breakdown",
                )
            )
        elif t.vat_total is not None and not _within(_q(vat_sum), t.vat_total, abs_tol, rel_tol):
            out.append(
                ValidationResult(
                    check="arithmetic.vat_breakdown",
                    level=ValidationLevel.error,
                    reason=f"The VAT breakdown sums to {_q(vat_sum)} but the VAT total is {t.vat_total}",
                    field="totals.vat_total",
                )
            )
        elif (
            t.net_total is not None and base_sum and not _within(_q(base_sum), t.net_total, abs_tol, rel_tol)
        ):
            out.append(
                ValidationResult(
                    check="arithmetic.vat_breakdown",
                    level=ValidationLevel.warning,
                    reason=f"The taxable amounts sum to {_q(base_sum)} but the net total is {t.net_total}",
                    field="vat_breakdown",
                )
            )
        else:
            out.append(
                ValidationResult(
                    check="arithmetic.vat_breakdown",
                    level=ValidationLevel.passed,
                    reason="VAT breakdown is consistent",
                )
            )
    return out
