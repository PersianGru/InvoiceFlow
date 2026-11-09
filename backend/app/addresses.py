"""Per-client inbound addresses: ``<name>.<8 random>@<firm domain>``.

A regenerated address keeps all of the client company's data. The old local part is kept in
``inbound_addresses`` forever, so it is never handed out again; during an optional grace period (at
most 14 days) it still accepts mail from known senders, flagged."""

from __future__ import annotations

import secrets
import unicodedata
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.client_company import ClientCompany, InboundAddress

MAX_GRACE_DAYS = 14
INTAKE_CHANNELS = ("email", "telegram", "whatsapp", "upload", "api")


def name_slug(name: str) -> str:
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    return "".join(ch for ch in ascii_name.lower() if ch.isalnum())[:12] or "client"


async def _taken(session: AsyncSession, local: str) -> bool:
    """Caller has the platform bypass (local parts are unique across the installation)."""
    current = select(ClientCompany.id).where(ClientCompany.inbound_email_local_part == local)
    if (await session.execute(current)).first():
        return True
    retired = select(InboundAddress.id).where(InboundAddress.local_part == local)
    return (await session.execute(retired)).first() is not None


async def new_local_part(session: AsyncSession, name: str) -> str:
    base = name_slug(name)
    for _ in range(20):
        candidate = f"{base}.{secrets.token_hex(4)}"
        if not await _taken(session, candidate):
            return candidate
    raise RuntimeError("no free inbound address")  # pragma: no cover - 2^32 per name


async def regenerate(
    session: AsyncSession, client: ClientCompany, reason: str, grace_days: int, user_id: uuid.UUID | None
) -> tuple[str | None, str]:
    """(old, new) local part. The old one goes to the history (never reissued)."""
    now = datetime.now(UTC)
    old = client.inbound_email_local_part
    new = await new_local_part(session, client.name)
    if old:
        grace = max(0, min(grace_days, MAX_GRACE_DAYS))
        session.add(
            InboundAddress(
                firm_id=client.firm_id,
                client_company_id=client.id,
                local_part=old,
                retired_at=now,
                grace_until=now + timedelta(days=grace) if grace else None,
                reason=reason[:500],
                retired_by_user_id=user_id,
            )
        )
    client.inbound_email_local_part = new
    await session.flush()
    return old, new


async def retired_address(session: AsyncSession, local: str) -> InboundAddress | None:
    stmt = select(InboundAddress).where(InboundAddress.local_part == local)
    return (await session.execute(stmt)).scalar_one_or_none()


def intake_paused(client: ClientCompany, channel: str) -> bool:
    paused = (client.settings or {}).get("intake_paused") or {}
    return bool(paused.get(channel))
