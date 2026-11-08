"""Retention, scheduled deletion and data minimisation (GDPR + tax retention). Runs for every firm,
licensed or not: legal duties continue in the unlicensed version (spec §2 rule 9).

* documents past ``retention_until`` (or with an erasure request whose period has ended): the
  original is deleted from storage, the extracted record cleared, the row kept as a tombstone;
* audit-row IPs older than 12 months are nulled (owner-defined function: the audit log is append-only);
* channel message texts older than 12 months are purged (same mechanism);
* webhook deliveries older than 90 days and idempotency keys older than 24 hours are removed.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.rls import set_platform_bypass
from app.models.audit import AuditLog
from app.models.document import Document
from app.models.integration import IdempotencyKey, WebhookDelivery
from app.storage.base import Storage, StorageError

log = logging.getLogger("invoiceflow.jobs.retention")

AUDIT_IP_MONTHS = 12
CHANNEL_MESSAGE_MONTHS = 12
WEBHOOK_DELIVERY_DAYS = 90
IDEMPOTENCY_HOURS = 24


async def run_retention(
    sessionmaker: async_sessionmaker[AsyncSession], storage: Storage, *, now: datetime | None = None
) -> dict[str, int]:
    now = now or datetime.now(UTC)
    counts = {"deleted": 0, "audit_ips_scrubbed": 0, "channel_messages_purged": 0, "deliveries_purged": 0}
    async with sessionmaker() as session:
        async with session.begin():
            await set_platform_bypass(session)
            stmt = (
                select(Document)
                .where(Document.retention_until <= now, Document.deleted_at.is_(None))
                .limit(1000)
            )
            for doc in (await session.execute(stmt)).scalars().all():
                try:
                    await storage.delete(doc.storage_key)
                except StorageError:
                    log.warning("blob already gone for document %s", doc.id)
                doc.deleted_at = now
                doc.extracted = None
                session.add(
                    AuditLog(
                        firm_id=doc.firm_id,
                        actor_role="system",
                        action="document.retention_delete",
                        target_type="document",
                        target_id=str(doc.id),
                        after={
                            "retention_until": (
                                doc.retention_until.isoformat() if doc.retention_until else None
                            ),
                            "erasure_requested": doc.erasure_requested_at is not None,
                        },
                    )
                )
                counts["deleted"] += 1
            counts["audit_ips_scrubbed"] = int(
                (
                    await session.execute(
                        text("SELECT scrub_audit_ips(:before)"),
                        {"before": now - timedelta(days=30 * AUDIT_IP_MONTHS)},
                    )
                ).scalar_one()
            )
            counts["channel_messages_purged"] = int(
                (
                    await session.execute(
                        text("SELECT purge_channel_messages(:before)"),
                        {"before": now - timedelta(days=30 * CHANNEL_MESSAGE_MONTHS)},
                    )
                ).scalar_one()
            )
            res = await session.execute(
                delete(WebhookDelivery).where(
                    WebhookDelivery.created_at < now - timedelta(days=WEBHOOK_DELIVERY_DAYS)
                )
            )
            counts["deliveries_purged"] = int(getattr(res, "rowcount", 0) or 0)
            await session.execute(
                delete(IdempotencyKey).where(
                    IdempotencyKey.created_at < now - timedelta(hours=IDEMPOTENCY_HOURS)
                )
            )
    log.info("retention run: %s", counts)
    return counts
