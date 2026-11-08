"""The processing job as the worker (and the inline test queue) runs it."""

from __future__ import annotations

import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.rls import set_platform_bypass, set_tenant
from app.models.document import DocumentStatus
from app.pipeline.runner import ProcessingRunner, load_document_for_processing

log = logging.getLogger("invoiceflow.pipeline.jobs")


async def process_document_job(
    sessionmaker: async_sessionmaker[AsyncSession], runner: ProcessingRunner, document_id: uuid.UUID
) -> str:
    async with sessionmaker() as session:
        async with session.begin():
            await set_platform_bypass(session)
            found = await load_document_for_processing(session, document_id)
            if found is None:
                return "missing"
            doc, firm = found
            await set_tenant(session, firm.id)
            try:
                outcome = await runner.process(session, doc, firm)
            except Exception as e:  # noqa: BLE001 - a crash must leave a readable trace on the document
                log.exception("processing failed for document %s: %s", document_id, type(e).__name__)
                doc.status, doc.error = DocumentStatus.failed, f"internal:{type(e).__name__}"[:500]
                return "failed"
            log.info(
                "document %s processed: status=%s review=%s",
                document_id,
                outcome.status.value,
                outcome.review_status.value,
            )
            return outcome.status.value
