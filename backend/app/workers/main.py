"""Arq worker: ``arq app.workers.main.WorkerSettings``.

Jobs: ``process_document`` (the pipeline). Cron: license refresh every minute (advisory lock per
firm), revocation list hourly (fetched weekly), retention daily (runs regardless of license state).
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from typing import Any

from arq import cron
from arq.connections import RedisSettings

from app.channels.email import parse_mime
from app.channels.imap import ImapMailbox, poll_mailbox
from app.channels.intake import IntakeDeps, handle_inbound
from app.channels.registry import ChannelHub
from app.config import get_settings
from app.connectors.receive import receive_tick
from app.connectors.service import ConnectorRegistry, auto_push_job
from app.db.session import dispose_engine, get_sessionmaker, init_engine
from app.jobs.retention import run_retention
from app.licensing.refresh import refresh_due_firms, refresh_revocations
from app.logging_setup import configure_logging
from app.mail import SmtpMailer
from app.main import build_license_service
from app.pipeline.factory import build_runner, build_scanner
from app.pipeline.jobs import process_document_job
from app.security.crypto import MasterKey
from app.settings_store import SettingsStore
from app.storage import build_storage
from app.webhooks.deliver import deliver_due

log = logging.getLogger("invoiceflow.worker")


async def startup(ctx: dict[str, Any]) -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    init_engine(settings.database_url)
    ctx["settings"] = settings
    ctx["service"] = build_license_service(settings)
    ctx["storage"] = build_storage(settings)
    ctx["store"] = SettingsStore(MasterKey(settings.master_key_bytes()))
    await _ensure_runner(ctx)
    ctx["channels"] = ChannelHub(settings)
    ctx["connectors"] = ConnectorRegistry()
    ctx["scanner"] = build_scanner(settings)
    await refresh_due_firms(
        get_sessionmaker(), ctx["service"], startup=True
    )  # spec §8.3: on start if older than 24 h


async def shutdown(ctx: dict[str, Any]) -> None:
    # A failed startup (e.g. a settings error) runs shutdown with a partial context: the startup error
    # must stay the one in the log, so close only what was created.
    for key in ("channels", "connectors"):
        if key in ctx:
            await ctx[key].aclose()
    if "service" in ctx:
        await ctx["service"].hub.aclose()
    await dispose_engine()


async def _ensure_runner(ctx: dict[str, Any]) -> None:
    """Build the pipeline with the dashboard's AI settings; rebuild when they change."""
    from app.ai_settings import effective_settings

    try:
        effective, sig = await effective_settings(ctx["settings"], ctx["store"])
    except Exception as e:  # noqa: BLE001 - no database yet: run with the environment
        log.warning("AI settings not read: %s", type(e).__name__)
        effective, sig = ctx["settings"], "env"
    if ctx.get("runner") is not None and ctx.get("ai_signature") == sig:
        return
    ctx["runner"] = build_runner(
        effective, ctx["storage"], ctx["service"], get_sessionmaker(), download_models=True
    )
    ctx["ai_signature"] = sig
    log.info(
        "pipeline built (AI settings %s)", "from the dashboard" if sig != "env" else "from the environment"
    )


async def process_document(ctx: dict[str, Any], document_id: str) -> str:
    await _ensure_runner(ctx)
    outcome = await process_document_job(get_sessionmaker(), ctx["runner"], uuid.UUID(document_id))
    if outcome == "approved" and ctx.get("redis") is not None:  # auto-approval → connectors with auto_push
        await ctx["redis"].enqueue_job("connector_push", document_id, _job_id=f"connector:{document_id}")
    return outcome


async def license_refresh_tick(ctx: dict[str, Any]) -> dict[str, int]:
    return await refresh_due_firms(get_sessionmaker(), ctx["service"])


async def revocations_tick(ctx: dict[str, Any]) -> str:
    return await refresh_revocations(get_sessionmaker(), ctx["service"])


async def retention_tick(ctx: dict[str, Any]) -> dict[str, int]:
    return await run_retention(get_sessionmaker(), ctx["storage"])


async def webhooks_tick(ctx: dict[str, Any]) -> dict[str, int]:
    return await deliver_due(get_sessionmaker(), ctx["service"])


async def connector_push(ctx: dict[str, Any], document_id: str) -> dict[str, int]:
    return await auto_push_job(
        get_sessionmaker(), ctx["connectors"], ctx["storage"], ctx["service"], uuid.UUID(document_id)
    )


async def connector_receive_tick(ctx: dict[str, Any]) -> dict[str, int]:
    """Poll the e-invoicing networks (Peppol, KSeF, PDP) for every firm that enabled receiving."""
    from app.jobs.queue import ArqQueue

    queue = ArqQueue(ctx["settings"].redis_url)
    try:
        return await receive_tick(
            get_sessionmaker(), ctx["connectors"], ctx["storage"], ctx["service"], ctx["scanner"], queue
        )
    finally:
        await queue.aclose()


async def bank_feed_tick(ctx: dict[str, Any]) -> dict[str, int]:
    """Pull transactions from the PSD2 aggregator for every linked account, then match."""
    from app.bank.service import sync_feeds

    return await sync_feeds(get_sessionmaker(), ctx["service"])


