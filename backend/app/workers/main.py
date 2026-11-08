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
from app.main import build_license_service
from app.pipeline.factory import build_runner, build_scanner
from app.pipeline.jobs import process_document_job
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
    ctx["runner"] = build_runner(
        settings, ctx["storage"], ctx["service"], get_sessionmaker(), download_models=True
    )
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


async def process_document(ctx: dict[str, Any], document_id: str) -> str:
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
    """IMAP polling when configured; each message is one transaction through the channel intake."""
    settings = ctx["settings"]
    if not settings.imap_host:
        return {"handled": 0, "failed": 0}
    from app.jobs.queue import ArqQueue

    queue = ArqQueue(settings.redis_url)
    deps = IntakeDeps(
        settings=settings,
        storage=ctx["storage"],
        scanner=ctx["scanner"],
        service=ctx["service"],
        queue=queue,
        transports=ctx["channels"].transports(),
    )

    async def handle_raw(raw: bytes) -> str:
        async with get_sessionmaker()() as session:
            async with session.begin():
                return (await handle_inbound(session, deps, parse_mime(raw))).outcome

    try:
        mailbox = await asyncio.to_thread(ImapMailbox, settings)
        return await poll_mailbox(mailbox, handle_raw)
    finally:
        await queue.aclose()


class WorkerSettings:
    functions = [
        process_document,
        license_refresh_tick,
        revocations_tick,
        retention_tick,
        webhooks_tick,
        email_poll_tick,
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
    ]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(os.environ.get("REDIS_URL", "redis://localhost:6379/0"))
    max_jobs = 4
    health_check_interval = 60  # the compose health check runs `arq ... --check` against this key
    job_timeout = 600
