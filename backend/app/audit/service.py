"""Audit trail: who, what, when, before/after, result. Append-only (RLS and grants deny updates).

Values pass through the log redaction so a license key or token can never land in the audit log.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.logging_setup import redact_value
from app.models.audit import AuditLog
from app.tenancy.context import Actor


async def record_audit(
    session: AsyncSession,
    *,
    firm_id: uuid.UUID | None,
    actor: Actor | None,
    action: str,
    target_type: str,
    target_id: str | None = None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    result: str = "ok",
) -> AuditLog:
    row = AuditLog(
        firm_id=firm_id,
        actor_user_id=actor.user_id if actor else None,
        actor_role=actor.role.value if actor else "system",
        action=action,
        target_type=target_type,
        target_id=target_id,
        before=redact_value(before) if before is not None else None,
        after=redact_value(after) if after is not None else None,
        result=result,
        ip=actor.ip if actor else None,
    )
    session.add(row)
    await session.flush()
    return row
