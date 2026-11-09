"""Trash for documents.

* Every step needs a justification and is audited per document (who, when, why, file hash, filter).
* Up to 5 documents: an accountant or firm owner trashes them at once. More than 5: an accountant asks
  and a firm owner approves (with a note); a firm owner's own bulk trash is self-approved and marked so.
  Requests expire after 7 days.
* Bookkeeping records are protected (GoBD): approved, exported, pushed to a connector, or exchanged
  through an e-invoicing network. They are corrected, not deleted.
* Trashed documents can be restored. Their files are deleted for good after the firm's retention
  (default 30 days, 7–90); a firm owner may delete one for good earlier, but not before it has been in
  the trash for 24 hours. The document row stays as a stub, so history and the audit log stay whole.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.service import record_audit
from app.models.connector import ConnectorRun
from app.models.document import Document, DocumentChannel, DocumentStatus
from app.models.firm import Firm
from app.models.integration import Export
from app.models.trash import TrashRequest
from app.storage.base import Storage
from app.tenancy.context import Actor

DIRECT_LIMIT = 5
REQUEST_TTL = timedelta(days=7)
PURGE_COOLING = timedelta(hours=24)
DEFAULT_RETENTION_DAYS = 30
NETWORK_CHANNELS = (DocumentChannel.peppol, DocumentChannel.ksef, DocumentChannel.pdp)


class TrashError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def retention_days(firm: Firm) -> int:
    v = (firm.settings or {}).get("trash_retention_days", DEFAULT_RETENTION_DAYS)
    return max(7, min(int(v), 90)) if isinstance(v, int) else DEFAULT_RETENTION_DAYS


async def protected_reason(session: AsyncSession, d: Document) -> str | None:
    """Why ``d`` may not be trashed, or None."""
    if d.status == DocumentStatus.approved or d.review_status == "approved":
        return "approved"
    if d.channel in NETWORK_CHANNELS:
        return "e_invoicing_network"
    exported = (
        await session.execute(
            select(Export.id)
            .where(Export.status == "done", Export.document_ids.contains([str(d.id)]))
            .limit(1)
        )
    ).first()
    if exported:
        return "exported"
    pushed = (
        await session.execute(
            select(ConnectorRun.id)
            .where(ConnectorRun.document_id == d.id, ConnectorRun.status == "ok")
            .limit(1)
        )
    ).first()
    if pushed:
        return "pushed_to_connector"
    return None


async def select_documents(
    session: AsyncSession, ids: list[uuid.UUID] | None, flt: dict[str, Any] | None
) -> list[Document]:
    """Active (not trashed) documents by id list or by filter, in the caller's firm (RLS)."""
    stmt = select(Document).where(Document.deleted_at.is_(None))
    if ids:
        stmt = stmt.where(Document.id.in_(ids))
    elif flt:
        if flt.get("client_company_id"):
            stmt = stmt.where(Document.client_company_id == uuid.UUID(str(flt["client_company_id"])))
        if flt.get("channel"):
            stmt = stmt.where(Document.channel == DocumentChannel(str(flt["channel"])))
        if flt.get("sender"):
            stmt = stmt.where(Document.sender_identity == str(flt["sender"]).strip().lower())
        if flt.get("status"):
            stmt = stmt.where(Document.status == DocumentStatus(str(flt["status"])))
        if flt.get("received_from"):
            stmt = stmt.where(
                Document.received_at
                >= datetime.combine(date.fromisoformat(str(flt["received_from"])), datetime.min.time(), UTC)
            )
        if flt.get("received_to"):
            end = datetime.combine(date.fromisoformat(str(flt["received_to"])), datetime.min.time(), UTC)
            stmt = stmt.where(Document.received_at < end + timedelta(days=1))
    else:
        return []
    return list((await session.execute(stmt.order_by(Document.received_at).limit(5000))).scalars().all())


