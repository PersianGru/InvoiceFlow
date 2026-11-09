"""The backup queue: jobs go to their own Arq queue, served only by the ``backup``
container, so a large backup never competes with document processing."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any, Protocol

from arq import create_pool
from arq.connections import ArqRedis, RedisSettings

from app.backup.service import Deps, run_backup, run_restore

QUEUE_NAME = "invoiceflow:backup"


class BackupQueue(Protocol):
    async def enqueue_backup(self, backup_id: uuid.UUID) -> None: ...

    async def enqueue_restore(self, restore_id: uuid.UUID) -> None: ...

    async def aclose(self) -> None: ...


class ArqBackupQueue:
    def __init__(self, redis_url: str) -> None:
        self._settings = RedisSettings.from_dsn(redis_url)
        self._pool: ArqRedis | None = None

    async def _get(self) -> ArqRedis:
        if self._pool is None:
            self._pool = await create_pool(self._settings, default_queue_name=QUEUE_NAME)
        return self._pool

    async def enqueue_backup(self, backup_id: uuid.UUID) -> None:
        pool = await self._get()
        await pool.enqueue_job(
            "backup_job", str(backup_id), _job_id=f"backup:{backup_id}", _queue_name=QUEUE_NAME
        )

    async def enqueue_restore(self, restore_id: uuid.UUID) -> None:
        pool = await self._get()
        await pool.enqueue_job(
            "restore_job", str(restore_id), _job_id=f"restore:{restore_id}", _queue_name=QUEUE_NAME
        )

    async def aclose(self) -> None:
        if self._pool is not None:
            await self._pool.aclose()
            self._pool = None


class InlineBackupQueue:
    """Tests and single-process development: the job runs at once."""

    def __init__(self, deps: Deps | Callable[[], Deps]) -> None:
        self._deps = deps
        self.results: list[dict[str, Any]] = []

    @property
    def deps(self) -> Deps:
        return self._deps if isinstance(self._deps, Deps) else self._deps()

    async def enqueue_backup(self, backup_id: uuid.UUID) -> None:
        self.results.append(await run_backup(self.deps, backup_id))

    async def enqueue_restore(self, restore_id: uuid.UUID) -> None:
        self.results.append(await run_restore(self.deps, restore_id))

    async def aclose(self) -> None:
        return None
