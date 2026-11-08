"""Payment matching: explainable, rule-based scores between a bank
transaction and the open invoices. Pure functions; the service applies them.

An automatic match needs the full amount plus the invoice number in the remittance text or the
supplier's IBAN; everything else above the suggestion threshold is proposed for a human to confirm.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

AUTO_THRESHOLD = Decimal("0.95")
SUGGEST_THRESHOLD = Decimal("0.50")
MAX_SUGGESTIONS = 3


@dataclass(slots=True)
class Candidate:
    """What the matcher needs to know about an open document."""

    document_id: str
    number: str | None
    gross_total: Decimal | None
    amount_due: Decimal | None
    currency: str | None
    direction: str | None  # purchase | sales | None (treated as purchase)
    supplier_name: str | None
    supplier_iban: str | None
    issue_date: date | None
    due_date: date | None
    paid_amount: Decimal = Decimal("0")


@dataclass(slots=True)
class TxView:
    amount: Decimal
    currency: str
    booking_date: date
    counterparty_name: str | None
    counterparty_iban: str | None
    remittance_info: str | None
    end_to_end_id: str | None


@dataclass(slots=True)
class MatchScore:
    document_id: str
    score: Decimal
    amount: Decimal  # the amount this match applies to the document
    reasons: list[str] = field(default_factory=list)
    full_amount: bool = False
    auto: bool = False


def _norm(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]", "", (value or "").lower())


def _tokens(value: str | None) -> set[str]:
    stop = {
        "gmbh",
        "ltd",
        "sarl",
        "bv",
        "nv",
        "ag",
        "co",
        "kg",
        "the",
        "and",
        "und",
        "inc",
        "llc",
        "sa",
        "srl",
        "sp",
        "zoo",
        "oy",
        "ab",
    }
    return {t for t in re.findall(r"[a-z0-9]{3,}", (value or "").lower()) if t not in stop}


def number_in_text(number: str | None, *texts: str | None) -> bool:
    n = _norm(number)
    if len(n) < 4:
        return False
    haystack = _norm(" ".join(t for t in texts if t))
    return n in haystack


def score_candidate(tx: TxView, cand: Candidate) -> MatchScore | None:
    if cand.currency and cand.currency.upper() != tx.currency.upper():
        return None
    outgoing = cand.direction == "sales"
    if outgoing and tx.amount <= 0 or not outgoing and tx.amount >= 0:
        return None  # a purchase invoice is paid by a debit, a sales invoice by a credit
    expected = cand.amount_due if cand.amount_due is not None else cand.gross_total
    if expected is None or expected <= 0:
        return None
    open_amount = expected - (cand.paid_amount or Decimal("0"))
    if open_amount <= 0:
        return None
    paid = abs(tx.amount)
    score = Decimal("0")
    reasons: list[str] = []
    full = paid == open_amount or paid == expected
    if full:
        score += Decimal("0.50")
        reasons.append("amount")
    elif Decimal("0") < paid < open_amount:
        score += Decimal("0.15")
        reasons.append("partial amount")
    else:
        return None  # more than the invoice: never this invoice alone
    if number_in_text(cand.number, tx.remittance_info, tx.end_to_end_id):
        score += Decimal("0.40")
        reasons.append("invoice number in remittance")
    if (
        cand.supplier_iban
        and tx.counterparty_iban
        and _norm(cand.supplier_iban) == _norm(tx.counterparty_iban)
    ):
        score += Decimal("0.30")
        reasons.append("supplier IBAN")
    supplier = _tokens(cand.supplier_name)
    if supplier:
        seen = _tokens(tx.counterparty_name) | _tokens(tx.remittance_info)
        overlap = len(supplier & seen)
        if overlap and overlap * 2 >= len(supplier):
            score += Decimal("0.15")
            reasons.append("supplier name")
    if cand.issue_date:
        earliest = cand.issue_date - timedelta(days=5)
        latest = (cand.due_date or cand.issue_date) + timedelta(days=120)
        if earliest <= tx.booking_date <= latest:
            score += Decimal("0.05")
            reasons.append("date window")
        else:
            score -= Decimal("0.10")
            reasons.append("outside date window")
    score = max(Decimal("0"), min(Decimal("1"), score)).quantize(Decimal("0.001"))
    if score < SUGGEST_THRESHOLD:
        return None
    strong = "invoice number in remittance" in reasons or "supplier IBAN" in reasons
    return MatchScore(
        document_id=cand.document_id,
        score=score,
        amount=min(paid, open_amount),
        reasons=reasons,
        full_amount=full,
        auto=bool(full and strong and score >= AUTO_THRESHOLD),
    )


def rank(tx: TxView, candidates: list[Candidate]) -> list[MatchScore]:
    scored = [s for s in (score_candidate(tx, c) for c in candidates) if s is not None]
    scored.sort(key=lambda s: (s.score, s.full_amount), reverse=True)
    if scored and scored[0].auto and (len(scored) == 1 or scored[1].score < scored[0].score):
        return [scored[0]]
    for s in scored:
        s.auto = False  # an ambiguous automatic match becomes a suggestion
    return scored[:MAX_SUGGESTIONS]


def candidate_from_invoice(
    document_id: str, extracted: dict[str, Any], paid_amount: Decimal | None
) -> Candidate:
    """Build a candidate from the stored canonical invoice JSON."""
    totals = extracted.get("totals") or {}
    supplier = extracted.get("supplier") or {}
    payment = extracted.get("payment") or {}

    def dec(v: Any) -> Decimal | None:
        try:
            return Decimal(str(v)) if v is not None else None
        except ArithmeticError:
            return None

    def day(v: Any) -> date | None:
        try:
            return date.fromisoformat(str(v)[:10]) if v else None
        except ValueError:
            return None

    return Candidate(
        document_id=document_id,
        number=extracted.get("number"),
        gross_total=dec(totals.get("gross_total")),
        amount_due=dec(totals.get("amount_due")),
        currency=extracted.get("currency"),
        direction=extracted.get("direction"),
        supplier_name=supplier.get("name"),
        supplier_iban=supplier.get("iban"),
        issue_date=day(extracted.get("issue_date")),
        due_date=day(payment.get("due_date")),
        paid_amount=paid_amount or Decimal("0"),
    )
