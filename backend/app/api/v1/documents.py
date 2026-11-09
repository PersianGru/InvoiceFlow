"""Document intake and the always-available views.

Intake (upload, API) passes the license gate and the monthly limit **before anything is stored**
, then the security checks; ZIP uploads become one document per file. Processing runs in
the worker. The unlicensed version keeps listing, searching and downloading (spec §2 rule 9).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, UploadFile
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.api.deps import FirmDep, LicenseServiceDep, SessionDep, require
from app.api.idempotency import idempotency_begin, idempotency_store
from app.audit.service import record_audit
from app.db.rls import set_tenant
from app.intake import store_document
from app.licensing.gate import check_feature, check_limit
from app.models.client_company import ClientCompany
from app.models.document import Document, DocumentChannel, DocumentStatus
from app.models.pipeline import DocumentEvent, Extraction, SupplierProfile
from app.pipeline import security
from app.pipeline.detect import unpack_zip
from app.pipeline.supplier import find_profile
from app.schemas.invoice import AuditEntry, Invoice, ReviewStatus, ValueSource
from app.storage.base import Storage
from app.tenancy.context import Actor
from app.webhooks import document_payload, emit_event

router = APIRouter(prefix="/documents", tags=["documents"])


class DocumentOut(BaseModel):
    id: str
    client_company_id: str | None
    channel: str
    status: str
    review_status: str
    original_filename: str
    mime_type: str
    size_bytes: int
    sha256: str
    received_at: str
    retention_until: str | None
    document_type: str | None = None
    invoice_number: str | None = None
    supplier_name: str | None = None
    issue_date: str | None = None
    currency: str | None = None
    gross_total: str | None = None
    min_confidence: float | None = None
    structured_format: str | None = None
    error: str | None = None
    assigned_to_user_id: str | None = None
    page_count: int | None = None
    client_company_id_display: str | None = None
    sender_identity: str | None = None
    payment_status: str = "unpaid"
    paid_amount: str | None = None
    paid_at: str | None = None


class UploadOut(DocumentOut):
    batch: list[DocumentOut] | None = None


def out(d: Document) -> DocumentOut:
    return DocumentOut(
        id=str(d.id),
        client_company_id=str(d.client_company_id) if d.client_company_id else None,
        channel=d.channel.value if isinstance(d.channel, DocumentChannel) else str(d.channel),
        status=d.status.value if isinstance(d.status, DocumentStatus) else str(d.status),
        review_status=d.review_status,
        original_filename=d.original_filename,
        mime_type=d.mime_type,
        size_bytes=d.size_bytes,
        sha256=d.sha256,
        received_at=d.received_at.isoformat() if d.received_at else "",
        retention_until=d.retention_until.isoformat() if d.retention_until else None,
        document_type=d.document_type,
        invoice_number=d.invoice_number,
        supplier_name=d.supplier_name,
        issue_date=d.issue_date.isoformat() if d.issue_date else None,
        currency=d.currency,
        gross_total=str(d.gross_total) if d.gross_total is not None else None,
        min_confidence=float(d.min_confidence) if d.min_confidence is not None else None,
        structured_format=d.structured_format,
        error=d.error,
        assigned_to_user_id=str(d.assigned_to_user_id) if d.assigned_to_user_id else None,
        page_count=d.page_count,
        sender_identity=d.sender_identity or None,
        payment_status=d.payment_status or "unpaid",
        paid_amount=str(d.paid_amount) if d.paid_amount is not None else None,
        paid_at=d.paid_at.isoformat() if d.paid_at else None,
    )


def _storage(request: Request) -> Storage:
    return request.app.state.storage  # type: ignore[no-any-return]


async def _documents_this_month(session: SessionDep) -> int:
    start = datetime.now(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    n = (
        await session.execute(
            select(func.count())
            .select_from(Document)
            .where(Document.received_at >= start, Document.deleted_at.is_(None))
        )
    ).scalar_one()
    return int(n)


async def _store_document(
    request: Request,
    session: SessionDep,
    firm: Any,
    actor: Actor,
    data: bytes,
    filename: str,
    mime: str,
    client_company_id: uuid.UUID | None,
    channel: DocumentChannel,
) -> Document:
    return await store_document(
        session,
        _storage(request),
        firm,
        data=data,
        filename=filename,
        mime=mime,
        client_company_id=client_company_id,
        channel=channel,
        sender_identity=actor.email,
        actor=actor,
    )


@router.post("", response_model=UploadOut, status_code=201)
async def upload(
    request: Request,
    file: UploadFile,
    session: SessionDep,
    firm: FirmDep,
    svc: LicenseServiceDep,
    client_company_id: uuid.UUID | None = None,
    actor: Actor = Depends(require("documents.upload")),
) -> Any:
    snap = check_feature(
        svc.snapshot(firm), "core"
    )  # license gate first: nothing is stored for an unlicensed firm
    if actor.is_client:
        client_company_id = actor.client_company_id
    elif actor.client_scope:
        if client_company_id is None and len(actor.client_scope) == 1:
            client_company_id = next(iter(actor.client_scope))
        if client_company_id is None or client_company_id not in actor.client_scope:
            raise HTTPException(
                403,
                {
                    "error": "client_out_of_scope",
                    "message": "This API key may only upload for its client companies.",
                },
            )
    client = await session.get(ClientCompany, client_company_id) if client_company_id is not None else None
    if client_company_id is not None and client is None:
        raise HTTPException(404, {"error": "not_found", "message": "Client company not found."})
    if client is not None:
        from app.addresses import intake_paused

        pause_channel = "api" if actor.is_api_key else "upload"
        if intake_paused(client, pause_channel):
            await record_audit(
                session,
                firm_id=firm.id,
                actor=actor,
                action="channel.intake_paused",
                target_type="client_company",
                target_id=str(client.id),
                after={"channel": pause_channel},
                result="refused",
            )
            raise HTTPException(
                423,
                {"error": "intake_paused", "message": f"Sending documents for {client.name} is paused."},
            )
    data = await file.read(security.MAX_UPLOAD_BYTES + 1)
    idem = await idempotency_begin(
        request, session, firm.id, body=(file.filename or "").encode() + b"" + data
    )
    if idem.replay is not None:
        return idem.replay
    try:
        security.check_size(data)
        mime, _ext = security.check_type(data)
    except security.IntakeRejectedError as e:
        raise HTTPException(
            413 if e.code == "too_large" else 422, {"error": e.code, "message": e.message}
        ) from e
    channel = DocumentChannel.api if actor.is_api_key else DocumentChannel.upload
    scanner: security.VirusScanner = request.app.state.scanner
    entries: list[tuple[str, bytes, str]] = []
    if mime == "application/zip":
        try:
            for entry in unpack_zip(data):
                emime, _ = security.check_type(entry.data)
                if emime == "application/zip":
                    continue  # nested archives are not unpacked
                entries.append((entry.name, entry.data, emime))
        except security.IntakeRejectedError as e:
            raise HTTPException(422, {"error": e.code, "message": e.message}) from e
        if not entries:
            raise HTTPException(
                422, {"error": "zip_empty", "message": "The ZIP contains no supported files."}
            )
    else:
        entries.append((file.filename or "upload", data, mime))
    check_limit(snap, "max_documents_per_month", await _documents_this_month(session), adding=len(entries))
    docs: list[Document] = []
    for name, blob, emime in entries:
        scan = await scanner.scan(blob)
        if not scan.clean:
            await record_audit(
                session,
                firm_id=firm.id,
                actor=actor,
                action="document.upload",
                target_type="document",
                target_id=name[:120],
                result="malware",
            )
            raise HTTPException(
                422, {"error": "malware", "message": "The file was rejected by the virus scanner."}
            )
        docs.append(
            await _store_document(
                request, session, firm, actor, blob, name, emime, client_company_id, channel
            )
        )
    for d in docs:
        await emit_event(session, firm.id, snap, "document.received", document_payload(d))
    await session.commit()  # the worker must see the rows before the job starts
    await session.begin()
    await set_tenant(session, firm.id)  # the RLS context is transaction-local
    queue = request.app.state.queue
    if queue is not None:
        for d in docs:
            await queue.enqueue_process_document(d.id)
        await queue.kick_webhooks()
    for d in docs:
        await session.refresh(d)
    first = out(docs[0])
    result = UploadOut(**first.model_dump(), batch=[out(d) for d in docs] if len(docs) > 1 else None)
    await idempotency_store(session, idem, firm.id, 201, result.model_dump())
    return JSONResponse(status_code=201, content=result.model_dump())


class DocumentPage(BaseModel):
    items: list[DocumentOut]
    total: int
    offset: int
    limit: int


@router.get("", response_model=DocumentPage)
async def list_documents(
    session: SessionDep,
    actor: Actor = Depends(require("documents.view")),
    q: str | None = None,
    status: str | None = None,
    channel: str | None = None,
    client_company_id: uuid.UUID | None = None,
    supplier: str | None = None,
    assigned_to: uuid.UUID | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    amount_min: float | None = None,
    amount_max: float | None = None,
    sort: str = Query(
        default="received_desc", pattern=r"^(received_desc|received_asc|issue_date|amount|supplier)$"
    ),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=500),
) -> DocumentPage:
    stmt = select(Document).where(Document.deleted_at.is_(None))
    scoped = actor.scoped_client_ids()
    if scoped is not None:
        stmt = stmt.where(Document.client_company_id.in_(scoped))
    elif client_company_id is not None:
        stmt = stmt.where(Document.client_company_id == client_company_id)
    if status:
        stmt = stmt.where(Document.status.in_([x for x in status.split(",") if x]))
    if channel:
        stmt = stmt.where(Document.channel == channel)
    if supplier:
        stmt = stmt.where(Document.supplier_name.ilike(f"%{supplier}%"))
    if assigned_to is not None:
        stmt = stmt.where(Document.assigned_to_user_id == assigned_to)
    if date_from:
        stmt = stmt.where(Document.received_at >= datetime.fromisoformat(date_from))
    if date_to:
        stmt = stmt.where(Document.received_at < datetime.fromisoformat(date_to) + timedelta(days=1))
    if amount_min is not None:
        stmt = stmt.where(Document.gross_total >= amount_min)
    if amount_max is not None:
        stmt = stmt.where(Document.gross_total <= amount_max)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(
            (Document.original_filename.ilike(like))
            | (Document.invoice_number.ilike(like))
            | (Document.supplier_name.ilike(like))
        )
    total = int((await session.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one())
    order: dict[str, Any] = {
        "received_desc": Document.received_at.desc(),
        "received_asc": Document.received_at.asc(),
        "issue_date": Document.issue_date.desc().nulls_last(),
        "amount": Document.gross_total.desc().nulls_last(),
        "supplier": Document.supplier_name.asc().nulls_last(),
    }
    rows = (
        (await session.execute(stmt.order_by(order[sort], Document.id).offset(offset).limit(limit)))
        .scalars()
        .all()
    )
    return DocumentPage(items=[out(d) for d in rows], total=total, offset=offset, limit=limit)


async def _get_doc(session: SessionDep, actor: Actor, document_id: uuid.UUID) -> Document:
    d = await session.get(Document, document_id)
    if d is None or d.deleted_at is not None or not actor.in_scope(d.client_company_id):
        raise HTTPException(404, {"error": "not_found", "message": "Document not found."})
    return d


@router.get("/{document_id}", response_model=DocumentOut)
async def get_document(
    document_id: uuid.UUID, session: SessionDep, actor: Actor = Depends(require("documents.view"))
) -> DocumentOut:
    d = await _get_doc(session, actor, document_id)
    await record_audit(
        session,
        firm_id=d.firm_id,
        actor=actor,
        action="document.view",
        target_type="document",
        target_id=str(d.id),
    )
    return out(d)


@router.get("/{document_id}/download")
async def download(
    request: Request,
    document_id: uuid.UUID,
    session: SessionDep,
    actor: Actor = Depends(require("documents.view")),
) -> Response:
    d = await _get_doc(session, actor, document_id)
    data = await _storage(request).get(d.storage_key)
    await record_audit(
        session,
        firm_id=d.firm_id,
        actor=actor,
        action="document.download",
        target_type="document",
        target_id=str(d.id),
    )
    safe_name = "".join(ch for ch in d.original_filename if ch.isalnum() or ch in "._- ")[:120] or "document"
    return Response(
        content=data,
        media_type=d.mime_type,
        headers={"content-disposition": f'attachment; filename="{safe_name}"'},
    )


@router.get("/{document_id}/invoice")
async def get_invoice(
    document_id: uuid.UUID, session: SessionDep, actor: Actor = Depends(require("documents.view"))
) -> dict[str, Any]:
    d = await _get_doc(session, actor, document_id)
    latest = (
        (
            await session.execute(
                select(Extraction)
                .where(Extraction.document_id == d.id)
                .order_by(Extraction.version.desc())
                .limit(1)
            )
        )
        .scalars()
        .first()
    )
    if latest is None:
        raise HTTPException(
            404, {"error": "not_processed", "message": "This document has not been processed yet."}
        )
    return {
        "version": latest.version,
        "provider": latest.provider,
        "model": latest.model,
        "prompt_version": latest.prompt_version,
        "invoice": latest.invoice,
    }


@router.get("/{document_id}/timeline")
async def timeline(
    document_id: uuid.UUID, session: SessionDep, actor: Actor = Depends(require("documents.view"))
) -> list[dict[str, Any]]:
    d = await _get_doc(session, actor, document_id)
    rows = (
        (
            await session.execute(
                select(DocumentEvent).where(DocumentEvent.document_id == d.id).order_by(DocumentEvent.id)
            )
        )
        .scalars()
        .all()
    )
    return [
        {
            "step": e.step,
            "status": e.status,
            "detail": e.detail,
            "duration_ms": e.duration_ms,
            "at": e.created_at.isoformat() if e.created_at else None,
        }
        for e in rows
    ]


@router.post("/{document_id}/reprocess", response_model=DocumentOut)
async def reprocess(
    request: Request,
    document_id: uuid.UUID,
    session: SessionDep,
    firm: FirmDep,
    svc: LicenseServiceDep,
    actor: Actor = Depends(require("documents.review")),
) -> DocumentOut:
    check_feature(svc.snapshot(firm), "core")
    d = await _get_doc(session, actor, document_id)
    d.status, d.error = DocumentStatus.received, None
    await record_audit(
        session,
        firm_id=firm.id,
        actor=actor,
        action="document.reprocess",
        target_type="document",
        target_id=str(d.id),
    )
    await session.commit()
    await session.begin()
    await set_tenant(session, firm.id)  # the RLS context is transaction-local
    queue = request.app.state.queue
    if queue is not None:
        await queue.enqueue_process_document(d.id)
    await session.refresh(d)
    return out(d)


class ReviewIn(BaseModel):
    action: str = Field(pattern=r"^(approve|reject|needs_info|save)$")
    corrections: dict[str, Any] = Field(default_factory=dict, description="dotted field path -> new value")
    comment: str | None = Field(default=None, max_length=2000)


def _apply_corrections(
    invoice: dict[str, Any], corrections: dict[str, Any], actor: Actor
) -> list[AuditEntry]:
    entries: list[AuditEntry] = []
    for path, value in corrections.items():
        parts = path.split(".")
        cur = invoice
        for p in parts[:-1]:
            cur = cur.setdefault(p, {})
        before = cur.get(parts[-1])
        cur[parts[-1]] = value
        invoice.setdefault("meta", {})[path] = {
            "confidence": 1.0,
            "source": ValueSource.human.value,
            "location": (invoice.get("meta", {}).get(path) or {}).get("location"),
            "ai_generated": False,
            "confirmed_by": str(actor.user_id),
            "confirmed_at": datetime.now(UTC).isoformat(),
        }
        entries.append(
            AuditEntry(
                at=datetime.now(UTC),
                actor=str(actor.user_id),
                action="correct",
                field=path,
                before=before,
                after=value,
            )
        )
    return entries


@router.post("/{document_id}/review", response_model=DocumentOut)
async def review(
    request: Request,
    document_id: uuid.UUID,
    body: ReviewIn,
    session: SessionDep,
    firm: FirmDep,
    svc: LicenseServiceDep,
    actor: Actor = Depends(require("documents.review")),
) -> DocumentOut:
    snap = check_feature(
        svc.snapshot(firm), "core"
    )  # review/approve is switched off in the unlicensed version
    if body.action == "approve" and actor.role.value not in ("firm_owner", "accountant"):
        raise HTTPException(403, {"error": "forbidden", "message": "Only accountants can approve."})
    d = await _get_doc(session, actor, document_id)
    latest = (
        (
            await session.execute(
                select(Extraction)
                .where(Extraction.document_id == d.id)
                .order_by(Extraction.version.desc())
                .limit(1)
            )
        )
        .scalars()
        .first()
    )
    if latest is None:
        raise HTTPException(
            409, {"error": "not_processed", "message": "This document has not been processed yet."}
        )
    inv_dict: dict[str, Any] = dict(latest.invoice)
    audit_entries = _apply_corrections(inv_dict, body.corrections, actor) if body.corrections else []
    status_map = {
        "approve": (DocumentStatus.approved, ReviewStatus.approved),
        "reject": (DocumentStatus.rejected, ReviewStatus.rejected),
        "needs_info": (DocumentStatus.needs_info, ReviewStatus.needs_info),
        "save": (
            d.status,
            (
                ReviewStatus(d.review_status)
                if d.review_status in ReviewStatus.__members__.values()
                else ReviewStatus.needs_review
            ),
        ),
    }
    new_status, new_review = status_map[body.action]
    inv_dict["review_status"] = new_review.value
    if body.action == "approve":
        for m in inv_dict.get("meta", {}).values():
            if isinstance(m, dict) and m.get("ai_generated"):
                m["ai_generated"] = False
                m["confirmed_by"], m["confirmed_at"] = str(actor.user_id), datetime.now(UTC).isoformat()
        audit_entries.append(AuditEntry(at=datetime.now(UTC), actor=str(actor.user_id), action="approve"))
    inv_dict["audit"] = list(inv_dict.get("audit", [])) + [e.model_dump(mode="json") for e in audit_entries]
    invoice = Invoice.model_validate(inv_dict)
    session.add(
        Extraction(
            firm_id=d.firm_id,
            document_id=d.id,
            version=latest.version + 1,
            provider="human" if body.corrections else latest.provider,
            model=latest.model,
            prompt_version=latest.prompt_version,
            invoice=invoice.model_dump(mode="json"),
            created_by_user_id=actor.user_id,
        )
    )
    d.extracted = invoice.model_dump(mode="json")
    d.status, d.review_status = new_status, new_review.value
    if body.action in ("approve", "reject"):
        d.reviewed_by_user_id, d.reviewed_at = actor.user_id, datetime.now(UTC)
    d.invoice_number = (invoice.number or "")[:128] or None
    d.supplier_name = (invoice.supplier.name or "")[:200] or None
    d.supplier_vat_id = (invoice.supplier.vat_id or "")[:32] or None
    d.issue_date, d.gross_total = invoice.issue_date, invoice.totals.gross_total
    d.currency = invoice.currency.upper() if invoice.currency else None
    d.document_type = invoice.document_type.value
    if body.corrections:
        profile = await find_profile(session, firm.id, invoice)
        if profile is not None:
            profile.correction_count = (profile.correction_count or 0) + 1
            examples = list(profile.examples or [])
            examples.append(
                {
                    "number": invoice.number,
                    "issue_date": invoice.issue_date.isoformat() if invoice.issue_date else None,
                    "gross_total": (
                        str(invoice.totals.gross_total) if invoice.totals.gross_total is not None else None
                    ),
                    "currency": invoice.currency,
                    "corrected": sorted(body.corrections),
                }
            )
            profile.examples = examples[-10:]
            rules = dict(profile.field_rules or {})
            for path, value in body.corrections.items():
                if path.startswith("supplier.") and isinstance(value, str):
                    rules[path] = value  # a confirmed supplier attribute becomes a rule for next time
            profile.field_rules = rules
            await session.flush()
            _ = SupplierProfile  # keep the import explicit for type checkers
    await record_audit(
        session,
        firm_id=firm.id,
        actor=actor,
        action=f"document.review.{body.action}",
        target_type="document",
        target_id=str(d.id),
        before={"status": str(latest.version)},
        after={
            "version": latest.version + 1,
            "corrections": sorted(body.corrections),
            "comment": body.comment,
        },
    )
    if body.action == "approve":
        await emit_event(
            session,
            firm.id,
            snap,
            "invoice.approved",
            {
                "document": document_payload(d),
                "invoice": invoice.model_dump(mode="json"),
                "version": latest.version + 1,
            },
        )
        queue = request.app.state.queue
        if queue is not None:
            await session.commit()  # the connector job reads the approved document in its own session
            await session.begin()
            await set_tenant(session, firm.id)
            await queue.kick_webhooks()
            await queue.enqueue_connector_push(d.id)
            await session.refresh(d)
    return out(d)