async def email_poll_tick(ctx: dict[str, Any]) -> dict[str, int]:
    """IMAP polling: the platform mailbox and every firm's own mailbox (dashboard settings, J). Each
    message is one transaction through the channel intake; one failing mailbox never stops the others."""
    from app.jobs.queue import ArqQueue

    settings = ctx["settings"]
    store = ctx["store"]
    mailboxes = await imap_sections(store)
    if not mailboxes:
        return {"handled": 0, "failed": 0}
    queue = ArqQueue(settings.redis_url)
    deps = IntakeDeps(
        settings=settings,
        storage=ctx["storage"],
        scanner=ctx["scanner"],
        service=ctx["service"],
        queue=queue,
        transports=ctx["channels"].transports(),
        store=store,
        mailer=SmtpMailer(store),
    )

    async def handle_raw(raw: bytes) -> str:
        async with get_sessionmaker()() as session:
            async with session.begin():
                return (await handle_inbound(session, deps, parse_mime(raw))).outcome

    totals = {"handled": 0, "failed": 0, "mailboxes": 0}
    try:
        for section in mailboxes:
            try:
                mailbox = await asyncio.to_thread(ImapMailbox.from_section, section)
            except Exception as e:  # noqa: BLE001 - a wrong password must not stop the other mailboxes
                log.warning("imap login failed (%s): %s", section.source, type(e).__name__)
                totals["failed"] += 1
                continue
            counts = await poll_mailbox(mailbox, handle_raw)
            totals["handled"] += counts["handled"]
            totals["failed"] += counts["failed"]
            totals["mailboxes"] += 1
        return totals
    finally:
        await queue.aclose()


async def imap_sections(store: Any) -> list[Any]:
    """The platform mailbox plus every firm's own enabled mailbox."""
    from sqlalchemy import select

    from app.db.rls import set_platform_bypass
    from app.models.config import ConfigEntry
    from app.models.firm import Firm

    out = []
    platform = await store.effective("imap", None)
    if platform.get("host"):
        out.append(platform)
    async with get_sessionmaker()() as session:
        async with session.begin():
            await set_platform_bypass(session)
            firm_ids = (
                (
                    await session.execute(
                        select(ConfigEntry.firm_id)
                        .join(Firm, Firm.id == ConfigEntry.firm_id)
                        .where(ConfigEntry.key == "imap", Firm.kind == "firm", Firm.is_active.is_(True))
                    )
                )
                .scalars()
                .all()
            )
    for fid in firm_ids:
        own = await store.own("imap", fid)
        if own is not None and own.enabled and own.get("host"):
            out.append(own)
    return out


async def held_tick(ctx: dict[str, Any]) -> dict[str, int]:
    """Files a multi-company sender never assigned: quarantine or drop after 24 h."""
    from app.channels.intake import expire_held
    from app.db.rls import set_platform_bypass

    deps = IntakeDeps(
        settings=ctx["settings"],
        storage=ctx["storage"],
        scanner=ctx["scanner"],
        service=ctx["service"],
        queue=None,
        transports={},
        store=ctx["store"],
    )
    async with get_sessionmaker()() as session:
        async with session.begin():
            await set_platform_bypass(session)
            return await expire_held(session, deps)


async def trash_tick(ctx: dict[str, Any]) -> dict[str, int]:
    """Purge trashed documents after the firm's trash retention; expire old trash requests."""
    from app.db.rls import set_platform_bypass
    from app.trash import expire_requests, purge_due

    async with get_sessionmaker()() as session:
        async with session.begin():
            await set_platform_bypass(session)
            return {
                "purged": await purge_due(session, ctx["storage"]),
                "expired": await expire_requests(session),
            }


async def domains_tick(ctx: dict[str, Any]) -> dict[str, int]:
    """Daily DNS re-check of the platform root and every firm's own domain (never switches off)."""
    from app.db.rls import set_platform_bypass
    from app.domains import DnsPythonChecker, check_all

    async with get_sessionmaker()() as session:
        async with session.begin():
            await set_platform_bypass(session)
            return await check_all(session, DnsPythonChecker())


class WorkerSettings:
    functions = [
        process_document,
        license_refresh_tick,
        revocations_tick,
        retention_tick,
        webhooks_tick,
        email_poll_tick,
        domains_tick,
        held_tick,
        trash_tick,
        connector_push,
        connector_receive_tick,
        bank_feed_tick,
    ]
    cron_jobs = [
        cron(license_refresh_tick, second=15, unique=True),
        cron(webhooks_tick, second={5, 35}, unique=True),
        cron(email_poll_tick, second=50, unique=True),
        cron(revocations_tick, minute=7, unique=True),
        cron(retention_tick, hour=3, minute=30, unique=True),
        cron(connector_receive_tick, minute=set(range(2, 60, 5)), unique=True),
        cron(bank_feed_tick, hour=5, minute=10, unique=True),
        cron(domains_tick, hour=4, minute=20, unique=True),
        cron(held_tick, minute={12, 42}, unique=True),
        cron(trash_tick, minute=25, unique=True),
    ]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(os.environ.get("REDIS_URL", "redis://localhost:6379/0"))
    max_jobs = 4
    health_check_interval = 60  # the compose health check runs `arq ... --check` against this key
    job_timeout = 600
