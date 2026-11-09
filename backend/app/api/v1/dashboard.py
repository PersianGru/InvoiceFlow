"""Dashboard endpoints: page images for the review screen, assignment, comments, bulk
actions, supplier profiles and statistics. Every write stays behind RBAC and the license gate;
reading always works (unlicensed version)."""

from __future__ import annotations

import io
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import case, func, select

from app.api.deps import FirmDep, LicenseServiceDep, SessionDep, require
from app.audit.service import record_audit
from app.channels.intake import question_transports, send_question
from app.db.rls import set_tenant
from app.licensing.gate import check_feature
from app.models.comment import DocumentComment
from app.models.document import Document, DocumentStatus
from app.models.pipeline import AiCallLog, DocumentEvent, SupplierProfile
from app.models.user import User
from app.pipeline.render import render_pdf_pages
from app.pipeline.text import load_image
from app.storage.base import Storage
from app.tenancy.context import Actor
from app.webhooks import document_payload, emit_event

router = APIRouter(tags=["dashboard"])


async def _doc(session: SessionDep, actor: Actor, document_id: uuid.UUID) -> Document:
    d = await session.get(Document, document_id)
    if d is None or d.deleted_at is not None or not actor.in_scope(d.client_company_id):
        raise HTTPException(404, {"error": "not_found", "message": "Document not found."})
    return d


# ---- page images -------------------------------------------------------------------------------
@router.get("/documents/{document_id}/pages/{page}.png")
async def page_image(
    request: Request,
    document_id: uuid.UUID,
    page: int,
    session: SessionDep,
    width: int = Query(default=1200, ge=200, le=2400),
    actor: Actor = Depends(require("documents.view")),
) -> Response:
    d = await _doc(session, actor, document_id)
    storage: Storage = request.app.state.storage
    data = await storage.get(d.storage_key)
    if d.mime_type == "application/pdf":
        pages = render_pdf_pages(data, scale=width / 595.0, max_pages=page)
        if page < 1 or page > len(pages):
            raise HTTPException(404, {"error": "not_found", "message": "Page not found."})
        img = pages[page - 1]
    elif d.mime_type.startswith("image/"):
        if page != 1:
            raise HTTPException(404, {"error": "not_found", "message": "Page not found."})
        img = load_image(data)
        if img.width > width:
            img = img.resize((width, int(img.height * width / img.width)))
    else:
        raise HTTPException(404, {"error": "no_preview", "message": "No preview for this file type."})
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="PNG", optimize=True)
    return Response(
        content=buf.getvalue(), media_type="image/png", headers={"cache-control": "private, max-age=3600"}
    )


# ---- assignment and comments ----------------------------------------------------------------------
class AssignIn(BaseModel):
    user_id: uuid.UUID | None = None


@router.post("/documents/{document_id}/assign")
async def assign(
    document_id: uuid.UUID,
    body: AssignIn,
    session: SessionDep,
    firm: FirmDep,
    actor: Actor = Depends(require("documents.review")),
) -> dict[str, Any]:
    d = await _doc(session, actor, document_id)
    if body.user_id is not None and await session.get(User, body.user_id) is None:
        raise HTTPException(404, {"error": "not_found", "message": "User not found."})
    before = str(d.assigned_to_user_id) if d.assigned_to_user_id else None
    d.assigned_to_user_id = body.user_id
    await record_audit(
        session,
        firm_id=firm.id,
        actor=actor,
        action="document.assign",
        target_type="document",
        target_id=str(d.id),
        before={"assigned": before},
        after={"assigned": str(body.user_id) if body.user_id else None},
    )
    return {"id": str(d.id), "assigned_to_user_id": str(body.user_id) if body.user_id else None}


class CommentIn(BaseModel):
    body: str = Field(min_length=1, max_length=4000)
    kind: str = Field(default="note", pattern=r"^(note|question|answer)$")
    visible_to_client: bool = False


class CommentOut(BaseModel):
    id: int
    author_user_id: str | None
    kind: str
    body: str
    visible_to_client: bool
    resolved_at: str | None
    created_at: str


def _comment_out(c: DocumentComment) -> CommentOut:
    return CommentOut(
        id=c.id,
        author_user_id=str(c.author_user_id) if c.author_user_id else None,
        kind=c.kind,
        body=c.body,
        visible_to_client=c.visible_to_client,
        resolved_at=c.resolved_at.isoformat() if c.resolved_at else None,
        created_at=c.created_at.isoformat() if c.created_at else "",
    )


