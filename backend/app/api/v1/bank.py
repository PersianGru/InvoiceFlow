"""Bank reconciliation API: accounts, statement import, transactions, suggested matches
and decisions, PSD2 feeds. Imports and decisions are points of use of ``core``; reading stays
available in the unlicensed version (the data is the firm's)."""

from __future__ import annotations

import secrets as pysecrets
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.api.deps import FirmDep, LicenseServiceDep, SessionDep, SettingsDep, require
from app.audit.service import record_audit
from app.bank.feeds.gocardless import FeedError
from app.bank.service import (
    FEED_FIELDS,
    FeedRegistry,
    StatementError,
    confirm_match,
    feed_config,
    import_statement,
    manual_match,
    match_transactions,
    public_feed_config,
    reject_match,
    store_feed_config,
    sync_feed_accounts,
    unmatch_document,
    upsert_account,
)
from app.licensing.gate import check_feature
from app.models.bank import BankAccount, BankFeed, BankStatement, BankTransaction, PaymentMatch
from app.models.client_company import ClientCompany
from app.models.document import Document
from app.tenancy.context import Actor

router = APIRouter(prefix="/bank", tags=["bank"])
MAX_STATEMENT_BYTES = 20 * 1024 * 1024


def _feeds(request: Request) -> FeedRegistry:
    reg = getattr(request.app.state, "bank_feeds", None)
    if reg is None:
        reg = request.app.state.bank_feeds = FeedRegistry()
    return reg


def _err(e: StatementError) -> HTTPException:
    return HTTPException(e.status, {"error": e.code, "message": e.message})


class AccountOut(BaseModel):
    id: str
    iban: str
    bic: str | None
    name: str
    currency: str
    source: str
    client_company_id: str | None
    last_sync_at: str | None
    transactions: int = 0
    unmatched: int = 0


class AccountIn(BaseModel):
    iban: str = Field(min_length=8, max_length=34)
    name: str = Field(default="", max_length=200)
    currency: str = Field(default="EUR", min_length=3, max_length=3)
    client_company_id: uuid.UUID | None = None


