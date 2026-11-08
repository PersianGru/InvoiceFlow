"""Bank reconciliation service: statement import, feed sync, matching, decisions."""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.audit.service import record_audit
from app.bank.feeds import BankFeedProvider
from app.bank.feeds.gocardless import FeedError, GocardlessFeed
from app.bank.matching import Candidate, TxView, candidate_from_invoice, rank
from app.bank.parsers import ParsedStatement, ParsedTransaction, StatementFormatError, parse_statement
from app.db.rls import set_platform_bypass, set_tenant
from app.licensing.service import LicenseService
from app.licensing.state import LicenseSnapshot
from app.licensing.store import FirmSecrets
from app.models.bank import BankAccount, BankFeed, BankStatement, BankTransaction, PaymentMatch
from app.models.document import Document, DocumentStatus
from app.models.firm import Firm
from app.tenancy.context import Actor
from app.webhooks import document_payload, emit_event

log = logging.getLogger("invoiceflow.bank")

OPEN_STATUSES = (DocumentStatus.approved, DocumentStatus.needs_review, DocumentStatus.needs_info)


class StatementError(Exception):
    def __init__(self, code: str, message: str, status: int = 422) -> None:
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


@dataclass(slots=True)
class ImportOutcome:
    statement: BankStatement
    account: BankAccount
    new_transactions: int
    duplicates: int
    matched: int
    suggested: int


# ---------------------------------------------------------------------------------------- feeds
class FeedRegistry:
    def __init__(self, providers: dict[str, BankFeedProvider] | None = None) -> None:
        self.providers: dict[str, BankFeedProvider] = providers or {"gocardless": GocardlessFeed()}

    def get(self, key: str) -> BankFeedProvider | None:
        return self.providers.get(key)


FEED_SECRET_FIELDS = {"gocardless": ("secret_key",)}
FEED_FIELDS = {"gocardless": ("secret_id", "secret_key")}


def feed_config(secrets: FirmSecrets, row: BankFeed) -> dict[str, Any]:
    plain = dict(row.settings or {})
    if row.config_enc:
        raw = secrets.open(f"bank_feed:{row.provider}", row.config_enc)
        if raw:
            try:
                plain.update(json.loads(raw))
            except ValueError:
                log.warning("bank feed secret blob unreadable")
    return plain


def store_feed_config(secrets: FirmSecrets, row: BankFeed, config: dict[str, Any]) -> None:
    secret_keys = set(FEED_SECRET_FIELDS.get(row.provider, ()))
    known = set(FEED_FIELDS.get(row.provider, ()))
    existing = feed_config(secrets, row) if row.config_enc else {}
    merged_secret = {k: v for k, v in existing.items() if k in secret_keys}
    merged_secret.update({k: v for k, v in config.items() if k in secret_keys and v not in (None, "")})
    row.settings = {**(row.settings or {}), **{k: v for k, v in config.items() if k in known - secret_keys}}
    row.config_enc = (
        secrets.seal(f"bank_feed:{row.provider}", json.dumps(merged_secret)) if merged_secret else None
    )
    row.updated_at = datetime.now(UTC)