@router.get("/documents/{document_id}/comments", response_model=list[CommentOut])
async def list_comments(
    document_id: uuid.UUID, session: SessionDep, actor: Actor = Depends(require("documents.view"))
) -> list[CommentOut]:
    d = await _doc(session, actor, document_id)
    stmt = select(DocumentComment).where(DocumentComment.document_id == d.id).order_by(DocumentComment.id)
    if actor.is_client:
        stmt = stmt.where(DocumentComment.visible_to_client.is_(True))
    return [_comment_out(c) for c in (await session.execute(stmt)).scalars().all()]


@router.post("/documents/{document_id}/comments", response_model=CommentOut, status_code=201)
async def add_comment(
    request: Request,
    document_id: uuid.UUID,
    body: CommentIn,
    session: SessionDep,
    firm: FirmDep,
    actor: Actor = Depends(require("documents.view")),
) -> CommentOut:
    d = await _doc(session, actor, document_id)
    kind = body.kind
    visible = body.visible_to_client
    if actor.is_client:
        kind, visible = "answer", True  # clients answer; they never write internal notes
    c = DocumentComment(
        firm_id=firm.id,
        document_id=d.id,
        author_user_id=actor.user_id,
        kind=kind,
        body=body.body.strip(),
        visible_to_client=visible,
    )
    session.add(c)
    if kind == "question":
        d.status = DocumentStatus.needs_info
        d.review_status = "needs_info"
        if d.channel.value in ("email", "telegram", "whatsapp"):
            await send_question(
                session, await question_transports(request, session, firm, d), firm, d, c.body
            )
    elif kind == "answer":
        for q in (
            await session.execute(
                select(DocumentComment).where(
                    DocumentComment.document_id == d.id,
                    DocumentComment.kind == "question",
                    DocumentComment.resolved_at.is_(None),
                )
            )
        ).scalars():
            q.resolved_at = datetime.now(UTC)
        if d.status == DocumentStatus.needs_info:
            d.status, d.review_status = DocumentStatus.needs_review, "needs_review"
    await session.flush()
    await record_audit(
        session,
        firm_id=firm.id,
        actor=actor,
        action=f"document.comment.{kind}",
        target_type="document",
        target_id=str(d.id),
        after={"comment_id": c.id, "visible_to_client": visible},
    )
    return _comment_out(c)


# ---- bulk actions -----------------------------------------------------------------------------------
class BulkIn(BaseModel):
    ids: list[uuid.UUID] = Field(min_length=1, max_length=200)
    action: str = Field(pattern=r"^(approve|reject|assign|reprocess)$")
    user_id: uuid.UUID | None = None


@router.post("/documents/bulk")
async def bulk(
    request: Request,
    body: BulkIn,
    session: SessionDep,
    firm: FirmDep,
    svc: LicenseServiceDep,
    actor: Actor = Depends(require("documents.review")),
) -> dict[str, Any]:
    snap = svc.snapshot(firm)
    if body.action in ("approve", "reprocess", "reject"):
        check_feature(snap, "core")
    if body.action == "approve" and actor.role.value not in ("firm_owner", "accountant"):
        raise HTTPException(403, {"error": "forbidden", "message": "Only accountants can approve."})
    done: list[str] = []
    skipped: list[dict[str, str]] = []
    for doc_id in body.ids:
        d = await session.get(Document, doc_id)
        if d is not None and d.processing_restricted:
            skipped.append({"id": str(doc_id), "reason": "processing_restricted"})
            continue
        if d is None or d.deleted_at is not None:
            skipped.append({"id": str(doc_id), "reason": "not_found"})
            continue
        if body.action == "assign":
            d.assigned_to_user_id = body.user_id
        elif body.action == "approve":
            if (
                d.status not in (DocumentStatus.needs_review, DocumentStatus.needs_info)
                or d.extracted is None
            ):
                skipped.append({"id": str(doc_id), "reason": "not_reviewable"})
                continue
            d.status, d.review_status = DocumentStatus.approved, "approved"
            d.reviewed_by_user_id, d.reviewed_at = actor.user_id, datetime.now(UTC)
            inv = dict(d.extracted)
            inv["review_status"] = "approved"
            for m in inv.get("meta", {}).values():
                if isinstance(m, dict) and m.get("ai_generated"):
                    m["ai_generated"], m["confirmed_by"], m["confirmed_at"] = (
                        False,
                        str(actor.user_id),
                        datetime.now(UTC).isoformat(),
                    )
            d.extracted = inv
            await emit_event(
                session,
                firm.id,
                snap,
                "invoice.approved",
                {"document": document_payload(d), "invoice": inv, "version": None},
            )
        elif body.action == "reject":
            d.status, d.review_status = DocumentStatus.rejected, "rejected"
            d.reviewed_by_user_id, d.reviewed_at = actor.user_id, datetime.now(UTC)
        elif body.action == "reprocess":
            d.status, d.error = DocumentStatus.received, None
        done.append(str(doc_id))
    await record_audit(
        session,
        firm_id=firm.id,
        actor=actor,
        action=f"document.bulk.{body.action}",
        target_type="document",
        target_id=None,
        after={"count": len(done), "skipped": len(skipped)},
    )
    if body.action == "approve" and done and request.app.state.queue is not None:
        await session.commit()
        await session.begin()
        await set_tenant(session, firm.id)
        for done_id in done:
            await request.app.state.queue.enqueue_connector_push(uuid.UUID(done_id))
    if body.action == "reprocess" and done:
        await session.commit()
        await session.begin()
        await set_tenant(session, firm.id)
        queue = request.app.state.queue
        if queue is not None:
            for done_id in done:
                await queue.enqueue_process_document(uuid.UUID(done_id))
    return {"done": done, "skipped": skipped}


