"""``/healthz``: liveness plus aggregate license counts per state (never per-firm data)."""

from __future__ import annotations

from fastapi import APIRouter, Request
from sqlalchemy import func, select, text

from app.db.rls import set_platform_bypass
from app.db.session import session_scope
from app.licensing.constants import LICENSE_SPEC
from app.models.firm import Firm

router = APIRouter(tags=["health"])


@router.get("/healthz", include_in_schema=False)
async def healthz(request: Request) -> dict[str, object]:
    settings = request.app.state.settings
    counts: dict[str, int] = {}
    db_ok = True
    try:
        async with session_scope() as session:
            await set_platform_bypass(session)
            await session.execute(text("SELECT 1"))
            rows = await session.execute(
                select(Firm.license_state, func.count()).group_by(Firm.license_state)
            )
            counts = {state: int(n) for state, n in rows.all()}
    except Exception:  # noqa: BLE001
        db_ok = False
    return {
        "status": "ok" if db_ok else "degraded",
        "version": settings.app_version,
        "license_spec": LICENSE_SPEC,
        "firms_by_license_state": counts,
    }
