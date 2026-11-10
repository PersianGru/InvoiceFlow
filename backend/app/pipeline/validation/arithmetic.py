"""Arithmetic: line totals = qty × unit price − discount + charge; Σ lines − document discounts +
charges = net; net + VAT = gross; gross − prepaid + rounding = due; per-rate VAT breakdown.
An early-payment discount is a payment term and never part of these sums."""

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
            # BT-131 is the amount after the line discount (BT-136) and with the line charge (BT-141)
            expected = _q(ln.quantity * ln.unit_price - (ln.allowance_amount or 0) + (ln.charge_amount or 0))
            if not _within(expected, ln.net_amount, abs_tol, rel_tol):
                bad_lines.append((i, expected, ln.net_amount))
    if bad_lines:
        i, expected, got = bad_lines[0]
        out.append(
            ValidationResult(
                check="arithmetic.line_totals",
                level=ValidationLevel.warning,
                reason=f"Line {i}: quantity × unit price − discount + charge = {expected}, "
                f"but the line shows {got}"
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
    # document-level discounts and charges: the stated totals, else the sum of the listed ones
    listed_allow = sum((a.amount for a in inv.allowances_charges if not a.charge and a.amount), Decimal(0))
    listed_charge = sum((a.amount for a in inv.allowances_charges if a.charge and a.amount), Decimal(0))
    allowances = t.allowances_total if t.allowances_total is not None else listed_allow
    charges = t.charges_total if t.charges_total is not None else listed_charge
    out += _allowances(inv, listed_allow, listed_charge, allowances, charges, abs_tol, rel_tol)
    # sum of lines = net
    net_ref = t.line_net_total if t.line_net_total is not None else t.net_total
    if have_line_amounts and net_ref is not None:
        adjusted = net_ref
        if t.line_net_total is None and (allowances or charges):
            adjusted = net_ref + allowances - charges
        if _within(_q(line_sum), adjusted, abs_tol, rel_tol):
            out.append(
                ValidationResult(
                    check="arithmetic.lines_sum",
                    level=ValidationLevel.passed,
                    reason="Sum of lines equals the net total",
                )
            )
        elif t.gross_total is not None and _within(
            _q(line_sum - allowances + charges), t.gross_total, abs_tol, rel_tol
        ):
            # till receipts print line amounts including VAT
            out.append(
                ValidationResult(
                    check="arithmetic.lines_sum",
                    level=ValidationLevel.passed,
                    reason="Sum of lines equals the gross total (line amounts include VAT)",
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
    # gross − prepaid + rounding = amount due (checked only when the document states prepaid or rounding)
    if t.amount_due is not None and t.gross_total is not None and (t.prepaid or t.rounding):
        due = t.gross_total - (t.prepaid or 0) + (t.rounding or 0)
        ok = _within(due, t.amount_due, abs_tol, rel_tol)
        out.append(
            ValidationResult(
                check="arithmetic.amount_due",
                level=ValidationLevel.passed if ok else ValidationLevel.error,
                reason=(
                    "Gross − prepaid + rounding equals the amount due"
                    if ok
                    else f"Gross {t.gross_total} − prepaid {t.prepaid or 0} + rounding {t.rounding or 0} = "
                    f"{_q(due)}, but the amount due is {t.amount_due}"
                ),
                field=None if ok else "totals.amount_due",
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


def _allowances(
    inv: Invoice,
    listed_allow: Decimal,
    listed_charge: Decimal,
    allowances: Decimal,
    charges: Decimal,
    abs_tol: Decimal,
    rel_tol: Decimal,
) -> list[ValidationResult]:
    """The listed document discounts/charges add up to the stated totals, and lines net − discounts +
    charges = net total when the document states both."""
    t = inv.totals
    problems: list[tuple[str, str]] = []
    if inv.allowances_charges:
        if t.allowances_total is not None and not _within(listed_allow, t.allowances_total, abs_tol, rel_tol):
            problems.append(
                (
                    f"The listed discounts sum to {_q(listed_allow)} but the discount total is "
                    f"{t.allowances_total}",
                    "totals.allowances_total",
                )
            )
        if t.charges_total is not None and not _within(listed_charge, t.charges_total, abs_tol, rel_tol):
            problems.append(
                (
                    f"The listed charges sum to {_q(listed_charge)} "
                    f"but the charge total is {t.charges_total}",
                    "totals.charges_total",
                )
            )
        for a in inv.allowances_charges:
            if a.percent is not None and a.base_amount is not None and a.amount is not None:
                expected = _q(a.base_amount * a.percent / 100)
                if not _within(expected, a.amount, abs_tol, rel_tol):
                    kind = "charge" if a.charge else "discount"
                    problems.append(
                        (
                            f"A {a.percent} % {kind} on {a.base_amount} is {expected}, "
                            f"but {a.amount} is stated",
                            "allowances_charges",
                        )
                    )
    if (
        t.line_net_total is not None
        and t.net_total is not None
        and (allowances or charges)
        and not _within(t.line_net_total - allowances + charges, t.net_total, abs_tol, rel_tol)
    ):
        problems.append(
            (
                f"Lines {t.line_net_total} − discounts {_q(allowances)} + charges {_q(charges)} = "
                f"{_q(t.line_net_total - allowances + charges)}, but the net total is {t.net_total}",
                "totals.net_total",
            )
        )
    if problems:
        return [
            ValidationResult(
                check="arithmetic.allowances",
                level=ValidationLevel.error,
                reason=problems[0][0] + (f" (+{len(problems) - 1} more)" if len(problems) > 1 else ""),
                field=problems[0][1],
            )
        ]
    if inv.allowances_charges or allowances or charges:
        return [
            ValidationResult(
                check="arithmetic.allowances",
                level=ValidationLevel.passed,
                reason="Document discounts and charges are consistent",
            )
        ]
    return []
