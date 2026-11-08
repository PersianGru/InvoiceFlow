"""Data-subject rights and archive integrity. No license gate: these duties
continue in the unlicensed version.

* export: one ZIP with the originals, canonical JSON, comments and audit rows of a client company
  (or one document);
* restrict / unrestrict processing: restricted documents are skipped by the pipeline, exports and
  bulk actions, but stay readable;
* erasure request: documents whose retention has ended are deleted now; the others are restricted,
  marked and deleted by the retention job when their period ends (tax law first);
* verify: recompute the SHA-256 of the archived original and compare it with the recorded hash.
"""

from __future__ import annotations

import hashlib
import io
import json
import uuid
import zipfile
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import select

from app.api.deps import FirmDep, SessionDep, require
from app.audit.service import record_audit
from app.models.audit import AuditLog
from app.models.comment import DocumentComment
from app.models.document import Document, DocumentStatus
from app.storage.base import Storage, StorageError
from app.tenancy.context import Actor

router = APIRouter(prefix="/dsr", tags=["dsr"])


class Scope(BaseModel):
    client_company_id: uuid.UUID | None = None
    document_id: uuid.UUID | None = None


def _storage(request: Request) -> Storage:
    return request.app.state.storage  # type: ignore[no-any-return]


async def _documents(session: SessionDep, scope: Scope) -> list[Document]:
    if scope.document_id is None and scope.client_company_id is None:
        raise HTTPException(
            422, {"error": "invalid_request", "message": "Give a client_company_id or a document_id."}
        )
    stmt = select(Document).where(Document.deleted_at.is_(None))
    if scope.document_id is not None:
        stmt = stmt.where(Document.id == scope.document_id)
    else:
        stmt = stmt.where(Document.client_company_id == scope.client_company_id)
    rows = list((await session.execute(stmt.order_by(Document.received_at))).scalars().all())
    if not rows:
        raise HTTPException(404, {"error": "not_found", "message": "No documents in this scope."})
    return rows


def _doc_record(d: Document) -> dict[str, Any]:
    return {
        "id": str(d.id),
        "client_company_id": str(d.client_company_id) if d.client_company_id else None,
        "channel": d.channel.value,
        "sender_identity": d.sender_identity,
        "received_at": d.received_at.isoformat() if d.received_at else None,
        "original_filename": d.original_filename,
        "mime_type": d.mime_type,
        "size_bytes": d.size_bytes,
        "sha256": d.sha256,
        "status": d.status.value,
        "review_status": d.review_status,
        "retention_until": d.retention_until.isoformat() if d.retention_until else None,
        "retention_rule": d.retention_rule,
        "processing_restricted": d.processing_restricted,
        "erasure_requested_at": d.erasure_requested_at.isoformat() if d.erasure_requested_at else None,
        "extracted": d.extracted,
    }


@router.post("/export")
async def dsr_export(
    request: Request,
    scope: Scope,
    session: SessionDep,
    firm: FirmDep,
    actor: Actor = Depends(require("dsr.manage")),
) -> Response:
    docs = await _documents(session, scope)
    storage = _storage(request)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        records = []
        for d in docs:
            records.append(_doc_record(d))
            try:
                zf.writestr(
                    f"originals/{d.id}/{d.original_filename or 'original'}", await storage.get(d.storage_key)
                )
            except StorageError:
                zf.writestr(f"originals/{d.id}/MISSING.txt", "The original is no longer in storage.")
            comments = (
                (
                    await session.execute(
                        select(DocumentComment)
                        .where(DocumentComment.document_id == d.id)
                        .order_by(DocumentComment.id)
                    )
                )
                .scalars()
                .all()
            )
            if comments:
                zf.writestr(
                    f"comments/{d.id}.json",
                    json.dumps(
                        [
                            {
                                "kind": c.kind,
                                "body": c.body,
                                "created_at": c.created_at.isoformat() if c.created_at else None,
                                "visible_to_client": c.visible_to_client,
                            }
                            for c in comments
                        ],
                        ensure_ascii=False,
                        indent=1,
                    ),
                )
        zf.writestr("documents.json", json.dumps(records, ensure_ascii=False, indent=1, default=str))
        ids = [str(d.id) for d in docs]
        audits = (
            (
                await session.execute(
                    select(AuditLog)
                    .where(AuditLog.target_type == "document", AuditLog.target_id.in_(ids))
                    .order_by(AuditLog.id)
                )
            )
            .scalars()
            .all()
        )
        zf.writestr(
            "audit.json",
            json.dumps(
                [
                    {
                        "at": a.created_at.isoformat() if a.created_at else None,
                        "actor_role": a.actor_role,
                        "action": a.action,
                        "target_id": a.target_id,
                        "result": a.result,
                        "after": a.after,
                    }
                    for a in audits
                ],
                ensure_ascii=False,
                indent=1,
                default=str,
            ),
        )
        zf.writestr(
            "README.txt",
            "InvoiceFlow data-subject export. documents.json: metadata and extracted records; "
            "originals/: archived files; comments/: notes and questions; audit.json: who did what.\n",
        )
    await record_audit(
        session,
        firm_id=firm.id,
        actor=actor,
        action="dsr.export",
        target_type="client_company" if scope.client_company_id else "document",
        target_id=str(scope.client_company_id or scope.document_id),
        after={"documents": len(docs)},
    )
    name = f"dsr-export-{(scope.client_company_id or scope.document_id)}.zip"
    return Response(
        content=buf.getvalue(),
        media_type="application/zip",
        headers={
            "content-disposition": f'attachment; filename="{name}"',
            "cache-control": "private, no-store",
        },
    )


