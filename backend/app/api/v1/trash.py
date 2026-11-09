"""Trash API. Every call needs a justification; see ``app.trash``."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import FirmDep, SessionDep, require
from app.audit.service import record_audit
from app.auth.rbac import allowed
from app.models.document import Document
from app.models.trash import TrashRequest
from app.tenancy.context import Actor
from app.trash import (
    DIRECT_LIMIT,
    PURGE_COOLING,
    REQUEST_TTL,
    TrashError,
    execute,
    purge,
    restore,
    select_documents,
    split_protected,
)

router = APIRouter(tags=["trash"])


class TrashFilter(BaseModel):
    client_company_id: uuid.UUID | None = None
    channel: str | None = Field(default=None, max_length=16)
    sender: str | None = Field(default=None, max_length=320)
    status: str | None = Field(default=None, max_length=16)
    received_from: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    received_to: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")


class TrashIn(BaseModel):
    document_ids: list[uuid.UUID] = Field(default_factory=list, max_length=5000)
    filter: TrashFilter | None = None
    reason: str = Field(min_length=3, max_length=2000)


class PreviewIn(BaseModel):
    document_ids: list[uuid.UUID] = Field(default_factory=list, max_length=5000)
    filter: TrashFilter | None = None


def _flt(f: TrashFilter | None) -> dict[str, Any] | None:
    if f is None:
        return None
    d = {k: (str(v) if v is not None else None) for k, v in f.model_dump().items()}
    return {k: v for k, v in d.items() if v} or None


async def _resolve(
    session: Any, ids: list[uuid.UUID], f: TrashFilter | None
) -> tuple[list[Document], list[dict[str, str]]]:
    flt = _flt(f)
    if not ids and not flt:
        raise HTTPException(422, {"error": "invalid_request", "message": "Choose documents or a filter."})
    try:
        docs = await select_documents(session, ids or None, flt)
    except ValueError as e:
        raise HTTPException(422, {"error": "invalid_request", "message": str(e)}) from e
    return await split_protected(session, docs)


@router.post("/documents/trash/preview")
async def preview(
    body: PreviewIn, session: SessionDep, actor: Actor = Depends(require("documents.trash"))
) -> dict[str, Any]:
    ok, skipped = await _resolve(session, body.document_ids, body.filter)
    return {
        "count": len(ok),
        "protected": skipped,
        "needs_approval": len(ok) > DIRECT_LIMIT and not allowed(actor.role, "documents.trash_approve"),
        "direct_limit": DIRECT_LIMIT,
    }


@router.post("/documents/trash")
async def trash(
    body: TrashIn, session: SessionDep, firm: FirmDep, actor: Actor = Depends(require("documents.trash"))
) -> dict[str, Any]:
    ok, skipped = await _resolve(session, body.document_ids, body.filter)
    if not ok:
        raise HTTPException(
            409,
            {
                "error": "nothing_to_trash",
                "message": "No document can be moved to the trash.",
                "protected": skipped,
            },
        )
    flt = _flt(body.filter)
    owner = allowed(actor.role, "documents.trash_approve")
    if len(ok) <= DIRECT_LIMIT:
        n = await execute(session, firm, ok, actor, body.reason, flt=flt)
        return {"status": "executed", "trashed": n, "protected": skipped, "request_id": None}
    now = datetime.now(UTC)
    req = TrashRequest(
        firm_id=firm.id,
        requested_by_user_id=actor.user_id,
        reason=body.reason,
        document_ids=[str(d.id) for d in ok],
        filter=flt,
        count=len(ok),
        expires_at=now + REQUEST_TTL,
    )
    session.add(req)
    await session.flush()
    await record_audit(
        session,
        firm_id=firm.id,
        actor=actor,
        action="trash_request.create",
        target_type="trash_request",
        target_id=str(req.id),
        after={"count": len(ok), "reason": body.reason, "filter": flt},
    )
    if not owner:
        return {"status": "pending_approval", "trashed": 0, "protected": skipped, "request_id": str(req.id)}
    # a firm owner's own bulk trash: approved by themselves, and marked so
    req.status, req.self_approved = "executed", True
    req.decided_by_user_id, req.decided_at, req.decision_note = actor.user_id, now, "self-approved"
    req.executed_count = await execute(session, firm, ok, actor, body.reason, request=req, flt=flt)
    await record_audit(
        session,
        firm_id=firm.id,
        actor=actor,
        action="trash_request.approve",
        target_type="trash_request",
        target_id=str(req.id),
        after={"self_approved": True, "executed": req.executed_count},
    )
    return {
        "status": "executed",
        "trashed": req.executed_count,
        "protected": skipped,
        "request_id": str(req.id),
    }


def _request_out(r: TrashRequest) -> dict[str, Any]:
    return {
        "id": str(r.id),
        "requested_by_user_id": str(r.requested_by_user_id),
        "reason": r.reason,
        "count": r.count,
        "filter": r.filter,
        "status": r.status,
        "decided_by_user_id": str(r.decided_by_user_id) if r.decided_by_user_id else None,
        "decision_note": r.decision_note,
        "self_approved": r.self_approved,
        "executed_count": r.executed_count,
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "decided_at": r.decided_at.isoformat() if r.decided_at else None,
        "expires_at": r.expires_at.isoformat(),
    }


@router.get("/trash-requests")
async def list_requests(
    session: SessionDep, actor: Actor = Depends(require("documents.trash"))
) -> list[dict[str, Any]]:
    rows = (
        await session.execute(select(TrashRequest).order_by(TrashRequest.created_at.desc()).limit(200))
    ).scalars()
    return [_request_out(r) for r in rows]


class DecisionIn(BaseModel):
    note: str = Field(min_length=3, max_length=2000)


async def _pending(session: AsyncSession, request_id: uuid.UUID) -> TrashRequest:
    r = await session.get(TrashRequest, request_id)
    if r is None:
        raise HTTPException(404, {"error": "not_found", "message": "Request not found."})
    if r.status != "pending":
        raise HTTPException(409, {"error": "not_pending", "message": f"This request is {r.status}."})
    if r.expires_at <= datetime.now(UTC):
        r.status = "expired"
        raise HTTPException(409, {"error": "expired", "message": "This request has expired."})
    return r


@router.post("/trash-requests/{request_id}/approve")
async def approve(
    request_id: uuid.UUID,
    body: DecisionIn,
    session: SessionDep,
    firm: FirmDep,
    actor: Actor = Depends(require("documents.trash_approve")),
) -> dict[str, Any]:
    r = await _pending(session, request_id)
    docs = await select_documents(session, [uuid.UUID(i) for i in r.document_ids], None)
    r.status, r.decided_by_user_id, r.decided_at, r.decision_note = (
        "executed",
        actor.user_id,
        datetime.now(UTC),
        body.note,
    )
    r.self_approved = r.requested_by_user_id == actor.user_id
    r.executed_count = await execute(session, firm, docs, actor, r.reason, request=r, flt=r.filter)
    await record_audit(
        session,
        firm_id=firm.id,
        actor=actor,
        action="trash_request.approve",
        target_type="trash_request",
        target_id=str(r.id),
        after={"note": body.note, "executed": r.executed_count, "self_approved": r.self_approved},
    )
    return _request_out(r)


@router.post("/trash-requests/{request_id}/reject")
async def reject(
    request_id: uuid.UUID,
    body: DecisionIn,
    session: SessionDep,
    firm: FirmDep,
    actor: Actor = Depends(require("documents.trash_approve")),
) -> dict[str, Any]:
    r = await _pending(session, request_id)
    r.status, r.decided_by_user_id, r.decided_at, r.decision_note = (
        "rejected",
        actor.user_id,
        datetime.now(UTC),
        body.note,
    )
    await record_audit(
        session,
        firm_id=firm.id,
        actor=actor,
        action="trash_request.reject",
        target_type="trash_request",
        target_id=str(r.id),
        after={"note": body.note},
    )
    return _request_out(r)


@router.get("/trash")
async def list_trash(
    session: SessionDep, actor: Actor = Depends(require("documents.trash"))
) -> list[dict[str, Any]]:
    rows = (
        await session.execute(
            select(Document)
            .where(Document.deleted_at.is_not(None))
            .order_by(Document.deleted_at.desc())
            .limit(500)
        )
    ).scalars()
    return [
        {
            "id": str(d.id),
            "original_filename": d.original_filename,
            "client_company_id": str(d.client_company_id) if d.client_company_id else None,
            "channel": d.channel.value,
            "sender_identity": d.sender_identity,
            "received_at": d.received_at.isoformat() if d.received_at else None,
            "deleted_at": d.deleted_at.isoformat() if d.deleted_at else None,
            "deleted_by_user_id": str(d.deleted_by_user_id) if d.deleted_by_user_id else None,
            "delete_reason": d.delete_reason,
            "purge_after": d.purge_after.isoformat() if d.purge_after else None,
            "purged_at": d.purged_at.isoformat() if d.purged_at else None,
            "sha256": d.sha256,
        }
        for d in rows
    ]


class ReasonIn(BaseModel):
    reason: str = Field(min_length=3, max_length=2000)


@router.post("/trash/{document_id}/restore")
async def restore_document(
    document_id: uuid.UUID,
    body: ReasonIn,
    session: SessionDep,
    firm: FirmDep,
    actor: Actor = Depends(require("documents.trash")),
) -> dict[str, str]:
    d = await session.get(Document, document_id)
    if d is None:
        raise HTTPException(404, {"error": "not_found", "message": "Document not found."})
    try:
        await restore(session, firm, d, actor, body.reason)
    except TrashError as e:
        raise HTTPException(409, {"error": e.code, "message": str(e)}) from e
    return {"id": str(d.id), "status": "restored"}


@router.post("/trash/{document_id}/purge")
async def purge_now(
    document_id: uuid.UUID,
    body: ReasonIn,
    request: Request,
    session: SessionDep,
    firm: FirmDep,
    actor: Actor = Depends(require("documents.purge")),
) -> dict[str, str]:
    d = await session.get(Document, document_id)
    if d is None or d.deleted_at is None or d.purged_at is not None:
        raise HTTPException(404, {"error": "not_found", "message": "This document is not in the trash."})
    if datetime.now(UTC) - d.deleted_at < PURGE_COOLING:
        raise HTTPException(
            409,
            {
                "error": "cooling_off",
                "message": "A document can be deleted for good 24 hours after it went to the trash.",
            },
        )
    await purge(session, request.app.state.storage, firm.id, d, actor, body.reason)
    return {"id": str(d.id), "status": "purged"}
