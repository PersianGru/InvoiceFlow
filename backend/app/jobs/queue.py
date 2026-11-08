"""Job queue abstraction: the API enqueues, the worker runs. Arq (Redis) in deployments; an inline
queue in tests runs the job at once in-process."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from arq import create_pool
from arq.connections import ArqRedis, RedisSettings

log = logging.getLogger("invoiceflow.jobs.queue")


class JobQueue(Protocol):
    async def enqueue_process_document(self, document_id: uuid.UUID) -> None: ...

    async def kick_webhooks(self) -> None: ...

    async def enqueue_connector_push(self, document_id: uuid.UUID) -> None: ...

    async def aclose(self) -> None: ...


class ArqQueue:
    def __init__(self, redis_url: str) -> None:
        self._settings = RedisSettings.from_dsn(redis_url)
        self._pool: ArqRedis | None = None

    async def _get(self) -> ArqRedis:
        if self._pool is None:
            self._pool = await create_pool(self._settings)
        return self._pool

    async def enqueue_process_document(self, document_id: uuid.UUID) -> None:
        pool = await self._get()
        await pool.enqueue_job("process_document", str(document_id), _job_id=f"doc:{document_id}")
        log.info("queued processing for document %s", document_id)

    async def kick_webhooks(self) -> None:
        """Run the deliverer soon (the cron tick is the safety net). The job id de-duplicates kicks."""
        pool = await self._get()
        await pool.enqueue_job("webhooks_tick", _job_id="webhooks:kick")

    async def enqueue_connector_push(self, document_id: uuid.UUID) -> None:
        pool = await self._get()
        await pool.enqueue_job("connector_push", str(document_id), _job_id=f"connector:{document_id}")

    async def aclose(self) -> None:
        if self._pool is not None:
            await self._pool.aclose()
            self._pool = None


class InlineQueue:
    """Runs the handler immediately (tests, single-process development)."""

    def __init__(
        self,
        handler: Callable[[uuid.UUID], Awaitable[Any]] | None = None,
        webhook_handler: Callable[[], Awaitable[Any]] | None = None,
        connector_handler: Callable[[uuid.UUID], Awaitable[Any]] | None = None,
    ) -> None:
        self.handler = handler
        self.webhook_handler = webhook_handler
        self.connector_handler = connector_handler
        self.connector_pushes: list[uuid.UUID] = []
        self.enqueued: list[uuid.UUID] = []
        self.webhook_kicks = 0

    async def enqueue_process_document(self, document_id: uuid.UUID) -> None:
        self.enqueued.append(document_id)
        if self.handler is not None:
            await self.handler(document_id)

    async def kick_webhooks(self) -> None:
        self.webhook_kicks += 1
        if self.webhook_handler is not None:
            await self.webhook_handler()

    async def enqueue_connector_push(self, document_id: uuid.UUID) -> None:
        self.connector_pushes.append(document_id)
        if self.connector_handler is not None:
            await self.connector_handler(document_id)

    async def aclose(self) -> None:
        return None
