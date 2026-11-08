from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select

from app.api.deps import SessionDep, require
from app.models.audit import AuditLog
from app.tenancy.context import Actor

router = APIRouter(prefix="/audit", tags=["audit"])


class AuditRow(BaseModel):
    id: int
    actor_user_id: str | None
    actor_role: str | None
    action: str
    target_type: str
    target_id: str | None
    result: str
    before: dict[str, Any] | None
    after: dict[str, Any] | None
    created_at: str


@router.get("", response_model=list[AuditRow])
async def list_audit(
    session: SessionDep,
    actor: Actor = Depends(require("audit.view")),
    limit: int = 200,
    action: str | None = None,
) -> list[AuditRow]:
    stmt = select(AuditLog).order_by(AuditLog.id.desc()).limit(min(limit, 1000))
    if action:
        stmt = stmt.where(AuditLog.action == action)
    rows = (await session.execute(stmt)).scalars().all()
    return [
        AuditRow(
            id=r.id,
            actor_user_id=str(r.actor_user_id) if r.actor_user_id else None,
            actor_role=r.actor_role,
            action=r.action,
            target_type=r.target_type,
            target_id=r.target_id,
            result=r.result,
            before=r.before,
            after=r.after,
            created_at=r.created_at.isoformat() if r.created_at else "",
        )
        for r in rows
    ]