# ---- suppliers -------------------------------------------------------------------------------------
class SupplierOut(BaseModel):
    id: str
    name: str
    vat_id: str | None
    country: str | None
    ibans: list[str]
    document_count: int
    correction_count: int
    accuracy: float | None
    default_gl_account: str | None
    default_cost_category: str | None
    default_vat_code: str | None
    field_rules: dict[str, Any]
    stats: dict[str, Any]


def _supplier_out(p: SupplierProfile) -> SupplierOut:
    acc = None
    if p.document_count:
        acc = round(max(0.0, 1 - (p.correction_count or 0) / p.document_count), 3)
    return SupplierOut(
        id=str(p.id),
        name=p.name,
        vat_id=p.vat_id,
        country=p.country,
        ibans=list(p.ibans or []),
        document_count=p.document_count,
        correction_count=p.correction_count,
        accuracy=acc,
        default_gl_account=p.default_gl_account,
        default_cost_category=p.default_cost_category,
        default_vat_code=p.default_vat_code,
        field_rules=dict(p.field_rules or {}),
        stats=dict(p.stats or {}),
    )


@router.get("/suppliers", response_model=list[SupplierOut])
async def list_suppliers(
    session: SessionDep,
    q: str | None = None,
    limit: int = 200,
    actor: Actor = Depends(require("clients.view")),
) -> list[SupplierOut]:
    stmt = (
        select(SupplierProfile)
        .order_by(SupplierProfile.document_count.desc(), SupplierProfile.name)
        .limit(min(limit, 1000))
    )
    if q:
        stmt = stmt.where((SupplierProfile.name.ilike(f"%{q}%")) | (SupplierProfile.vat_id.ilike(f"%{q}%")))
    return [_supplier_out(p) for p in (await session.execute(stmt)).scalars().all()]


@router.get("/suppliers/{supplier_id}", response_model=SupplierOut)
async def get_supplier(
    supplier_id: uuid.UUID, session: SessionDep, actor: Actor = Depends(require("clients.view"))
) -> SupplierOut:
    p = await session.get(SupplierProfile, supplier_id)
    if p is None:
        raise HTTPException(404, {"error": "not_found", "message": "Supplier not found."})
    return _supplier_out(p)


class SupplierPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    default_gl_account: str | None = Field(default=None, max_length=32)
    default_cost_category: str | None = Field(default=None, max_length=64)
    default_vat_code: str | None = Field(default=None, max_length=16)
    field_rules: dict[str, str] | None = None