async def split_protected(
    session: AsyncSession, docs: list[Document]
) -> tuple[list[Document], list[dict[str, str]]]:
    ok: list[Document] = []
    skipped: list[dict[str, str]] = []
    for d in docs:
        reason = await protected_reason(session, d)
        if reason is None:
            ok.append(d)
        else:
            skipped.append({"id": str(d.id), "reason": reason})
    return ok, skipped


async def execute(
    session: AsyncSession,
    firm: Firm,
    docs: list[Document],
    actor: Actor,
    reason: str,
    *,
    request: TrashRequest | None = None,
    flt: dict[str, Any] | None = None,
) -> int:
    now = datetime.now(UTC)
    purge_after = now + timedelta(days=retention_days(firm))
    n = 0
    for d in docs:
        if d.deleted_at is not None or await protected_reason(session, d) is not None:
            continue
        d.deleted_at, d.deleted_by_user_id, d.delete_reason, d.purge_after = (
            now,
            actor.user_id,
            reason,
            purge_after,
        )
        n += 1
        await record_audit(
            session,
            firm_id=firm.id,
            actor=actor,
            action="document.trash",
            target_type="document",
            target_id=str(d.id),
            after={
                "reason": reason,
                "sha256": d.sha256,
                "file": d.original_filename,
                "request_id": str(request.id) if request else None,
                "filter": flt,
                "purge_after": purge_after.isoformat(),
            },
        )
    await record_audit(
        session,
        firm_id=firm.id,
        actor=actor,
        action="document.trash_bulk",
        target_type="firm",
        target_id=str(firm.id),
        after={
            "documents": n,
            "reason": reason,
            "request_id": str(request.id) if request else None,
            "filter": flt,
        },
    )
    return n


async def restore(session: AsyncSession, firm: Firm, d: Document, actor: Actor, reason: str) -> None:
    if d.deleted_at is None or d.purged_at is not None:
        raise TrashError(
            "not_in_trash", "This document is not in the trash (or was already deleted for good)."
        )
    d.deleted_at, d.deleted_by_user_id, d.delete_reason, d.purge_after = None, None, None, None
    await record_audit(
        session,
        firm_id=firm.id,
        actor=actor,
        action="document.restore",
        target_type="document",
        target_id=str(d.id),
        after={"reason": reason, "sha256": d.sha256},
    )


async def purge(
    session: AsyncSession, storage: Storage, firm_id: uuid.UUID, d: Document, actor: Actor | None, reason: str
) -> None:
    """Delete the stored file for good; the row stays as a stub."""
    try:
        await storage.delete(d.storage_key)
    except Exception:  # noqa: BLE001,S110 - already gone is fine; the stub records the purge either way
        pass
    d.purged_at = datetime.now(UTC)
    d.extracted = None
    await record_audit(
        session,
        firm_id=firm_id,
        actor=actor,
        action="document.purge",
        target_type="document",
        target_id=str(d.id),
        after={"reason": reason, "sha256": d.sha256, "file": d.original_filename},
    )


async def purge_due(session: AsyncSession, storage: Storage) -> int:
    """Worker: purge trashed documents whose retention has passed. Caller has the platform bypass."""
    rows = (
        (
            await session.execute(
                select(Document)
                .where(
                    Document.deleted_at.is_not(None),
                    Document.purged_at.is_(None),
                    Document.purge_after.is_not(None),
                    Document.purge_after <= datetime.now(UTC),
                )
                .limit(500)
            )
        )
        .scalars()
        .all()
    )
    for d in rows:
        await purge(session, storage, d.firm_id, d, None, "trash retention ended")
    return len(rows)


async def expire_requests(session: AsyncSession) -> int:
    rows = (
        (
            await session.execute(
                select(TrashRequest).where(
                    TrashRequest.status == "pending", TrashRequest.expires_at <= datetime.now(UTC)
                )
            )
        )
        .scalars()
        .all()
    )
    for r in rows:
        r.status = "expired"
        await record_audit(
            session,
            firm_id=r.firm_id,
            actor=None,
            action="trash_request.expired",
            target_type="trash_request",
            target_id=str(r.id),
        )
    return len(rows)
