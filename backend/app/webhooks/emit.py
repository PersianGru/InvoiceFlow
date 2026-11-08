"""The outbox: ``emit_event`` writes one pending delivery per subscribed, active endpoint inside the
caller's transaction. Nothing is sent here; the deliverer (worker) sends.

Webhooks belong to the ``api`` entitlement: an unlicensed firm, or one without ``api``, emits
nothing (outbound integrations are switched off in the unlicensed version)."""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.licensing.state import LicenseSnapshot
from app.models.document import Document
from app.models.integration import WebhookDelivery, WebhookEndpoint

log = logging.getLogger("invoiceflow.webhooks")


def document_payload(d: Document) -> dict[str, Any]:
    return {
        "id": str(d.id),
        "client_company_id": str(d.client_company_id) if d.client_company_id else None,
        "channel": d.channel.value,
        "status": d.status.value,
        "review_status": d.review_status,
        "original_filename": d.original_filename,
        "mime_type": d.mime_type,
        "sha256": d.sha256,
        "received_at": d.received_at.isoformat() if d.received_at else None,
        "document_type": d.document_type,
        "invoice_number": d.invoice_number,
        "supplier_name": d.supplier_name,
        "supplier_vat_id": d.supplier_vat_id,
        "issue_date": d.issue_date.isoformat() if d.issue_date else None,
        "currency": d.currency,
        "gross_total": str(d.gross_total) if d.gross_total is not None else None,
        "min_confidence": float(d.min_confidence) if d.min_confidence is not None else None,
        "structured_format": d.structured_format,
        "payment_status": d.payment_status or "unpaid",
        "error": d.error,
    }


def webhooks_allowed(snapshot: LicenseSnapshot) -> bool:
    return snapshot.core_on and snapshot.feature_on("api")


async def emit_event(
    session: AsyncSession,
    firm_id: uuid.UUID,
    snapshot: LicenseSnapshot | None,
    event_type: str,
    data: dict[str, Any],
    *,
    only_endpoint: uuid.UUID | None = None,
) -> int:
    """Returns the number of deliveries queued. ``snapshot=None`` skips the license check (used for
    the explicit test ping, which the UI only offers to licensed firms anyway)."""
    if snapshot is not None and not webhooks_allowed(snapshot):
        return 0
    stmt = select(WebhookEndpoint).where(WebhookEndpoint.firm_id == firm_id, WebhookEndpoint.active.is_(True))
    if only_endpoint is not None:
        stmt = stmt.where(WebhookEndpoint.id == only_endpoint)
    endpoints = (await session.execute(stmt)).scalars().all()
    event_id = uuid.uuid4()
    created_at = datetime.now(UTC)
    payload = {
        "id": str(event_id),
        "type": event_type,
        "created_at": created_at.isoformat(),
        "firm_id": str(firm_id),
        "data": json.loads(json.dumps(data, default=str)),
    }
    n = 0
    for ep in endpoints:
        if event_type != "ping" and event_type not in (ep.events or []):
            continue
        session.add(
            WebhookDelivery(
                firm_id=firm_id,
                endpoint_id=ep.id,
                event_id=event_id,
                event_type=event_type,
                payload=payload,
                next_attempt_at=created_at,
            )
        )
        n += 1
    if n:
        await session.flush()
        log.info("queued %d webhook delivery(ies) for %s", n, event_type)
    return n
