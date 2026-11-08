"""Background refresh (spec §8.3) and the weekly revocation list (spec §10).

* ``refresh_due_firms``: every firm whose ``next_refresh_at`` has passed (or that has a key and no
  token, e.g. after ``instance_blocked``), one at a time, under a **PostgreSQL transaction-level
  advisory lock per firm**, so several replicas never refresh the same firm at once.
* ``refresh_revocations``: fetch the list when the cached one is older than 7 days (global lock),
  verify it, cache it, apply it to every firm.
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy import or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.rls import set_platform_bypass
from app.licensing.client import HubUnreachableError
from app.licensing.constants import DAY, REVOCATION_FETCH_INTERVAL
from app.licensing.service import LicenseService
from app.licensing.verifier import verify_revocation_list
from app.models.firm import Firm
from app.models.license import RevocationListCache

log = logging.getLogger("invoiceflow.licensing.refresh")

_LOCK_NS = 0x1C21  # advisory lock namespace for licensing
_REVOCATION_LOCK_ID = 1


def _firm_lock_key(firm_id: uuid.UUID) -> int:
    return int.from_bytes(firm_id.bytes[:4], "big") & 0x7FFFFFFF


async def try_firm_lock(session: AsyncSession, firm_id: uuid.UUID) -> bool:
    row = await session.execute(
        text("SELECT pg_try_advisory_xact_lock(:ns, :key)"), {"ns": _LOCK_NS, "key": _firm_lock_key(firm_id)}
    )
    return bool(row.scalar())


async def due_firm_ids(session: AsyncSession, now: int, startup: bool = False) -> list[uuid.UUID]:
    await set_platform_bypass(session)
    conds = [Firm.next_refresh_at <= now]
    if startup:
        # on start: tokens older than 24 h (spec §8.3)
        conds.append((Firm.license_token_enc.is_not(None)) & (Firm.last_refresh_at <= now - DAY))
    stmt = select(Firm.id).where(Firm.is_active.is_(True), or_(*conds))
    return list((await session.execute(stmt)).scalars().all())


async def refresh_one(
    sessionmaker: async_sessionmaker[AsyncSession],
    service: LicenseService,
    firm_id: uuid.UUID,
    *,
    reason: str,
) -> str:
    """Refresh one firm under its advisory lock. Returns the outcome code ('locked' when another
    replica holds the firm)."""
    async with sessionmaker() as session:
        async with session.begin():
            await set_platform_bypass(session)
            if not await try_firm_lock(session, firm_id):
                return "locked"
            firm = await session.get(Firm, firm_id, with_for_update=True)
            if firm is None:
                return "missing"
            outcome = await service.refresh(session, firm, reason=reason)
            return outcome.code


async def refresh_due_firms(
    sessionmaker: async_sessionmaker[AsyncSession], service: LicenseService, *, startup: bool = False
) -> dict[str, int]:
    now = service.clock()
    async with sessionmaker() as session:
        async with session.begin():
            ids = await due_firm_ids(session, now, startup=startup)
    counts: dict[str, int] = {}
    for fid in ids:
        code = await refresh_one(sessionmaker, service, fid, reason="startup" if startup else "scheduled")
        counts[code] = counts.get(code, 0) + 1
    if counts:
        log.info("license refresh tick: %s", counts)
    return counts


async def refresh_revocations(
    sessionmaker: async_sessionmaker[AsyncSession], service: LicenseService, *, force: bool = False
) -> str:
    """Fetch (weekly), verify and apply the revocation list. Returns a short outcome code."""
    now = service.clock()
    async with sessionmaker() as session:
        async with session.begin():
            await set_platform_bypass(session)
            locked = (
                await session.execute(
                    text("SELECT pg_try_advisory_xact_lock(:ns, :key)"),
                    {"ns": _LOCK_NS, "key": _REVOCATION_LOCK_ID},
                )
            ).scalar()
            if not locked:
                return "locked"
            cached = await session.get(RevocationListCache, 1)
            if cached is not None and not force and now - cached.fetched_at < REVOCATION_FETCH_INTERVAL:
                return "fresh"
            try:
                jws = await service.hub.get_revocations()
            except HubUnreachableError as e:
                log.warning("revocation list fetch failed: %s", e.kind)
                return "network"
            r = verify_revocation_list(
                jws,
                product=service.settings.product_id,
                keys=service.keyring.keys,
                now=now,
                sub=None,
                act=None,
            )
            if not r.ok and r.reason == "unknown_kid":
                await service.keyring.refresh_from_hub()
                r = verify_revocation_list(
                    jws,
                    product=service.settings.product_id,
                    keys=service.keyring.keys,
                    now=now,
                    sub=None,
                    act=None,
                )
            if not r.ok:
                log.warning("revocation list rejected: %s", r.reason)
                return f"rejected_{r.reason}"
            exp = int(r.claims.get("exp", now))
            if cached is None:
                session.add(RevocationListCache(id=1, jws=jws, fetched_at=now, list_exp=exp))
            else:
                cached.jws, cached.fetched_at, cached.list_exp = jws, now, exp
            firm_ids = list(
                (await session.execute(select(Firm.id).where(Firm.license_token_enc.is_not(None)))).scalars()
            )
    hits = 0
    for fid in firm_ids:
        async with sessionmaker() as session:
            async with session.begin():
                await set_platform_bypass(session)
                if not await try_firm_lock(session, fid):
                    continue
                firm = await session.get(Firm, fid, with_for_update=True)
                if firm is not None and await service.apply_revocation_list(session, firm, jws):
                    hits += 1
    log.info("revocation list applied: %d firm(s) stopped", hits)
    return "ok"
