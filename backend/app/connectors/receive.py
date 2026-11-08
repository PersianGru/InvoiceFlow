"""Receiving documents from e-invoicing networks.

One poll per enabled receiving connector: the license gate (``core`` + ``connectors``) and the
monthly document limit run **before anything is stored**; the receiver identifier routes to the
client company (unknown receivers are quarantined, never processed); the one intake function
stores the original; the provider is acknowledged only after the document is stored; provider
ids de-duplicate re-deliveries. Nothing is ever acknowledged or advanced past when the gate or
the limit refused: the provider keeps the documents until the firm is licensed again.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.audit.service import record_audit
from app.connectors.base import ReceivedItem, ReceivingConnector
from app.connectors.service import ConnectorRegistry, effective_config
from app.db.rls import set_platform_bypass, set_tenant
from app.intake import documents_this_month, store_document
from app.jobs.queue import JobQueue
from app.licensing.gate import LicenseLimitReached, check_limit
from app.licensing.service import LicenseService
from app.models.client_company import ClientCompany
from app.models.connector import ClientConnectorCredential, ConnectorRun, FirmConnector
from app.models.document import DocumentChannel, DocumentStatus
from app.models.firm import Firm
from app.pipeline import security
from app.storage.base import Storage
from app.tenancy.context import Actor
from app.webhooks import document_payload, emit_event

log = logging.getLogger("invoiceflow.connectors.receive")

CHANNEL_FOR_KEY = {
    "peppol": DocumentChannel.peppol,
    "ksef": DocumentChannel.ksef,
    "france_pdp": DocumentChannel.pdp,
}


def _normalise_id(value: str | None) -> str:
    return "".join(ch for ch in (value or "").strip().lower() if not ch.isspace())


async def _route(session: AsyncSession, connector: Any, receiver: str | None) -> uuid.UUID | None:
    field = getattr(connector, "routing_field", None)
    wanted = _normalise_id(receiver)
    if not field or not wanted:
        return None
    column = ClientCompany.peppol_id if field == "peppol_id" else ClientCompany.tax_number
    rows = (await session.execute(select(ClientCompany).where(column.is_not(None)))).scalars().all()
    for c in rows:
        if _normalise_id(getattr(c, field)) == wanted and c.is_active:
            return c.id
    return None


async def _already_received(session: AsyncSession, key: str, external_id: str) -> bool:
    row = (
        await session.execute(
            select(ConnectorRun.id).where(
                ConnectorRun.key == key,
                ConnectorRun.trigger == "receive",
                ConnectorRun.external_id == external_id,
                ConnectorRun.status == "ok",
            )
        )
    ).first()
    return row is not None


def _run(
    firm_id: uuid.UUID,
    key: str,
    status: str,
    message: str,
    *,
    external_id: str | None = None,
    document_id: uuid.UUID | None = None,
) -> ConnectorRun:
    return ConnectorRun(
        firm_id=firm_id,
        key=key,
        document_id=document_id,
        trigger="receive",
        status=status,
        external_id=external_id,
        message=message[:500],
    )


async def receive_for_firm(
    session: AsyncSession,
    *,
    registry: ConnectorRegistry,
    storage: Storage,
    service: LicenseService,
    scanner: security.VirusScanner,
    queue: JobQueue | None,
    firm: Firm,
    key: str,
    actor: Actor | None = None,
    stored_ids: list[uuid.UUID] | None = None,
) -> dict[str, int]:
    """Poll one connector for one firm. Returns counts: stored, quarantined, skipped, errors, duplicates.
    Stored document ids are appended to ``stored_ids``; the caller enqueues processing **after** the
    transaction is committed (the worker reads the rows in its own session)."""
    counts = {"stored": 0, "quarantined": 0, "skipped": 0, "errors": 0, "duplicates": 0}
    if stored_ids is None:
        stored_ids = []
    connector = registry.get(key)
    row = await session.get(FirmConnector, (firm.id, key))
    if connector is None or row is None or not row.enabled or not isinstance(connector, ReceivingConnector):
        return counts
    if not (row.settings or {}).get("receive"):
        return counts
    channel = CHANNEL_FOR_KEY.get(key)
    if channel is None:
        return counts
    snap = service.snapshot(firm)
    if not snap.core_on or not snap.feature_on("connectors"):  # point of use: nothing is pulled
        session.add(_run(firm.id, key, "skipped", "license_required: connectors"))
        await record_audit(
            session,
            firm_id=firm.id,
            actor=actor,
            action="connector.receive",
            target_type="connector",
            target_id=key,
            result="refused",
            after={"reason": "license_required"},
        )
        counts["skipped"] += 1
        return counts
    secrets = service.secrets(firm)
    firm_config = effective_config(secrets, row)

    sources: list[
        tuple[dict[str, Any], str | None, uuid.UUID | None, FirmConnector | ClientConnectorCredential]
    ]
    if connector.receive_scope == "client":
        creds = (
            (
                await session.execute(
                    select(ClientConnectorCredential).where(
                        ClientConnectorCredential.key == key, ClientConnectorCredential.enabled.is_(True)
                    )
                )
            )
            .scalars()
            .all()
        )
        sources = [
            ({**firm_config, **effective_config(secrets, c)}, c.cursor, c.client_company_id, c) for c in creds
        ]
    else:
        sources = [(firm_config, (row.settings or {}).get("cursor"), None, row)]

    for config, cursor, client_id, source_row in sources:
        try:
            result = await connector.receive(config, cursor)
        except Exception as e:  # noqa: BLE001 - a provider failure is a run error, never a crash
            log.warning("connector %s receive failed: %s", key, type(e).__name__)
            session.add(_run(firm.id, key, "error", f"receive failed: {type(e).__name__}"))
            if isinstance(source_row, ClientConnectorCredential):
                source_row.last_error = f"receive failed: {type(e).__name__}"[:300]
            counts["errors"] += 1
            continue
        acked: list[str] = []
        blocked = False
        for item in result.items:
            if await _already_received(session, key, item.external_id):
                counts["duplicates"] += 1
                acked.append(item.external_id)
                continue
            outcome = await _store_item(
                session,
                storage=storage,
                scanner=scanner,
                stored_ids=stored_ids,
                firm=firm,
                snap=snap,
                connector=connector,
                channel=channel,
                key=key,
                item=item,
                client_id=client_id,
                actor=actor,
            )
            if outcome == "limit":
                blocked = True
                break
            counts[outcome] += 1
            acked.append(item.external_id)  # malformed/malware items are acknowledged too (they are logged)
        if acked:
            try:
                await connector.acknowledge(config, acked)
            except Exception as e:  # noqa: BLE001
                log.warning("connector %s ack failed: %s", key, type(e).__name__)
        if not blocked and result.cursor:
            if isinstance(source_row, ClientConnectorCredential):
                source_row.cursor = result.cursor[:200]
            else:
                source_row.settings = {**(source_row.settings or {}), "cursor": result.cursor[:200]}
        if isinstance(source_row, ClientConnectorCredential):
            source_row.last_pull_at = datetime.now(UTC)
            source_row.last_error = None if not blocked else "license_limit_reached"
    await session.flush()
    await record_audit(
        session,
        firm_id=firm.id,
        actor=actor,
        action="connector.receive",
        target_type="connector",
        target_id=key,
        after=counts,
    )
    return counts


async def _store_item(
    session: AsyncSession,
    *,
    storage: Storage,
    scanner: security.VirusScanner,
    stored_ids: list[uuid.UUID],
    firm: Firm,
    snap: Any,
    connector: Any,
    channel: DocumentChannel,
    key: str,
    item: ReceivedItem,
    client_id: uuid.UUID | None,
    actor: Actor | None,
) -> str:
    """Returns the counter to bump: stored | quarantined | errors | skipped, or ``limit`` to stop."""
    try:
        security.check_size(item.data)
        mime, _ext = security.check_type(item.data)
    except security.IntakeRejectedError as e:
        session.add(_run(firm.id, key, "error", f"rejected: {e.code}", external_id=item.external_id))
        return "errors"
    scan = await scanner.scan(item.data)
    if not scan.clean:
        session.add(_run(firm.id, key, "error", "rejected: malware", external_id=item.external_id))
        await record_audit(
            session,
            firm_id=firm.id,
            actor=None,
            action="document.receive",
            target_type="document",
            target_id=item.external_id[:120],
            after={"channel": channel.value},
            result="malware",
        )
        return "errors"
    try:
        check_limit(snap, "max_documents_per_month", await documents_this_month(session), adding=1)
    except LicenseLimitReached:
        session.add(_run(firm.id, key, "skipped", "license_limit_reached: max_documents_per_month"))
        return "limit"
    target = client_id or item.client_company_id or await _route(session, connector, item.receiver_id)
    status = DocumentStatus.received if target is not None else DocumentStatus.quarantined
    doc = await store_document(
        session,
        storage,
        firm,
        data=item.data,
        filename=item.filename,
        mime=mime,
        client_company_id=target,
        channel=channel,
        sender_identity=item.sender_identity or f"{key}:{item.receiver_id or ''}",
        actor=actor,
        status=status,
        error=None if target is not None else "unknown_receiver",
    )
    session.add(_run(firm.id, key, "ok", "received", external_id=item.external_id, document_id=doc.id))
    await session.flush()
    if status == DocumentStatus.received:
        await emit_event(session, firm.id, snap, "document.received", document_payload(doc))
        stored_ids.append(doc.id)
        return "stored"
    return "quarantined"


async def receive_tick(
    sessionmaker: async_sessionmaker[AsyncSession],
    registry: ConnectorRegistry,
    storage: Storage,
    service: LicenseService,
    scanner: security.VirusScanner,
    queue: JobQueue | None,
) -> dict[str, int]:
    """Worker cron: poll every enabled receiving connector of every firm, one transaction per firm."""
    totals = {"stored": 0, "quarantined": 0, "skipped": 0, "errors": 0, "duplicates": 0}
    async with sessionmaker() as session:
        async with session.begin():
            await set_platform_bypass(session)
            rows = (
                await session.execute(
                    select(FirmConnector.firm_id, FirmConnector.key).where(FirmConnector.enabled.is_(True))
                )
            ).all()
    for firm_id, key in rows:
        connector: object | None = registry.get(key)
        if not isinstance(connector, ReceivingConnector):
            continue
        stored: list[uuid.UUID] = []
        async with sessionmaker() as session:
            async with session.begin():
                await set_platform_bypass(session)
                firm = await session.get(Firm, firm_id)
                if firm is None:
                    continue
                await set_tenant(session, firm.id)
                counts = await receive_for_firm(
                    session,
                    registry=registry,
                    storage=storage,
                    service=service,
                    scanner=scanner,
                    queue=queue,
                    firm=firm,
                    key=key,
                    stored_ids=stored,
                )
                for k, v in counts.items():
                    totals[k] += v
        if queue is not None:
            for doc_id in stored:  # after the commit
                await queue.enqueue_process_document(doc_id)
    return totals
