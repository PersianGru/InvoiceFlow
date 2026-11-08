"""The deliverer: POSTs due deliveries, signs them, records the outcome and schedules retries
(1 min, 5 min, 30 min, 2 h, 6 h). Runs in the worker (every 30 s) and in the inline queue."""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.rls import set_platform_bypass
from app.licensing.service import LicenseService
from app.licensing.store import FirmSecrets
from app.models.firm import Firm
from app.models.integration import WebhookDelivery, WebhookEndpoint
from app.webhooks.emit import webhooks_allowed
from app.webhooks.signing import sign

log = logging.getLogger("invoiceflow.webhooks")

RETRY_SCHEDULE = (60, 300, 1800, 7200, 21600)
MAX_ATTEMPTS = len(RETRY_SCHEDULE) + 1
DISABLE_AFTER = 50
TIMEOUT = 10.0
BATCH = 100


def endpoint_secret(master: FirmSecrets, ep: WebhookEndpoint) -> str | None:
    return master.open("webhook_secret", ep.secret_enc)


async def deliver_one(
    client: httpx.AsyncClient,
    delivery: WebhookDelivery,
    endpoint: WebhookEndpoint,
    secret: str | None,
    now: datetime,
) -> bool:
    delivery.attempts += 1
    delivery.last_attempt_at = now
    body = json.dumps(delivery.payload, separators=(",", ":"), ensure_ascii=False).encode()
    if secret is None:
        delivery.status, delivery.last_error = "failed", "endpoint_secret_unavailable"
        return False
    headers = {
        "content-type": "application/json",
        "user-agent": "InvoiceFlow-Webhooks/1.0",
        "x-invoiceflow-event": delivery.event_type,
        "x-invoiceflow-delivery": str(delivery.id),
        "x-invoiceflow-signature": sign(secret, body, int(now.timestamp())),
    }
    try:
        r = await client.post(endpoint.url, content=body, headers=headers, timeout=TIMEOUT)
        delivery.last_status_code = r.status_code
        ok = 200 <= r.status_code < 300
        delivery.last_error = None if ok else f"http_{r.status_code}"
    except httpx.HTTPError as e:
        delivery.last_status_code = None
        delivery.last_error = type(e).__name__[:200]
        ok = False
    if ok:
        delivery.status, delivery.delivered_at = "delivered", now
        endpoint.consecutive_failures = 0
        return True
    endpoint.consecutive_failures += 1
    if delivery.attempts >= MAX_ATTEMPTS:
        delivery.status = "failed"
    else:
        delivery.next_attempt_at = now + timedelta(seconds=RETRY_SCHEDULE[delivery.attempts - 1])
    if endpoint.consecutive_failures >= DISABLE_AFTER and endpoint.active:
        endpoint.active = False
        endpoint.disabled_at, endpoint.disabled_reason = now, "too_many_failures"
        log.warning(
            "webhook endpoint %s disabled after %d failures", endpoint.id, endpoint.consecutive_failures
        )
    return False


async def deliver_due(
    sessionmaker: async_sessionmaker[AsyncSession],
    service: LicenseService,
    client: httpx.AsyncClient | None = None,
    *,
    now: datetime | None = None,
    firm_id: uuid.UUID | None = None,
) -> dict[str, int]:
    """Send every pending delivery whose time has come. Returns counts."""
    now = now or datetime.now(UTC)
    counts = {"delivered": 0, "failed": 0, "retry": 0, "skipped": 0}
    own_client = client is None
    client = client or httpx.AsyncClient(follow_redirects=False)
    try:
        async with sessionmaker() as session:
            async with session.begin():
                await set_platform_bypass(session)
                stmt = (
                    select(WebhookDelivery)
                    .where(WebhookDelivery.status == "pending", WebhookDelivery.next_attempt_at <= now)
                    .order_by(WebhookDelivery.next_attempt_at)
                    .limit(BATCH)
                    .with_for_update(skip_locked=True)
                )
                if firm_id is not None:
                    stmt = stmt.where(WebhookDelivery.firm_id == firm_id)
                due = (await session.execute(stmt)).scalars().all()
                firms: dict[uuid.UUID, tuple[Firm, FirmSecrets]] = {}
                for d in due:
                    ep = await session.get(WebhookEndpoint, d.endpoint_id)
                    if ep is None or not ep.active:
                        d.status, d.last_error = "failed", "endpoint_inactive"
                        counts["skipped"] += 1
                        continue
                    if d.firm_id not in firms:
                        firm = await session.get(Firm, d.firm_id)
                        if firm is None:
                            d.status, d.last_error = "failed", "firm_missing"
                            counts["skipped"] += 1
                            continue
                        firms[d.firm_id] = (firm, FirmSecrets(service.master, firm))
                    firm, secrets_ = firms[d.firm_id]
                    if d.event_type != "ping" and not webhooks_allowed(service.snapshot(firm)):
                        d.next_attempt_at = now + timedelta(
                            hours=1
                        )  # keep, re-check later (license may return)
                        d.last_error = "license_required"
                        counts["skipped"] += 1
                        continue
                    ok = await deliver_one(client, d, ep, endpoint_secret(secrets_, ep), now)
                    if ok:
                        counts["delivered"] += 1
                    elif d.status == "failed":
                        counts["failed"] += 1
                    else:
                        counts["retry"] += 1
    finally:
        if own_client:
            await client.aclose()
    return counts
