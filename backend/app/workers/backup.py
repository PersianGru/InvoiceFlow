"""The backup worker: ``arq app.workers.backup.WorkerSettings`` in the ``backup``
container. One job at a time, its own queue, low priority; the compose file limits CPU and memory."""

from __future__ import annotations

import logging
import os
import uuid
from typing import Any

from arq import cron
from arq.connections import RedisSettings

from app.backup.queue import QUEUE_NAME
from app.backup.service import Deps, run_backup, run_restore, schedule_due
from app.config import get_settings
from app.db.session import dispose_engine, get_sessionmaker, init_engine
from app.logging_setup import configure_logging
from app.security.crypto import MasterKey
from app.settings_store import SettingsStore
from app.storage import build_storage

log = logging.getLogger("invoiceflow.backup.worker")


async def startup(ctx: dict[str, Any]) -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    init_engine(settings.database_url)
    nice = getattr(os, "nice", None)  # POSIX only
    if nice is not None:
        try:
            nice(10)  # low CPU priority next to the API and the document worker
        except OSError:
            pass
    master = MasterKey(settings.master_key_bytes())
    ctx["deps"] = Deps(get_sessionmaker(), build_storage(settings), SettingsStore(master), master, settings)


async def shutdown(ctx: dict[str, Any]) -> None:
    await dispose_engine()


async def backup_job(ctx: dict[str, Any], backup_id: str) -> dict[str, Any]:
    return await run_backup(ctx["deps"], uuid.UUID(backup_id))


async def restore_job(ctx: dict[str, Any], restore_id: str) -> dict[str, Any]:
    return await run_restore(ctx["deps"], uuid.UUID(restore_id))


async def schedule_tick(ctx: dict[str, Any]) -> dict[str, int]:
    created = await schedule_due(ctx["deps"])
    for bid in created:
        await run_backup(ctx["deps"], bid)
    return {"backups": len(created)}


class WorkerSettings:
    functions = [backup_job, restore_job, schedule_tick]
    cron_jobs = [cron(schedule_tick, minute=5, unique=True)]
    on_startup = startup
    on_shutdown = shutdown
    queue_name = QUEUE_NAME
    redis_settings = RedisSettings.from_dsn(os.environ.get("REDIS_URL", "redis://localhost:6379/0"))
    max_jobs = 1
    job_timeout = 6 * 3600
    health_check_interval = 60
    health_check_key = f"{QUEUE_NAME}:health-check"