def public_feed_config(row: BankFeed | None, provider: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k in FEED_FIELDS.get(provider, ()):
        if k in FEED_SECRET_FIELDS.get(provider, ()):
            out[k] = "••••••" if row and row.config_enc else ""
        else:
            out[k] = (row.settings or {}).get(k, "") if row else ""
    return out


# ---------------------------------------------------------------------------------------- accounts
async def upsert_account(
    session: AsyncSession,
    firm: Firm,
    *,
    iban: str,
    currency: str,
    name: str | None = None,
    bic: str | None = None,
    client_company_id: uuid.UUID | None = None,
    source: str = "file",
    external_id: str | None = None,
) -> BankAccount:
    iban = "".join(ch for ch in iban if ch.isalnum()).upper()
    if not iban:
        raise StatementError("invalid_request", "The statement names no account.")
    row = (
        await session.execute(
            select(BankAccount).where(BankAccount.firm_id == firm.id, BankAccount.iban == iban)
        )
    ).scalar_one_or_none()
    if row is None:
        row = BankAccount(
            firm_id=firm.id,
            iban=iban,
            currency=(currency or "EUR").upper()[:3],
            name=(name or "")[:200],
            bic=(bic or None) and bic[:11],
            client_company_id=client_company_id,
            source=source,
            external_id=external_id,
        )
        session.add(row)
        await session.flush()
    else:
        if client_company_id is not None and row.client_company_id is None:
            row.client_company_id = client_company_id
        if external_id and not row.external_id:
            row.external_id, row.source = external_id, source
        if name and not row.name:
            row.name = name[:200]
    return row


async def insert_transactions(
    session: AsyncSession,
    firm: Firm,
    account: BankAccount,
    parsed: list[ParsedTransaction],
    statement_id: uuid.UUID | None,
) -> tuple[list[BankTransaction], int]:
    if not parsed:
        return [], 0
    existing = set(
        (
            await session.execute(
                select(BankTransaction.external_id).where(
                    BankTransaction.account_id == account.id,
                    BankTransaction.external_id.in_([t.external_id for t in parsed]),
                )
            )
        )
        .scalars()
        .all()
    )
    new: list[BankTransaction] = []
    duplicates = 0
    seen: set[str] = set()
    for t in parsed:
        if t.external_id in existing or t.external_id in seen:
            duplicates += 1
            continue
        seen.add(t.external_id)
        row = BankTransaction(
            firm_id=firm.id,
            account_id=account.id,
            statement_id=statement_id,
            external_id=t.external_id,
            booking_date=t.booking_date,
            value_date=t.value_date,
            amount=t.amount,
            currency=t.currency,
            counterparty_name=t.counterparty_name,
            counterparty_iban=t.counterparty_iban,
            remittance_info=t.remittance_info,
            end_to_end_id=t.end_to_end_id,
            raw=t.raw,
        )
        session.add(row)
        new.append(row)
    await session.flush()
    return new, duplicates


# ---------------------------------------------------------------------------------------- import
async def import_statement(
    session: AsyncSession,
    *,
    firm: Firm,
    snap: LicenseSnapshot | None,
    data: bytes,
    filename: str,
    actor: Actor | None,
    client_company_id: uuid.UUID | None = None,
) -> list[ImportOutcome]:
    try:
        parsed_statements: list[ParsedStatement] = parse_statement(data)
    except StatementFormatError as e:
        raise StatementError("unsupported_format", f"Could not read the statement: {e}.") from e
    digest = hashlib.sha256(data).hexdigest()
    dup = (
        await session.execute(
            select(BankStatement.id).where(BankStatement.firm_id == firm.id, BankStatement.sha256 == digest)
        )
    ).first()
    if dup is not None:
        raise StatementError("duplicate", "This statement file was imported before.", status=409)
    outcomes: list[ImportOutcome] = []
    for i, ps in enumerate(parsed_statements):
        account = await upsert_account(
            session,
            firm,
            iban=ps.iban,
            currency=ps.currency,
            name=ps.account_name,
            bic=ps.bic,
            client_company_id=client_company_id,
        )
        st = BankStatement(
            firm_id=firm.id,
            account_id=account.id,
            format=ps.format,
            filename=(filename or "statement")[:255],
            sha256=digest if i == 0 else hashlib.sha256(f"{digest}:{i}".encode()).hexdigest(),
            from_date=ps.from_date,
            to_date=ps.to_date,
            opening_balance=ps.opening_balance,
            closing_balance=ps.closing_balance,
            transactions_count=len(ps.transactions),
            imported_by_user_id=actor.user_id if actor and not actor.is_api_key else None,
        )
        session.add(st)
        await session.flush()
        new, duplicates = await insert_transactions(session, firm, account, ps.transactions, st.id)
        counts = await match_transactions(session, firm, snap, new, actor=actor)
        outcomes.append(
            ImportOutcome(st, account, len(new), duplicates, counts["matched"], counts["suggested"])
        )
    await record_audit(
        session,
        firm_id=firm.id,
        actor=actor,
        action="bank.import",
        target_type="bank_statement",
        target_id=digest[:16],
        after={
            "statements": len(outcomes),
            "transactions": sum(o.new_transactions for o in outcomes),
            "matched": sum(o.matched for o in outcomes),
            "suggested": sum(o.suggested for o in outcomes),
        },
    )
    return outcomes


# ---------------------------------------------------------------------------------------- matching
async def open_candidates(session: AsyncSession) -> tuple[list[Candidate], dict[str, Document]]:
    docs = (
        (
            await session.execute(
                select(Document).where(
                    Document.deleted_at.is_(None),
                    Document.processing_restricted.is_(False),
                    Document.extracted.is_not(None),
                    Document.status.in_(OPEN_STATUSES),
                    Document.payment_status != "paid",
                )
            )
        )
        .scalars()
        .all()
    )
    by_id = {str(d.id): d for d in docs}
    return [candidate_from_invoice(str(d.id), d.extracted or {}, d.paid_amount) for d in docs], by_id


def _tx_view(t: BankTransaction) -> TxView:
    return TxView(
        amount=Decimal(t.amount),
        currency=t.currency,
        booking_date=t.booking_date,
        counterparty_name=t.counterparty_name,
        counterparty_iban=t.counterparty_iban,
        remittance_info=t.remittance_info,
        end_to_end_id=t.end_to_end_id,
    )


async def match_transactions(
    session: AsyncSession,
    firm: Firm,
    snap: LicenseSnapshot | None,
    transactions: list[BankTransaction],
    *,
    actor: Actor | None,
) -> dict[str, int]:
    counts = {"matched": 0, "suggested": 0, "unmatched": 0}
    if not transactions:
        return counts
    candidates, docs = await open_candidates(session)
    for t in transactions:
        if t.match_status in ("matched", "ignored"):
            continue
        scores = rank(_tx_view(t), candidates)
        if not scores:
            t.match_status = "unmatched"
            counts["unmatched"] += 1
            continue
        for s in scores:
            m = PaymentMatch(
                firm_id=firm.id,
                transaction_id=t.id,
                document_id=uuid.UUID(s.document_id),
                amount=s.amount,
                score=s.score,
                method="auto",
                status="suggested",
                reasons=s.reasons,
            )
            session.add(m)
            await session.flush()
            if s.auto:
                await _apply_match(session, firm, snap, m, t, docs[s.document_id], actor=None)
                counts["matched"] += 1
                # the candidate is now (partly) paid: refresh it for the next transaction
                candidates = [c for c in candidates if c.document_id != s.document_id]
                doc = docs[s.document_id]
                if doc.payment_status != "paid":
                    candidates.append(
                        candidate_from_invoice(s.document_id, doc.extracted or {}, doc.paid_amount)
                    )
                break
        else:
            t.match_status = "suggested"
            counts["suggested"] += 1
    await session.flush()
    return counts


def _expected_amount(doc: Document) -> Decimal | None:
    totals = (doc.extracted or {}).get("totals") or {}
    for key in ("amount_due", "gross_total"):
        v = totals.get(key)
        if v is not None:
            try:
                return Decimal(str(v))
            except ArithmeticError:
                continue
    return Decimal(doc.gross_total) if doc.gross_total is not None else None


async def _apply_match(
    session: AsyncSession,
    firm: Firm,
    snap: LicenseSnapshot | None,
    match: PaymentMatch,
    tx: BankTransaction,
    doc: Document,
    *,
    actor: Actor | None,
) -> None:
    match.status = "confirmed"
    match.decided_at = datetime.now(UTC)
    match.decided_by_user_id = actor.user_id if actor and not actor.is_api_key else None
    tx.match_status = "matched"
    # other suggestions for this transaction are moot
    others = (
        await session.execute(
            select(PaymentMatch).where(
                PaymentMatch.transaction_id == tx.id,
                PaymentMatch.id != match.id,
                PaymentMatch.status == "suggested",
            )
        )
    ).scalars()
    for o in others:
        o.status = "rejected"
        o.decided_at = datetime.now(UTC)
    paid = (Decimal(doc.paid_amount) if doc.paid_amount is not None else Decimal("0")) + Decimal(match.amount)
    expected = _expected_amount(doc)
    doc.paid_amount = paid
    if expected is not None and paid >= expected:
        doc.payment_status = "paid"
        doc.paid_at = tx.booking_date
    else:
        doc.payment_status = "partial"
    await session.flush()
    await record_audit(
        session,
        firm_id=firm.id,
        actor=actor,
        action="bank.match",
        target_type="document",
        target_id=str(doc.id),
        after={
            "transaction_id": str(tx.id),
            "amount": str(match.amount),
            "score": str(match.score),
            "method": match.method,
            "payment_status": doc.payment_status,
        },
    )
    if doc.payment_status == "paid":
        payload = document_payload(doc)
        payload["payment"] = {
            "status": doc.payment_status,
            "paid_amount": str(doc.paid_amount),
            "paid_at": doc.paid_at.isoformat() if doc.paid_at else None,
            "transaction_id": str(tx.id),
            "booking_date": tx.booking_date.isoformat(),
        }
        await emit_event(session, firm.id, snap, "invoice.paid", payload)


async def confirm_match(
    session: AsyncSession,
    firm: Firm,
    snap: LicenseSnapshot | None,
    match: PaymentMatch,
    *,
    actor: Actor | None,
) -> None:
    if match.status != "suggested":
        raise StatementError("invalid_state", "This suggestion was already decided.", status=409)
    tx = await session.get(BankTransaction, match.transaction_id)
    doc = await session.get(Document, match.document_id)
    if tx is None or doc is None:
        raise StatementError("not_found", "Transaction or document not found.", status=404)
    if tx.match_status == "matched":
        raise StatementError("invalid_state", "The transaction is already matched.", status=409)
    await _apply_match(session, firm, snap, match, tx, doc, actor=actor)


async def reject_match(
    session: AsyncSession, firm: Firm, match: PaymentMatch, *, actor: Actor | None
) -> None:
    if match.status != "suggested":
        raise StatementError("invalid_state", "This suggestion was already decided.", status=409)
    match.status = "rejected"
    match.decided_at = datetime.now(UTC)
    match.decided_by_user_id = actor.user_id if actor and not actor.is_api_key else None
    tx = await session.get(BankTransaction, match.transaction_id)
    if tx is not None:
        remaining = (
            await session.execute(
                select(func.count())
                .select_from(PaymentMatch)
                .where(PaymentMatch.transaction_id == tx.id, PaymentMatch.status == "suggested")
            )
        ).scalar_one()
        if not remaining and tx.match_status == "suggested":
            tx.match_status = "unmatched"
    await record_audit(
        session,
        firm_id=firm.id,
        actor=actor,
        action="bank.reject",
        target_type="payment_match",
        target_id=str(match.id),
    )


async def manual_match(
    session: AsyncSession,
    firm: Firm,
    snap: LicenseSnapshot | None,
    tx: BankTransaction,
    doc: Document,
    *,
    amount: Decimal | None,
    actor: Actor | None,
) -> PaymentMatch:
    if tx.match_status == "matched":
        raise StatementError("invalid_state", "The transaction is already matched.", status=409)
    if doc.deleted_at is not None or doc.processing_restricted:
        raise StatementError("invalid_state", "This document cannot be matched.", status=409)
    applied = amount if amount is not None else abs(Decimal(tx.amount))
    if applied <= 0 or applied > abs(Decimal(tx.amount)):
        raise StatementError(
            "invalid_request", "The amount must be positive and at most the transaction amount."
        )
    m = PaymentMatch(
        firm_id=firm.id,
        transaction_id=tx.id,
        document_id=doc.id,
        amount=applied,
        score=Decimal("1.000"),
        method="manual",
        status="suggested",
        reasons=["manual"],
    )
    session.add(m)
    await session.flush()
    await _apply_match(session, firm, snap, m, tx, doc, actor=actor)
    return m


async def unmatch_document(session: AsyncSession, firm: Firm, doc: Document, *, actor: Actor | None) -> int:
    """Undo confirmed matches of a document (a wrong confirmation); the transactions become open again."""
    rows = (
        (
            await session.execute(
                select(PaymentMatch).where(
                    PaymentMatch.document_id == doc.id, PaymentMatch.status == "confirmed"
                )
            )
        )
        .scalars()
        .all()
    )
    for m in rows:
        m.status = "rejected"
        m.decided_at = datetime.now(UTC)
        tx = await session.get(BankTransaction, m.transaction_id)
        if tx is not None:
            tx.match_status = "unmatched"
    doc.paid_amount, doc.payment_status, doc.paid_at = None, "unpaid", None
    await record_audit(
        session,
        firm_id=firm.id,
        actor=actor,
        action="bank.unmatch",
        target_type="document",
        target_id=str(doc.id),
    )
    return len(rows)


# ---------------------------------------------------------------------------------------- feed sync
async def sync_feed_accounts(
    session: AsyncSession,
    *,
    firm: Firm,
    snap: LicenseSnapshot | None,
    service: LicenseService,
    registry: FeedRegistry,
    actor: Actor | None,
) -> dict[str, int]:
    counts = {"accounts": 0, "transactions": 0, "matched": 0, "suggested": 0, "errors": 0}
    feeds = (await session.execute(select(BankFeed).where(BankFeed.enabled.is_(True)))).scalars().all()
    for feed in feeds:
        provider = registry.get(feed.provider)
        if provider is None:
            continue
        config = feed_config(service.secrets(firm), feed)
        accounts = (
            (
                await session.execute(
                    select(BankAccount).where(
                        BankAccount.source == feed.provider, BankAccount.external_id.is_not(None)
                    )
                )
            )
            .scalars()
            .all()
        )
        for account in accounts:
            assert account.external_id is not None
            date_from = account.cursor or (date.today() - timedelta(days=90)).isoformat()
            try:
                parsed = await provider.transactions(config, account.external_id, date_from)
            except (FeedError, Exception) as e:  # noqa: BLE001 - one bank failing never stops the others
                feed.last_error = (
                    f"{account.iban[-4:]}: {e if isinstance(e, FeedError) else type(e).__name__}"[:300]
                )
                counts["errors"] += 1
                continue
            new, _dups = await insert_transactions(session, firm, account, parsed, None)
            m = await match_transactions(session, firm, snap, new, actor=actor)
            counts["accounts"] += 1
            counts["transactions"] += len(new)
            counts["matched"] += m["matched"]
            counts["suggested"] += m["suggested"]
            account.last_sync_at = datetime.now(UTC)
            if parsed:
                latest = max(t.booking_date for t in parsed)
                account.cursor = (latest - timedelta(days=3)).isoformat()  # overlap; duplicates are dropped
        feed.last_sync_at = datetime.now(UTC)
        if not counts["errors"]:
            feed.last_error = None
    return counts


async def sync_feeds(
    sessionmaker: async_sessionmaker[AsyncSession],
    service: LicenseService,
    registry: FeedRegistry | None = None,
) -> dict[str, int]:
    """Worker cron: every firm with an enabled feed, one transaction per firm."""
    registry = registry or FeedRegistry()
    totals = {"accounts": 0, "transactions": 0, "matched": 0, "suggested": 0, "errors": 0}
    async with sessionmaker() as session:
        async with session.begin():
            await set_platform_bypass(session)
            firm_ids = (
                (await session.execute(select(BankFeed.firm_id).where(BankFeed.enabled.is_(True))))
                .scalars()
                .all()
            )
    for firm_id in set(firm_ids):
        async with sessionmaker() as session:
            async with session.begin():
                await set_platform_bypass(session)
                firm = await session.get(Firm, firm_id)
                if firm is None:
                    continue
                await set_tenant(session, firm.id)
                snap = service.snapshot(firm)
                if not snap.core_on:
                    continue  # the unlicensed version pulls nothing new; stored data stays
                counts = await sync_feed_accounts(
                    session, firm=firm, snap=snap, service=service, registry=registry, actor=None
                )
                for k, v in counts.items():
                    totals[k] += v
    return totals