@router.patch("/suppliers/{supplier_id}", response_model=SupplierOut)
async def patch_supplier(
    supplier_id: uuid.UUID,
    body: SupplierPatch,
    session: SessionDep,
    firm: FirmDep,
    actor: Actor = Depends(require("clients.manage")),
) -> SupplierOut:
    p = await session.get(SupplierProfile, supplier_id)
    if p is None:
        raise HTTPException(404, {"error": "not_found", "message": "Supplier not found."})
    before = _supplier_out(p).model_dump()
    for k, v in body.model_dump(exclude_none=True).items():
        setattr(p, k, v)
    p.updated_at = datetime.now(UTC)
    await record_audit(
        session,
        firm_id=firm.id,
        actor=actor,
        action="supplier.update",
        target_type="supplier",
        target_id=str(p.id),
        before=before,
        after=_supplier_out(p).model_dump(),
    )
    return _supplier_out(p)


# ---- statistics --------------------------------------------------------------------------------------
@router.get("/stats")
async def stats(
    session: SessionDep,
    days: int = Query(default=30, ge=1, le=365),
    actor: Actor = Depends(require("audit.view")),
) -> dict[str, Any]:
    since = datetime.now(UTC) - timedelta(days=days)
    by_status = {
        row[0] if isinstance(row[0], str) else row[0].value: int(row[1])
        for row in (
            await session.execute(
                select(Document.status, func.count())
                .where(Document.received_at >= since, Document.deleted_at.is_(None))
                .group_by(Document.status)
            )
        ).all()
    }
    by_day = [
        {"day": row[0].isoformat() if row[0] else None, "count": int(row[1])}
        for row in (
            await session.execute(
                select(func.date(Document.received_at), func.count())
                .where(Document.received_at >= since, Document.deleted_at.is_(None))
                .group_by(func.date(Document.received_at))
                .order_by(func.date(Document.received_at))
            )
        ).all()
    ]
    by_channel = {
        row[0] if isinstance(row[0], str) else row[0].value: int(row[1])
        for row in (
            await session.execute(
                select(Document.channel, func.count())
                .where(Document.received_at >= since, Document.deleted_at.is_(None))
                .group_by(Document.channel)
            )
        ).all()
    }
    processed = (
        await session.execute(
            select(
                func.count(), func.sum(case((Document.review_status == "auto_approved", 1), else_=0))
            ).where(Document.processed_at.is_not(None), Document.received_at >= since)
        )
    ).one()
    total_processed, auto = int(processed[0] or 0), int(processed[1] or 0)
    durations = (
        await session.execute(
            select(
                func.avg(DocumentEvent.duration_ms),
                func.percentile_cont(0.95).within_group(DocumentEvent.duration_ms),
            ).where(DocumentEvent.step == "done", DocumentEvent.created_at >= since)
        )
    ).one()
    ai = (
        await session.execute(
            select(
                AiCallLog.provider,
                func.count(),
                func.coalesce(func.sum(AiCallLog.tokens_in), 0),
                func.coalesce(func.sum(AiCallLog.tokens_out), 0),
                func.coalesce(func.sum(AiCallLog.duration_ms), 0),
            )
            .where(AiCallLog.created_at >= since)
            .group_by(AiCallLog.provider)
        )
    ).all()
    suppliers = (
        (
            await session.execute(
                select(SupplierProfile).order_by(SupplierProfile.document_count.desc()).limit(10)
            )
        )
        .scalars()
        .all()
    )
    structured = int(
        (
            await session.execute(
                select(func.count()).where(
                    Document.structured_format.is_not(None), Document.received_at >= since
                )
            )
        ).scalar_one()
    )
    return {
        "days": days,
        "documents": {
            "total": sum(by_status.values()),
            "by_status": by_status,
            "by_day": by_day,
            "by_channel": by_channel,
            "structured": structured,
        },
        "auto_approval_rate": round(auto / total_processed, 3) if total_processed else None,
        "processing_ms": {
            "avg": round(float(durations[0]), 1) if durations[0] is not None else None,
            "p95": round(float(durations[1]), 1) if durations[1] is not None else None,
        },
        "ai_calls": [
            {
                "provider": r[0],
                "calls": int(r[1]),
                "tokens_in": int(r[2] or 0),
                "tokens_out": int(r[3] or 0),
                "duration_ms": int(r[4] or 0),
            }
            for r in ai
        ],
        "suppliers": [
            {
                "id": str(p.id),
                "name": p.name,
                "documents": p.document_count,
                "corrections": p.correction_count,
                "accuracy": (
                    round(max(0.0, 1 - (p.correction_count or 0) / p.document_count), 3)
                    if p.document_count
                    else None
                ),
            }
            for p in suppliers
        ],
    }
