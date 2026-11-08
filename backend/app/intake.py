"""The one "store an inbound file" function every channel uses: the original goes to the
archive untouched, the row records channel, sender identity, hash and client company. Callers
have already passed the license gate and the monthly limit; this never checks them."""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.service import record_audit
from app.compliance.retention import retention_until_for
from app.models.client_company import ClientCompany
from app.models.document import Document, DocumentChannel, DocumentStatus
from app.models.firm import Firm
from app.storage.base import Storage
from app.tenancy.context import Actor


async def store_document(
    session: AsyncSession,
    storage: Storage,
    firm: Firm,
    *,
    data: bytes,
    filename: str,
    mime: str,
    client_company_id: uuid.UUID | None,
    channel: DocumentChannel,
    sender_identity: str,
    actor: Actor | None = None,
    status: DocumentStatus = DocumentStatus.received,
    error: str | None = None,
) -> Document:
    digest = hashlib.sha256(data).hexdigest()
    country = firm.country
    if client_company_id is not None:
        cc_country = (
            await session.execute(select(ClientCompany.country).where(ClientCompany.id == client_company_id))
        ).scalar_one_or_none()
        country = cc_country or country
    received_at = datetime.now(UTC)
    retention_until, rule = retention_until_for(
        received_at=received_at, country=country, firm_retention_days=firm.retention_days
    )
    doc = Document(
        firm_id=firm.id,
        client_company_id=client_company_id,
        channel=channel,
        sender_identity=sender_identity[:320],
        original_filename=(filename or "upload")[:255],
        mime_type=mime[:127],
        size_bytes=len(data),
        sha256=digest,
        storage_key="",
        uploaded_by_user_id=actor.user_id if actor is not None and not actor.is_api_key else None,
        retention_until=retention_until,
        retention_rule=rule,
        status=status,
        error=error,
    )
    doc.id = uuid.uuid4()
    doc.storage_key = f"firms/{firm.id}/documents/{doc.id}/original"
    await storage.put(
        doc.storage_key, data, retain_until=retention_until
    )  # the original, untouched (archive)
    session.add(doc)
    await session.flush()
    await record_audit(
        session,
        firm_id=firm.id,
        actor=actor,
        action=(
            "document.upload"
            if channel in (DocumentChannel.upload, DocumentChannel.api)
            else "document.receive"
        ),
        target_type="document",
        target_id=str(doc.id),
        after={
            "sha256": digest,
            "size": len(data),
            "channel": channel.value,
            "mime": mime,
            "status": status.value,
            "sender": sender_identity[:120],
        },
    )
    return doc


async def documents_this_month(session: AsyncSession) -> int:
    """Documents stored since the first of the month (the ``max_documents_per_month`` counter)."""
    now = datetime.now(UTC)
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return int(
        (
            await session.execute(
                select(func.count()).select_from(Document).where(Document.received_at >= start)
            )
        ).scalar_one()
    )
