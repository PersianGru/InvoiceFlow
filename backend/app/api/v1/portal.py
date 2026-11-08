"""The client portal's summary. Everything else the portal needs already exists and is
scoped to the client's company by the existing routes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import func, select

from app.api.deps import SessionDep, require
from app.models.comment import DocumentComment
from app.models.document import Document
from app.tenancy.context import Actor

router = APIRouter(prefix="/my", tags=["portal"])


@router.get("/summary")
async def summary(session: SessionDep, actor: Actor = Depends(require("documents.view"))) -> dict[str, Any]:
    stmt = (
        select(Document.status, func.count()).where(Document.deleted_at.is_(None)).group_by(Document.status)
    )
    if actor.is_client:
        stmt = stmt.where(Document.client_company_id == actor.client_company_id)
    rows = (await session.execute(stmt)).all()
    by_status = {str(getattr(status, "value", status)): int(n) for status, n in rows}
    q = (
        select(func.count())
        .select_from(DocumentComment)
        .join(Document, Document.id == DocumentComment.document_id)
        .where(
            DocumentComment.kind == "question",
            DocumentComment.resolved_at.is_(None),
            DocumentComment.visible_to_client.is_(True),
            Document.deleted_at.is_(None),
        )
    )
    if actor.is_client:
        q = q.where(Document.client_company_id == actor.client_company_id)
    open_questions = int((await session.execute(q)).scalar_one())
    return {"by_status": by_status, "total": sum(by_status.values()), "open_questions": open_questions}
