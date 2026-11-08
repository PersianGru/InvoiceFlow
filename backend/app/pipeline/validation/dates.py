"""Dates: issue date not in the future, due after issue, periods ordered, due date not absurd."""

from __future__ import annotations

from datetime import date, timedelta

from app.schemas.invoice import Invoice, ValidationLevel, ValidationResult


def check(inv: Invoice, today: date) -> list[ValidationResult]:
    out: list[ValidationResult] = []
    if inv.issue_date:
        if inv.issue_date > today + timedelta(days=1):
            out.append(
                ValidationResult(
                    check="dates.issue_in_future",
                    level=ValidationLevel.error,
                    reason=f"Issue date {inv.issue_date} is in the future",
                    field="issue_date",
                )
            )
        elif inv.issue_date < today - timedelta(days=5 * 365):
            out.append(
                ValidationResult(
                    check="dates.issue_old",
                    level=ValidationLevel.warning,
                    reason=f"Issue date {inv.issue_date} is more than five years ago",
                    field="issue_date",
                )
            )
        else:
            out.append(
                ValidationResult(
                    check="dates.issue_in_future",
                    level=ValidationLevel.passed,
                    reason="Issue date is plausible",
                )
            )
    due = inv.payment.due_date
    if due and inv.issue_date:
        if due < inv.issue_date:
            out.append(
                ValidationResult(
                    check="dates.due_before_issue",
                    level=ValidationLevel.warning,
                    reason=f"Due date {due} is before the issue date {inv.issue_date}",
                    field="payment.due_date",
                )
            )
        elif due > inv.issue_date + timedelta(days=180):
            out.append(
                ValidationResult(
                    check="dates.due_far",
                    level=ValidationLevel.warning,
                    reason=f"Due date {due} is more than 180 days after the issue date",
                    field="payment.due_date",
                )
            )
        else:
            out.append(
                ValidationResult(
                    check="dates.due_before_issue",
                    level=ValidationLevel.passed,
                    reason="Due date follows the issue date",
                )
            )
    if inv.period_start and inv.period_end and inv.period_end < inv.period_start:
        out.append(
            ValidationResult(
                check="dates.period_order",
                level=ValidationLevel.warning,
                reason="Service period ends before it starts",
                field="period_end",
            )
        )
    return out