@router.post("/restrict")
async def dsr_restrict(
    scope: Scope, session: SessionDep, firm: FirmDep, actor: Actor = Depends(require("dsr.manage"))
) -> dict[str, Any]:
    docs = await _documents(session, scope)
    for d in docs:
        d.processing_restricted = True
    await record_audit(
        session,
        firm_id=firm.id,
        actor=actor,
        action="dsr.restrict",
        target_type="client_company" if scope.client_company_id else "document",
        target_id=str(scope.client_company_id or scope.document_id),
        after={"documents": len(docs)},
    )
    return {"restricted": len(docs)}


@router.post("/unrestrict")
async def dsr_unrestrict(
    scope: Scope, session: SessionDep, firm: FirmDep, actor: Actor = Depends(require("dsr.manage"))
) -> dict[str, Any]:
    docs = await _documents(session, scope)
    for d in docs:
        if d.erasure_requested_at is None:
            d.processing_restricted = False
    await record_audit(
        session,
        firm_id=firm.id,
        actor=actor,
        action="dsr.unrestrict",
        target_type="client_company" if scope.client_company_id else "document",
        target_id=str(scope.client_company_id or scope.document_id),
        after={"documents": len(docs)},
    )
    return {"unrestricted": sum(1 for d in docs if not d.processing_restricted)}


@router.post("/erase")
async def dsr_erase(
    request: Request,
    scope: Scope,
    session: SessionDep,
    firm: FirmDep,
    actor: Actor = Depends(require("dsr.manage")),
) -> dict[str, Any]:
    """Erasure that respects tax retention: delete what may be deleted, schedule the rest."""
    docs = await _documents(session, scope)
    now = datetime.now(UTC)
    storage = _storage(request)
    deleted, scheduled = 0, 0
    for d in docs:
        d.erasure_requested_at = d.erasure_requested_at or now
        d.processing_restricted = True
        if d.retention_until is None or d.retention_until <= now:
            try:
                await storage.delete(d.storage_key)
            except StorageError:
                pass
            d.deleted_at, d.extracted = now, None
            deleted += 1
        else:
            scheduled += 1
    await record_audit(
        session,
        firm_id=firm.id,
        actor=actor,
        action="dsr.erase",
        target_type="client_company" if scope.client_company_id else "document",
        target_id=str(scope.client_company_id or scope.document_id),
        after={"deleted_now": deleted, "scheduled": scheduled},
    )
    return {
        "deleted_now": deleted,
        "scheduled": scheduled,
        "note": (
            "Documents under tax retention are restricted now and deleted when their retention "
            "period ends."
        ),
    }


@router.get("/verify/{document_id}")
async def verify_document(
    request: Request,
    document_id: uuid.UUID,
    session: SessionDep,
    firm: FirmDep,
    actor: Actor = Depends(require("documents.view")),
) -> dict[str, Any]:
    d = await session.get(Document, document_id)
    if d is None or (actor.is_client and d.client_company_id != actor.client_company_id):
        raise HTTPException(404, {"error": "not_found", "message": "Document not found."})
    if d.deleted_at is not None:
        result: dict[str, Any] = {
            "status": "deleted",
            "recorded_sha256": d.sha256,
            "deleted_at": d.deleted_at.isoformat(),
        }
    else:
        try:
            data = await _storage(request).get(d.storage_key)
            actual = hashlib.sha256(data).hexdigest()
            result = {
                "status": "intact" if actual == d.sha256 else "MISMATCH",
                "recorded_sha256": d.sha256,
                "actual_sha256": actual,
                "size_bytes": len(data),
            }
        except StorageError:
            result = {"status": "missing", "recorded_sha256": d.sha256}
    await record_audit(
        session,
        firm_id=firm.id,
        actor=actor,
        action="document.verify",
        target_type="document",
        target_id=str(d.id),
        result=str(result["status"]),
    )
    return {
        "document_id": str(d.id),
        **result,
        "document_status": d.status.value if isinstance(d.status, DocumentStatus) else str(d.status),
    }
