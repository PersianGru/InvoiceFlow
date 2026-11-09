"""Exports and export templates.

New exports need the core tool ("new exports" are switched off in the unlicensed
version); listing and downloading exports made before stays available. Templates are plain data.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.api.deps import FirmDep, LicenseServiceDep, SessionDep, require
from app.api.idempotency import idempotency_begin, idempotency_store
from app.audit.service import record_audit
from app.exports import (
    COLUMNS,
    ExportError,
    ExportRow,
    build_export,
    default_template_columns,
    validate_columns,
)
from app.licensing.gate import check_feature
from app.models.client_company import ClientCompany
from app.models.document import Document
from app.models.integration import EXPORT_FORMATS, Export, ExportTemplate
from app.storage.base import Storage
from app.tenancy.context import Actor
from app.webhooks import emit_event


def _scoped_exports(stmt: Any, actor: Actor) -> Any:
    """A client-scoped API key sees only exports of its client companies."""
    scoped = actor.scoped_client_ids()
    return stmt.where(Export.client_company_id.in_(scoped)) if scoped is not None else stmt


def _export_visible(e: Export, actor: Actor) -> bool:
    scoped = actor.scoped_client_ids()
    return scoped is None or e.client_company_id in scoped


router = APIRouter(tags=["exports"])

TEMPLATE_FORMATS = ("csv", "xlsx", "datev")


class TemplateIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    format: str = Field(default="csv", pattern="^(csv|xlsx|datev)$")
    client_company_id: uuid.UUID | None = None
    columns: list[dict[str, Any]] = Field(default_factory=list)
    options: dict[str, Any] = Field(default_factory=dict)


class TemplateOut(BaseModel):
    id: str
    name: str
    format: str
    client_company_id: str | None
    columns: list[dict[str, str]]
    options: dict[str, Any]
    created_at: str
    updated_at: str


def template_out(t: ExportTemplate) -> TemplateOut:
    return TemplateOut(
        id=str(t.id),
        name=t.name,
        format=t.format,
        client_company_id=str(t.client_company_id) if t.client_company_id else None,
        columns=list(t.columns or []),
        options=dict(t.options or {}),
        created_at=t.created_at.isoformat() if t.created_at else "",
        updated_at=t.updated_at.isoformat() if t.updated_at else "",
    )


@router.get("/exports/columns")
async def export_columns(actor: Actor = Depends(require("exports.view"))) -> dict[str, Any]:
    return {
        "columns": [{"key": c.key, "label": c.label, "kind": c.kind, "group": c.group} for c in COLUMNS],
        "default": default_template_columns(),
        "formats": list(EXPORT_FORMATS),
    }


@router.get("/export-templates", response_model=list[TemplateOut])
async def list_templates(
    session: SessionDep, actor: Actor = Depends(require("exports.view"))
) -> list[TemplateOut]:
    rows = (await session.execute(select(ExportTemplate).order_by(ExportTemplate.name))).scalars().all()
    return [template_out(t) for t in rows]


def _clean_template(body: TemplateIn) -> tuple[list[dict[str, str]], dict[str, Any]]:
    try:
        columns = validate_columns(body.columns) if body.columns else default_template_columns()
    except ValueError as e:
        raise HTTPException(422, {"error": "invalid_template", "message": str(e)}) from e
    options = {k: v for k, v in body.options.items() if isinstance(k, str) and len(k) <= 40}
    if len(options) > 30:
        raise HTTPException(422, {"error": "invalid_template", "message": "Too many options."})
    return columns, options


@router.post("/export-templates", response_model=TemplateOut, status_code=201)
async def create_template(
    body: TemplateIn, session: SessionDep, firm: FirmDep, actor: Actor = Depends(require("exports.create"))
) -> TemplateOut:
    columns, options = _clean_template(body)
    if (
        body.client_company_id is not None
        and await session.get(ClientCompany, body.client_company_id) is None
    ):
        raise HTTPException(404, {"error": "not_found", "message": "Client company not found."})
    t = ExportTemplate(
        firm_id=firm.id,
        client_company_id=body.client_company_id,
        name=body.name.strip(),
        format=body.format,
        columns=columns,
        options=options,
        created_by_user_id=actor.user_id,
    )
    session.add(t)
    await session.flush()
    await record_audit(
        session,
        firm_id=firm.id,
        actor=actor,
        action="export_template.create",
        target_type="export_template",
        target_id=str(t.id),
    )
    return template_out(t)


@router.patch("/export-templates/{template_id}", response_model=TemplateOut)
async def update_template(
    template_id: uuid.UUID,
    body: TemplateIn,
    session: SessionDep,
    firm: FirmDep,
    actor: Actor = Depends(require("exports.create")),
) -> TemplateOut:
    t = await session.get(ExportTemplate, template_id)
    if t is None:
        raise HTTPException(404, {"error": "not_found", "message": "Template not found."})
    columns, options = _clean_template(body)
    t.name, t.format, t.columns, t.options = body.name.strip(), body.format, columns, options
    t.client_company_id = body.client_company_id
    t.updated_at = datetime.now(UTC)
    await session.flush()
    await record_audit(
        session,
        firm_id=firm.id,
        actor=actor,
        action="export_template.update",
        target_type="export_template",
        target_id=str(t.id),
    )
    return template_out(t)


@router.delete("/export-templates/{template_id}", status_code=204)
async def delete_template(
    template_id: uuid.UUID,
    session: SessionDep,
    firm: FirmDep,
    actor: Actor = Depends(require("exports.create")),
) -> Response:
    t = await session.get(ExportTemplate, template_id)
    if t is None:
        raise HTTPException(404, {"error": "not_found", "message": "Template not found."})
    await session.delete(t)
    await record_audit(
        session,
        firm_id=firm.id,
        actor=actor,
        action="export_template.delete",
        target_type="export_template",
        target_id=str(template_id),
    )
    return Response(status_code=204)


# ---- exports -------------------------------------------------------------------------------------
class ExportFilters(BaseModel):
    status: str | None = None  # csv of statuses
    client_company_id: uuid.UUID | None = None
    supplier: str | None = None
    date_from: str | None = None
    date_to: str | None = None


class ExportIn(BaseModel):
    format: str
    template_id: uuid.UUID | None = None
    document_ids: list[uuid.UUID] = Field(default_factory=list, max_length=5000)
    filters: ExportFilters | None = None
    options: dict[str, Any] = Field(default_factory=dict)


class ExportOut(BaseModel):
    id: str
    format: str
    status: str
    file_name: str
    media_type: str
    size_bytes: int
    sha256: str
    document_count: int
    document_ids: list[str]
    client_company_id: str | None
    template_id: str | None
    error: str | None
    created_at: str
    download_url: str


def export_out(e: Export) -> ExportOut:
    return ExportOut(
        id=str(e.id),
        format=e.format,
        status=e.status,
        file_name=e.file_name,
        media_type=e.media_type,
        size_bytes=e.size_bytes,
        sha256=e.sha256,
        document_count=e.document_count,
        document_ids=list(e.document_ids or []),
        client_company_id=str(e.client_company_id) if e.client_company_id else None,
        template_id=str(e.template_id) if e.template_id else None,
        error=e.error,
        created_at=e.created_at.isoformat() if e.created_at else "",
        download_url=f"/api/v1/exports/{e.id}/download",
    )


def _storage(request: Request) -> Storage:
    return request.app.state.storage  # type: ignore[no-any-return]


async def _select_documents(session: SessionDep, actor: Actor, body: ExportIn) -> list[Document]:
    stmt = select(Document).where(
        Document.deleted_at.is_(None),
        Document.extracted.isnot(None),
        Document.processing_restricted.is_(False),
    )
    scoped = actor.scoped_client_ids()
    if scoped is not None:
        stmt = stmt.where(Document.client_company_id.in_(scoped))
    if body.document_ids:
        stmt = stmt.where(Document.id.in_(body.document_ids))
    elif body.filters is not None:
        f = body.filters
        if f.status:
            stmt = stmt.where(Document.status.in_([x for x in f.status.split(",") if x]))
        if f.client_company_id is not None:
            stmt = stmt.where(Document.client_company_id == f.client_company_id)
        if f.supplier:
            stmt = stmt.where(Document.supplier_name.ilike(f"%{f.supplier}%"))
        if f.date_from:
            stmt = stmt.where(Document.received_at >= datetime.fromisoformat(f.date_from))
        if f.date_to:
            stmt = stmt.where(Document.received_at < datetime.fromisoformat(f.date_to) + timedelta(days=1))
    else:
        raise HTTPException(422, {"error": "invalid_request", "message": "Give document_ids or filters."})
    rows = (await session.execute(stmt.order_by(Document.received_at.asc()).limit(5001))).scalars().all()
    if len(rows) > 5000:
        raise HTTPException(422, {"error": "too_many", "message": "At most 5000 documents per export."})
    return list(rows)


async def _rows_for(session: SessionDep, docs: list[Document]) -> list[ExportRow]:
    from app.schemas.invoice import Invoice

    client_ids = {d.client_company_id for d in docs if d.client_company_id}
    names: dict[uuid.UUID, str] = {}
    if client_ids:
        for c in (
            await session.execute(select(ClientCompany).where(ClientCompany.id.in_(client_ids)))
        ).scalars():
            names[c.id] = c.name
    rows = []
    for d in docs:
        inv = Invoice.model_validate(d.extracted)
        rows.append(ExportRow(inv, d, names.get(d.client_company_id) if d.client_company_id else None))
    return rows


@router.post("/exports", response_model=ExportOut, status_code=201)
async def create_export(
    request: Request,
    body: ExportIn,
    session: SessionDep,
    firm: FirmDep,
    svc: LicenseServiceDep,
    actor: Actor = Depends(require("exports.create")),
) -> Any:
    snap = check_feature(svc.snapshot(firm), "core")  # new exports are part of the licensed product
    if body.format not in EXPORT_FORMATS:
        raise HTTPException(422, {"error": "invalid_request", "message": f"Unknown format '{body.format}'."})
    idem = await idempotency_begin(request, session, firm.id)
    if idem.replay is not None:
        return idem.replay
    template = None
    if body.template_id is not None:
        template = await session.get(ExportTemplate, body.template_id)
        if template is None:
            raise HTTPException(404, {"error": "not_found", "message": "Template not found."})
    docs = await _select_documents(session, actor, body)
    if not docs:
        raise HTTPException(422, {"error": "nothing_to_export", "message": "No processed documents match."})
    rows = await _rows_for(session, docs)
    originals: dict[str, bytes] = {}
    if body.format == "facturx":
        storage = _storage(request)
        for d in docs:
            if d.mime_type == "application/pdf":
                originals[str(d.id)] = await storage.get(d.storage_key)
    options = {**(template.options if template else {}), **body.options}
    try:
        built = build_export(
            body.format,
            rows,
            template_columns=template.columns if template else None,
            options=options,
            originals=originals,
        )
    except ExportError as e:
        raise HTTPException(422, {"error": "export_failed", "message": str(e)}) from e
    export = Export(
        id=uuid.uuid4(),
        firm_id=firm.id,
        client_company_id=body.filters.client_company_id if body.filters else None,
        template_id=template.id if template else None,
        format=body.format,
        file_name=built.file_name,
        media_type=built.media_type,
        storage_key="",
        size_bytes=len(built.data),
        sha256=hashlib.sha256(built.data).hexdigest(),
        document_count=built.document_count,
        document_ids=[str(d.id) for d in docs],
        filters=body.filters.model_dump(mode="json") if body.filters else {},
        created_by_user_id=actor.user_id,
    )
    export.storage_key = f"exports/{firm.id}/{export.id}/{built.file_name}"
    await _storage(request).put(export.storage_key, built.data)
    session.add(export)
    await session.flush()
    await record_audit(
        session,
        firm_id=firm.id,
        actor=actor,
        action="export.create",
        target_type="export",
        target_id=str(export.id),
        after={"format": body.format, "documents": built.document_count, "sha256": export.sha256},
    )
    out = export_out(export)
    await emit_event(session, firm.id, snap, "export.completed", out.model_dump())
    await idempotency_store(session, idem, firm.id, 201, out.model_dump())
    queue = request.app.state.queue
    if queue is not None:
        await queue.kick_webhooks()
    return JSONResponse(status_code=201, content=out.model_dump())


@router.get("/exports", response_model=list[ExportOut])
async def list_exports(
    session: SessionDep,
    actor: Actor = Depends(require("exports.view")),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> list[ExportOut]:
    rows = (
        (
            await session.execute(
                _scoped_exports(select(Export), actor)
                .order_by(Export.created_at.desc())
                .offset(offset)
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return [export_out(e) for e in rows]


@router.get("/exports/{export_id}", response_model=ExportOut)
async def get_export(
    export_id: uuid.UUID, session: SessionDep, actor: Actor = Depends(require("exports.view"))
) -> ExportOut:
    e = await session.get(Export, export_id)
    if e is not None and not _export_visible(e, actor):
        e = None
    if e is None:
        raise HTTPException(404, {"error": "not_found", "message": "Export not found."})
    return export_out(e)


@router.get("/exports/{export_id}/download")
async def download_export(
    request: Request,
    export_id: uuid.UUID,
    session: SessionDep,
    firm: FirmDep,
    actor: Actor = Depends(require("exports.view")),
) -> Response:
    e = await session.get(Export, export_id)
    if e is not None and not _export_visible(e, actor):
        e = None
    if e is None:
        raise HTTPException(404, {"error": "not_found", "message": "Export not found."})
    data = await _storage(request).get(e.storage_key)
    await record_audit(
        session,
        firm_id=firm.id,
        actor=actor,
        action="export.download",
        target_type="export",
        target_id=str(e.id),
    )
    return Response(
        content=data,
        media_type=e.media_type,
        headers={
            "content-disposition": f'attachment; filename="{e.file_name}"',
            "cache-control": "private, no-store",
            "x-content-sha256": e.sha256,
        },
    )


@router.get("/documents/{document_id}/export")
async def export_single_document(
    request: Request,
    document_id: uuid.UUID,
    session: SessionDep,
    firm: FirmDep,
    svc: LicenseServiceDep,
    actor: Actor = Depends(require("exports.create")),
    format: str = Query(default="json", alias="format"),  # noqa: A002
) -> Response:
    """One document straight to a file (no export row): for the review screen's download menu."""
    check_feature(svc.snapshot(firm), "core")
    if format not in EXPORT_FORMATS:
        raise HTTPException(422, {"error": "invalid_request", "message": f"Unknown format '{format}'."})
    d = await session.get(Document, document_id)
    if d is None or d.deleted_at is not None or not actor.in_scope(d.client_company_id):
        raise HTTPException(404, {"error": "not_found", "message": "Document not found."})
    if d.extracted is None:
        raise HTTPException(
            409, {"error": "not_processed", "message": "This document has not been processed yet."}
        )
    rows = await _rows_for(session, [d])
    originals = {}
    if format == "facturx" and d.mime_type == "application/pdf":
        originals[str(d.id)] = await _storage(request).get(d.storage_key)
    try:
        built = build_export(format, rows, originals=originals)
    except ExportError as e:
        raise HTTPException(422, {"error": "export_failed", "message": str(e)}) from e
    await record_audit(
        session,
        firm_id=firm.id,
        actor=actor,
        action="document.export",
        target_type="document",
        target_id=str(d.id),
        after={"format": format},
    )
    return Response(
        content=built.data,
        media_type=built.media_type,
        headers={
            "content-disposition": f'attachment; filename="{built.file_name}"',
            "cache-control": "private, no-store",
        },
    )


_ = func  # sqlalchemy func kept for count queries in later iterations
