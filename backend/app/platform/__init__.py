"""The platform: first-run setup, the platform row, the platform license (number of firms)."""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.rls import set_platform_bypass
from app.licensing.instance import new_instance
from app.licensing.state import LicenseSnapshot
from app.licensing.store import new_firm_data_key
from app.models.account import PlatformState
from app.models.firm import Firm
from app.models.user import Role, User
from app.security.crypto import MasterKey

log = logging.getLogger("invoiceflow.platform")

PLATFORM_TOOL = "platform"
DEFAULT_MAX_FIRMS = 1


def setup_code_for(settings: Settings, nonce: str) -> str:
    """Every API process derives the same code from SECRET_KEY and the stored nonce."""
    digest = hmac.new(settings.secret_key.encode(), f"setup:{nonce}".encode(), hashlib.sha256).hexdigest()
    raw = digest[:12].upper()
    return f"{raw[:4]}-{raw[4:8]}-{raw[8:]}"


async def super_admin_exists(session: AsyncSession) -> bool:
    await set_platform_bypass(session)
    row = (
        await session.execute(
            select(User.id).where(User.role == Role.super_admin, User.deleted_at.is_(None)).limit(1)
        )
    ).first()
    return row is not None


async def platform_state(session: AsyncSession) -> PlatformState:
    await set_platform_bypass(session)
    state = await session.get(PlatformState, 1)
    if state is None:
        state = PlatformState(id=1)
        session.add(state)
        await session.flush()
    return state


async def ensure_setup_code(session: AsyncSession, settings: Settings) -> str | None:
    """While no super admin exists: the setup code (made once, logged by the caller). Else None."""
    if await super_admin_exists(session):
        return None
    state = await platform_state(session)
    if not state.setup_nonce:
        state.setup_nonce = secrets.token_hex(16)
        await session.flush()
    return setup_code_for(settings, state.setup_nonce)


async def check_setup_code(session: AsyncSession, settings: Settings, code: str) -> bool:
    state = await platform_state(session)
    if not state.setup_nonce:
        return False
    expected = setup_code_for(settings, state.setup_nonce)
    given = (code or "").strip().upper().replace(" ", "")
    if "-" not in given and len(given) == 12:
        given = f"{given[:4]}-{given[4:8]}-{given[8:]}"
    return hmac.compare_digest(expected, given)


async def get_platform(session: AsyncSession) -> Firm | None:
    """Caller has the platform bypass set."""
    return (await session.execute(select(Firm).where(Firm.kind == "platform"))).scalar_one_or_none()


async def ensure_platform(session: AsyncSession, settings: Settings, master: MasterKey, now: int) -> Firm:
    """The platform row (made once, at setup): data key and its own sealed installation id."""
    await set_platform_bypass(session)
    existing = await get_platform(session)
    if existing is not None:
        return existing
    iid, fp, source = new_instance(settings, now)
    row = Firm(id=uuid.uuid4(), name="Platform", kind="platform", country="DE", locale="en")
    row.data_key_wrapped = new_firm_data_key(master, str(row.id))
    row.instance_id, row.instance_fp, row.instance_fp_source = iid, fp, source
    row.last_seen = now
    session.add(row)
    await session.flush()
    state = await platform_state(session)
    state.platform_firm_id = row.id
    return row


async def create_firm_record(
    session: AsyncSession,
    settings: Settings,
    master: MasterKey,
    now: int,
    name: str,
    country: str = "DE",
    locale: str = "en",
) -> Firm:
    """A new firm row: own data key and own sealed installation id (spec §7.1)."""
    await set_platform_bypass(session)
    iid, fp, source = new_instance(settings, now)
    firm = Firm(id=uuid.uuid4(), name=name.strip(), country=country.upper(), locale=locale, kind="firm")
    firm.data_key_wrapped = new_firm_data_key(master, str(firm.id))
    firm.instance_id, firm.instance_fp, firm.instance_fp_source = iid, fp, source
    firm.last_seen = now
    session.add(firm)
    await session.flush()
    return firm


async def complete_setup(session: AsyncSession) -> None:
    state = await platform_state(session)
    state.setup_nonce = None
    state.setup_completed_at = datetime.now(UTC)


def max_firms(snapshot: LicenseSnapshot | None) -> int:
    """Entitlement from the verified platform token only: ``platform`` on → ``max_firms`` (0 or missing
    means unlimited per the limit convention), anything else → 1."""
    if snapshot is None or not snapshot.core_on:
        return DEFAULT_MAX_FIRMS
    limit = snapshot.limit("max_firms")
    return limit if limit > 0 else 1_000_000


async def firm_count(session: AsyncSession) -> int:
    """Firms that count toward ``max_firms``: not the platform row, not deleted."""
    await set_platform_bypass(session)
    n = (
        await session.execute(
            select(func.count()).select_from(Firm).where(Firm.kind == "firm", Firm.deleted_at.is_(None))
        )
    ).scalar_one()
    return int(n)