class AccountPatch(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    client_company_id: uuid.UUID | None = None


class MatchOut(BaseModel):
    id: int
    transaction_id: str
    document_id: str
    amount: str
    score: float
    method: str
    status: str
    reasons: list[str]
    invoice_number: str | None = None
    supplier_name: str | None = None
    gross_total: str | None = None
    created_at: str


class TransactionOut(BaseModel):
    id: str
    account_id: str
    booking_date: str
    value_date: str | None
    amount: str
    currency: str
    counterparty_name: str | None
    counterparty_iban: str | None
    remittance_info: str | None
    end_to_end_id: str | None
    match_status: str
    matches: list[MatchOut] = Field(default_factory=list)


class StatementOut(BaseModel):
    id: str
    account_id: str
    format: str
    filename: str
    from_date: str | None
    to_date: str | None
    opening_balance: str | None
    closing_balance: str | None
    transactions_count: int
    imported_at: str


class ImportOut(BaseModel):
    statements: list[StatementOut]
    new_transactions: int
    duplicates: int
    matched: int
    suggested: int


class ManualMatchIn(BaseModel):
    document_id: uuid.UUID
    amount: Decimal | None = Field(default=None, gt=0)


class FeedOut(BaseModel):
    provider: str
    name: str
    enabled: bool
    config: dict[str, Any]
    fields: list[str]
    last_sync_at: str | None
    last_error: str | None


class FeedIn(BaseModel):
    enabled: bool = False
    config: dict[str, Any] = Field(default_factory=dict)


class RequisitionIn(BaseModel):
    institution_id: str = Field(min_length=1, max_length=100)
    client_company_id: uuid.UUID | None = None


def _account_out(a: BankAccount, counts: dict[uuid.UUID, tuple[int, int]]) -> AccountOut:
    total, unmatched = counts.get(a.id, (0, 0))
    return AccountOut(
        id=str(a.id),
        iban=a.iban,
        bic=a.bic,
        name=a.name,
        currency=a.currency,
        source=a.source,
        client_company_id=str(a.client_company_id) if a.client_company_id else None,
        last_sync_at=a.last_sync_at.isoformat() if a.last_sync_at else None,
        transactions=total,
        unmatched=unmatched,
    )


def _match_out(m: PaymentMatch, doc: Document | None) -> MatchOut:
    return MatchOut(
        id=m.id,
        transaction_id=str(m.transaction_id),
        document_id=str(m.document_id),
        amount=str(m.amount),
        score=float(m.score),
        method=m.method,
        status=m.status,
        reasons=list(m.reasons or []),
        invoice_number=doc.invoice_number if doc else None,
        supplier_name=doc.supplier_name if doc else None,
        gross_total=str(doc.gross_total) if doc and doc.gross_total is not None else None,
        created_at=m.created_at.isoformat() if m.created_at else "",
    )


def _tx_out(t: BankTransaction, matches: list[MatchOut]) -> TransactionOut:
    return TransactionOut(
        id=str(t.id),
        account_id=str(t.account_id),
        booking_date=t.booking_date.isoformat(),
        value_date=t.value_date.isoformat() if t.value_date else None,
        amount=str(t.amount),
        currency=t.currency,
        counterparty_name=t.counterparty_name,
        counterparty_iban=t.counterparty_iban,
        remittance_info=t.remittance_info,
        end_to_end_id=t.end_to_end_id,
        match_status=t.match_status,
        matches=matches,
    )


def _statement_out(s: BankStatement) -> StatementOut:
    return StatementOut(
        id=str(s.id),
        account_id=str(s.account_id),
        format=s.format,
        filename=s.filename,
        from_date=s.from_date.isoformat() if s.from_date else None,
        to_date=s.to_date.isoformat() if s.to_date else None,
        opening_balance=str(s.opening_balance) if s.opening_balance is not None else None,
        closing_balance=str(s.closing_balance) if s.closing_balance is not None else None,
        transactions_count=s.transactions_count,
        imported_at=s.imported_at.isoformat() if s.imported_at else "",
    )


async def _matches_for(
    session: SessionDep, tx_ids: list[uuid.UUID], statuses: tuple[str, ...]
) -> dict[uuid.UUID, list[MatchOut]]:
    out: dict[uuid.UUID, list[MatchOut]] = {}
    if not tx_ids:
        return out
    rows = (
        await session.execute(
            select(PaymentMatch, Document)
            .join(Document, Document.id == PaymentMatch.document_id)
            .where(PaymentMatch.transaction_id.in_(tx_ids), PaymentMatch.status.in_(statuses))
            .order_by(PaymentMatch.score.desc(), PaymentMatch.id)
        )
    ).all()
    for m, d in rows:
        out.setdefault(m.transaction_id, []).append(_match_out(m, d))
    return out


# ---------------------------------------------------------------------------------------- summary / accounts
@router.get("/summary")
async def summary(session: SessionDep, actor: Actor = Depends(require("bank.view"))) -> dict[str, Any]:
    accounts = int((await session.execute(select(func.count()).select_from(BankAccount))).scalar_one())
    by_status = {
        str(s): int(n)
        for s, n in (
            await session.execute(
                select(BankTransaction.match_status, func.count()).group_by(BankTransaction.match_status)
            )
        ).all()
    }
    return {
        "accounts": accounts,
        "unmatched": by_status.get("unmatched", 0),
        "suggested": by_status.get("suggested", 0),
        "matched": by_status.get("matched", 0),
        "ignored": by_status.get("ignored", 0),
    }


@router.get("/accounts", response_model=list[AccountOut])
async def list_accounts(
    session: SessionDep, actor: Actor = Depends(require("bank.view"))
) -> list[AccountOut]:
    rows = (await session.execute(select(BankAccount).order_by(BankAccount.created_at))).scalars().all()
    counts: dict[uuid.UUID, tuple[int, int]] = {}
    for account_id, status, n in (
        await session.execute(
            select(BankTransaction.account_id, BankTransaction.match_status, func.count()).group_by(
                BankTransaction.account_id, BankTransaction.match_status
            )
        )
    ).all():
        total, unmatched = counts.get(account_id, (0, 0))
        counts[account_id] = (
            total + int(n),
            unmatched + (int(n) if status in ("unmatched", "suggested") else 0),
        )
    return [_account_out(a, counts) for a in rows]


@router.post("/accounts", response_model=AccountOut, status_code=201)
async def create_account(
    body: AccountIn,
    session: SessionDep,
    firm: FirmDep,
    svc: LicenseServiceDep,
    actor: Actor = Depends(require("bank.manage")),
) -> AccountOut:
    check_feature(svc.snapshot(firm), "core")
    if (
        body.client_company_id is not None
        and await session.get(ClientCompany, body.client_company_id) is None
    ):
        raise HTTPException(404, {"error": "not_found", "message": "Client company not found."})
    try:
        a = await upsert_account(
            session,
            firm,
            iban=body.iban,
            currency=body.currency,
            name=body.name,
            client_company_id=body.client_company_id,
        )
    except StatementError as e:
        raise _err(e) from e
    await record_audit(
        session,
        firm_id=firm.id,
        actor=actor,
        action="bank.account",
        target_type="bank_account",
        target_id=str(a.id),
    )
    return _account_out(a, {})


@router.patch("/accounts/{account_id}", response_model=AccountOut)
async def patch_account(
    account_id: uuid.UUID,
    body: AccountPatch,
    session: SessionDep,
    firm: FirmDep,
    actor: Actor = Depends(require("bank.manage")),
) -> AccountOut:
    a = await session.get(BankAccount, account_id)
    if a is None:
        raise HTTPException(404, {"error": "not_found", "message": "Account not found."})
    if body.name is not None:
        a.name = body.name[:200]
    if "client_company_id" in body.model_fields_set:
        a.client_company_id = body.client_company_id
    return _account_out(a, {})


# ---------------------------------------------------------------------------------------- statements
@router.post("/statements", response_model=ImportOut, status_code=201)
async def upload_statement(
    session: SessionDep,
    firm: FirmDep,
    svc: LicenseServiceDep,
    file: UploadFile = File(...),
    client_company_id: uuid.UUID | None = Form(default=None),
    actor: Actor = Depends(require("bank.manage")),
) -> ImportOut:
    snap = check_feature(svc.snapshot(firm), "core")
    data = await file.read(MAX_STATEMENT_BYTES + 1)
    if len(data) > MAX_STATEMENT_BYTES:
        raise HTTPException(413, {"error": "too_large", "message": "The statement is larger than 20 MB."})
    if not data:
        raise HTTPException(422, {"error": "invalid_request", "message": "The file is empty."})
    try:
        outcomes = await import_statement(
            session,
            firm=firm,
            snap=snap,
            data=data,
            filename=file.filename or "statement",
            actor=actor,
            client_company_id=client_company_id,
        )
    except StatementError as e:
        raise _err(e) from e
    return ImportOut(
        statements=[_statement_out(o.statement) for o in outcomes],
        new_transactions=sum(o.new_transactions for o in outcomes),
        duplicates=sum(o.duplicates for o in outcomes),
        matched=sum(o.matched for o in outcomes),
        suggested=sum(o.suggested for o in outcomes),
    )


@router.get("/statements", response_model=list[StatementOut])
async def list_statements(
    session: SessionDep,
    actor: Actor = Depends(require("bank.view")),
    limit: int = Query(default=50, ge=1, le=200),
) -> list[StatementOut]:
    rows = (
        (await session.execute(select(BankStatement).order_by(BankStatement.imported_at.desc()).limit(limit)))
        .scalars()
        .all()
    )
    return [_statement_out(s) for s in rows]


# ---- transactions / matches
@router.get("/transactions", response_model=list[TransactionOut])
async def list_transactions(
    session: SessionDep,
    actor: Actor = Depends(require("bank.view")),
    status: str | None = Query(default=None, pattern="^(unmatched|suggested|matched|ignored)$"),
    account_id: uuid.UUID | None = None,
    limit: int = Query(default=100, ge=1, le=500),
) -> list[TransactionOut]:
    stmt = (
        select(BankTransaction)
        .order_by(BankTransaction.booking_date.desc(), BankTransaction.created_at.desc())
        .limit(limit)
    )
    if status:
        stmt = stmt.where(BankTransaction.match_status == status)
    if account_id is not None:
        stmt = stmt.where(BankTransaction.account_id == account_id)
    rows = (await session.execute(stmt)).scalars().all()
    matches = await _matches_for(session, [t.id for t in rows], ("suggested", "confirmed"))
    return [_tx_out(t, matches.get(t.id, [])) for t in rows]


@router.get("/matches", response_model=list[MatchOut])
async def list_matches(
    session: SessionDep,
    actor: Actor = Depends(require("bank.view")),
    status: str = Query(default="suggested", pattern="^(suggested|confirmed|rejected)$"),
    document_id: uuid.UUID | None = None,
    limit: int = Query(default=100, ge=1, le=500),
) -> list[MatchOut]:
    stmt = (
        select(PaymentMatch, Document)
        .join(Document, Document.id == PaymentMatch.document_id)
        .where(PaymentMatch.status == status)
        .order_by(PaymentMatch.id.desc())
        .limit(limit)
    )
    if document_id is not None:
        stmt = stmt.where(PaymentMatch.document_id == document_id)
    return [_match_out(m, d) for m, d in (await session.execute(stmt)).all()]


@router.post("/matches/{match_id}/confirm", response_model=MatchOut)
async def confirm(
    match_id: int,
    session: SessionDep,
    firm: FirmDep,
    svc: LicenseServiceDep,
    actor: Actor = Depends(require("bank.manage")),
) -> MatchOut:
    snap = check_feature(svc.snapshot(firm), "core")
    m = await session.get(PaymentMatch, match_id)
    if m is None:
        raise HTTPException(404, {"error": "not_found", "message": "Match not found."})
    try:
        await confirm_match(session, firm, snap, m, actor=actor)
    except StatementError as e:
        raise _err(e) from e
    return _match_out(m, await session.get(Document, m.document_id))


@router.post("/matches/{match_id}/reject", response_model=MatchOut)
async def reject(
    match_id: int,
    session: SessionDep,
    firm: FirmDep,
    svc: LicenseServiceDep,
    actor: Actor = Depends(require("bank.manage")),
) -> MatchOut:
    check_feature(svc.snapshot(firm), "core")
    m = await session.get(PaymentMatch, match_id)
    if m is None:
        raise HTTPException(404, {"error": "not_found", "message": "Match not found."})
    try:
        await reject_match(session, firm, m, actor=actor)
    except StatementError as e:
        raise _err(e) from e
    return _match_out(m, await session.get(Document, m.document_id))


@router.post("/transactions/{transaction_id}/match", response_model=MatchOut, status_code=201)
async def match_manually(
    transaction_id: uuid.UUID,
    body: ManualMatchIn,
    session: SessionDep,
    firm: FirmDep,
    svc: LicenseServiceDep,
    actor: Actor = Depends(require("bank.manage")),
) -> MatchOut:
    snap = check_feature(svc.snapshot(firm), "core")
    tx = await session.get(BankTransaction, transaction_id)
    doc = await session.get(Document, body.document_id)
    if tx is None or doc is None:
        raise HTTPException(404, {"error": "not_found", "message": "Transaction or document not found."})
    try:
        m = await manual_match(session, firm, snap, tx, doc, amount=body.amount, actor=actor)
    except StatementError as e:
        raise _err(e) from e
    return _match_out(m, doc)


@router.post("/transactions/{transaction_id}/ignore", response_model=TransactionOut)
async def ignore_transaction(
    transaction_id: uuid.UUID,
    session: SessionDep,
    firm: FirmDep,
    svc: LicenseServiceDep,
    actor: Actor = Depends(require("bank.manage")),
) -> TransactionOut:
    check_feature(svc.snapshot(firm), "core")
    tx = await session.get(BankTransaction, transaction_id)
    if tx is None:
        raise HTTPException(404, {"error": "not_found", "message": "Transaction not found."})
    if tx.match_status == "matched":
        raise HTTPException(409, {"error": "invalid_state", "message": "The transaction is matched."})
    tx.match_status = "unmatched" if tx.match_status == "ignored" else "ignored"
    return _tx_out(tx, [])


@router.post("/transactions/{transaction_id}/rematch", response_model=TransactionOut)
async def rematch(
    transaction_id: uuid.UUID,
    session: SessionDep,
    firm: FirmDep,
    svc: LicenseServiceDep,
    actor: Actor = Depends(require("bank.manage")),
) -> TransactionOut:
    """Run the matcher again for one transaction (after documents arrived or were corrected)."""
    snap = check_feature(svc.snapshot(firm), "core")
    tx = await session.get(BankTransaction, transaction_id)
    if tx is None:
        raise HTTPException(404, {"error": "not_found", "message": "Transaction not found."})
    if tx.match_status == "matched":
        raise HTTPException(409, {"error": "invalid_state", "message": "The transaction is matched."})
    for old in (
        await session.execute(
            select(PaymentMatch).where(
                PaymentMatch.transaction_id == tx.id, PaymentMatch.status == "suggested"
            )
        )
    ).scalars():
        old.status = "rejected"
        old.decided_at = datetime.now(UTC)
    tx.match_status = "unmatched"
    await match_transactions(session, firm, snap, [tx], actor=actor)
    matches = await _matches_for(session, [tx.id], ("suggested", "confirmed"))
    return _tx_out(tx, matches.get(tx.id, []))


@router.post("/documents/{document_id}/unmatch")
async def unmatch(
    document_id: uuid.UUID,
    session: SessionDep,
    firm: FirmDep,
    svc: LicenseServiceDep,
    actor: Actor = Depends(require("bank.manage")),
) -> dict[str, int]:
    check_feature(svc.snapshot(firm), "core")
    doc = await session.get(Document, document_id)
    if doc is None:
        raise HTTPException(404, {"error": "not_found", "message": "Document not found."})
    return {"undone": await unmatch_document(session, firm, doc, actor=actor)}


# ---------------------------------------------------------------------------------------- feeds (PSD2)
def _feed_out(provider: Any, row: BankFeed | None) -> FeedOut:
    return FeedOut(
        provider=provider.key,
        name=provider.name,
        enabled=bool(row and row.enabled),
        config=public_feed_config(row, provider.key),
        fields=list(FEED_FIELDS.get(provider.key, ())),
        last_sync_at=row.last_sync_at.isoformat() if row and row.last_sync_at else None,
        last_error=row.last_error if row else None,
    )


@router.get("/feeds", response_model=list[FeedOut])
async def list_feeds(
    request: Request, session: SessionDep, actor: Actor = Depends(require("firm.manage"))
) -> list[FeedOut]:
    rows = {r.provider: r for r in (await session.execute(select(BankFeed))).scalars().all()}
    return [_feed_out(p, rows.get(k)) for k, p in _feeds(request).providers.items()]


@router.put("/feeds/{provider}", response_model=FeedOut)
async def put_feed(
    request: Request,
    provider: str,
    body: FeedIn,
    session: SessionDep,
    firm: FirmDep,
    svc: LicenseServiceDep,
    actor: Actor = Depends(require("firm.manage")),
) -> FeedOut:
    prov = _feeds(request).get(provider)
    if prov is None:
        raise HTTPException(404, {"error": "not_found", "message": "Unknown feed provider."})
    if body.enabled:
        check_feature(svc.snapshot(firm), "core")
    row = await session.get(BankFeed, (firm.id, provider))
    if row is None:
        row = BankFeed(firm_id=firm.id, provider=provider)
        session.add(row)
    secrets = svc.secrets(firm)
    store_feed_config(secrets, row, {k: v for k, v in body.config.items() if isinstance(k, str)})
    if body.enabled:
        cfg = feed_config(secrets, row)
        missing = [k for k in FEED_FIELDS.get(provider, ()) if not str(cfg.get(k) or "").strip()]
        if missing:
            raise HTTPException(
                422,
                {
                    "error": "invalid_request",
                    "message": f"Missing: {', '.join(missing)}.",
                    "missing": missing,
                },
            )
        ok, message = await prov.test(cfg)
        if not ok:
            raise HTTPException(422, {"error": "invalid_request", "message": message})
    row.enabled = body.enabled
    await session.flush()
    await record_audit(
        session,
        firm_id=firm.id,
        actor=actor,
        action="bank.feed_configure",
        target_type="bank_feed",
        target_id=provider,
        after={"enabled": row.enabled},
    )
    return _feed_out(prov, row)


@router.get("/feeds/{provider}/institutions")
async def institutions(
    request: Request,
    provider: str,
    session: SessionDep,
    firm: FirmDep,
    svc: LicenseServiceDep,
    actor: Actor = Depends(require("firm.manage")),
    country: str = Query(default="DE", min_length=2, max_length=2),
) -> list[dict[str, str]]:
    prov = _feeds(request).get(provider)
    row = await session.get(BankFeed, (firm.id, provider))
    if prov is None or row is None or not row.enabled:
        raise HTTPException(404, {"error": "not_found", "message": "Enable the feed first."})
    try:
        return await prov.institutions(feed_config(svc.secrets(firm), row), country)
    except FeedError as e:
        raise HTTPException(502, {"error": "provider_error", "message": str(e)}) from e


@router.post("/feeds/{provider}/requisitions", status_code=201)
async def start_requisition(
    request: Request,
    provider: str,
    body: RequisitionIn,
    session: SessionDep,
    firm: FirmDep,
    svc: LicenseServiceDep,
    settings: SettingsDep,
    actor: Actor = Depends(require("firm.manage")),
) -> dict[str, str]:
    prov = _feeds(request).get(provider)
    row = await session.get(BankFeed, (firm.id, provider))
    if prov is None or row is None or not row.enabled:
        raise HTTPException(404, {"error": "not_found", "message": "Enable the feed first."})
    check_feature(svc.snapshot(firm), "core")
    reference = f"ivf-{pysecrets.token_hex(8)}"
    web = settings.cors_origins.split(",")[0].strip().rstrip("/") or settings.public_url.rstrip("/")
    try:
        req = await prov.start_link(
            feed_config(svc.secrets(firm), row),
            institution_id=body.institution_id,
            redirect=f"{web}/bank?linked=1",
            reference=reference,
        )
    except FeedError as e:
        raise HTTPException(502, {"error": "provider_error", "message": str(e)}) from e
    pending = dict((row.settings or {}).get("requisitions") or {})
    pending[req["id"]] = {
        "client_company_id": str(body.client_company_id) if body.client_company_id else None,
        "reference": reference,
    }
    row.settings = {**(row.settings or {}), "requisitions": pending}
    await record_audit(
        session,
        firm_id=firm.id,
        actor=actor,
        action="bank.feed_link",
        target_type="bank_feed",
        target_id=provider,
        after={"requisition": req["id"]},
    )
    return {"id": req["id"], "link": req["link"]}


@router.post("/feeds/{provider}/requisitions/{requisition_id}/complete", response_model=list[AccountOut])
async def complete_requisition(
    request: Request,
    provider: str,
    requisition_id: str,
    session: SessionDep,
    firm: FirmDep,
    svc: LicenseServiceDep,
    actor: Actor = Depends(require("firm.manage")),
) -> list[AccountOut]:
    prov = _feeds(request).get(provider)
    row = await session.get(BankFeed, (firm.id, provider))
    if prov is None or row is None or not row.enabled:
        raise HTTPException(404, {"error": "not_found", "message": "Enable the feed first."})
    check_feature(svc.snapshot(firm), "core")
    pending = dict((row.settings or {}).get("requisitions") or {})
    meta = pending.get(requisition_id)
    if meta is None:
        raise HTTPException(404, {"error": "not_found", "message": "Unknown requisition."})
    try:
        accounts = await prov.linked_accounts(feed_config(svc.secrets(firm), row), requisition_id)
    except FeedError as e:
        raise HTTPException(502, {"error": "provider_error", "message": str(e)}) from e
    client_id = uuid.UUID(meta["client_company_id"]) if meta.get("client_company_id") else None
    out: list[BankAccount] = []
    for a in accounts:
        if not a.get("iban"):
            continue
        try:
            acc = await upsert_account(
                session,
                firm,
                iban=a["iban"],
                currency=a.get("currency", "EUR"),
                name=a.get("name"),
                bic=a.get("bic") or None,
                client_company_id=client_id,
                source=provider,
                external_id=a["id"],
            )
        except StatementError as e:
            raise _err(e) from e
        out.append(acc)
    pending.pop(requisition_id, None)
    row.settings = {**(row.settings or {}), "requisitions": pending}
    return [_account_out(a, {}) for a in out]


@router.post("/feeds/sync")
async def sync_now(
    request: Request,
    session: SessionDep,
    firm: FirmDep,
    svc: LicenseServiceDep,
    actor: Actor = Depends(require("bank.manage")),
) -> dict[str, int]:
    snap = check_feature(svc.snapshot(firm), "core")
    return await sync_feed_accounts(
        session, firm=firm, snap=snap, service=svc, registry=_feeds(request), actor=actor
    )
