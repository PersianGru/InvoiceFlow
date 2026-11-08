"""``Idempotency-Key`` support for the creating endpoints.

Usage in a route::

    idem = await idempotency_begin(request, session, firm_id)
    if idem.replay is not None:
        return idem.replay
    ... do the work, build ``payload`` (a JSON-able dict) ...
    await idempotency_store(session, idem, status_code, payload)

The stored response is replayed for 24 h under the same firm + key; a different request fingerprint
under the same key is refused with ``422 idempotency_mismatch``.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.integration import IdempotencyKey

TTL = timedelta(hours=24)
HEADER = "idempotency-key"


@dataclass(slots=True)
class Idempotency:
    key: str | None
    fingerprint: str
    replay: JSONResponse | None = None


async def idempotency_begin(
    request: Request, session: AsyncSession, firm_id: uuid.UUID, body: bytes | None = None
) -> Idempotency:
    key = request.headers.get(HEADER)
    if body is None:
        body = await request.body()
    fp = hashlib.sha256(
        f"{request.method} {request.url.path}\n".encode() + hashlib.sha256(body).digest()
    ).hexdigest()
    if not key:
        return Idempotency(None, fp)
    if len(key) > 200:
        raise HTTPException(422, {"error": "invalid_request", "message": "Idempotency-Key is too long."})
    await session.execute(delete(IdempotencyKey).where(IdempotencyKey.created_at < datetime.now(UTC) - TTL))
    row = (
        await session.execute(
            select(IdempotencyKey).where(IdempotencyKey.firm_id == firm_id, IdempotencyKey.key == key)
        )
    ).scalar_one_or_none()
    if row is None:
        return Idempotency(key, fp)
    if row.fingerprint != fp:
        raise HTTPException(
            422,
            {
                "error": "idempotency_mismatch",
                "message": "This Idempotency-Key was already used with a different request.",
            },
        )
    return Idempotency(
        key,
        fp,
        JSONResponse(
            status_code=row.status_code, content=row.response, headers={"idempotent-replayed": "true"}
        ),
    )


async def idempotency_store(
    session: AsyncSession, idem: Idempotency, firm_id: uuid.UUID, status_code: int, payload: dict[str, Any]
) -> None:
    if idem.key is None:
        return
    session.add(
        IdempotencyKey(
            firm_id=firm_id,
            key=idem.key,
            fingerprint=idem.fingerprint,
            status_code=status_code,
            response=payload,
        )
    )
    await session.flush()
