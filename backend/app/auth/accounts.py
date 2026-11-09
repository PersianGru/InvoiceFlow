"""Account building blocks: one-time links, recovery codes, the row whose data
key protects a user's 2FA secret, and the web links put into emails.

Link and recovery tokens are random, shown once and stored only as SHA-256. They never appear in a log
or an audit entry, and links carry the token in the URL fragment so it reaches no server log."""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.models.account import UserLink
from app.models.firm import Firm
from app.models.user import User, UserSession

INVITE_TTL = timedelta(hours=24)
RESET_TTL = timedelta(minutes=15)
RECOVERY_CODES = 10
NO_PASSWORD = "!invited"  # noqa: S105 - never a valid argon2 hash: the account has no usable password yet


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def web_base_url(settings: Settings) -> str:
    """Where people open the web app (links in emails): the first CORS origin, else PUBLIC_URL."""
    first = settings.cors_origins.split(",")[0].strip().rstrip("/")
    return first or settings.public_url.rstrip("/")


def link_url(settings: Settings, kind: str, token: str) -> str:
    page = "set-password" if kind == "invite" else "reset-password"
    return f"{web_base_url(settings)}/{page}#t={token}"


async def create_link(
    session: AsyncSession, user: User, kind: str, created_by: uuid.UUID | None
) -> tuple[str, UserLink]:
    """A new link of ``kind``; earlier unused links of the same kind stop working."""
    now = datetime.now(UTC)
    await session.execute(
        update(UserLink)
        .where(UserLink.user_id == user.id, UserLink.kind == kind, UserLink.used_at.is_(None))
        .values(used_at=now)
    )
    token = secrets.token_urlsafe(32)
    row = UserLink(
        firm_id=user.firm_id,
        user_id=user.id,
        kind=kind,
        token_hash=_hash(token),
        expires_at=now + (INVITE_TTL if kind == "invite" else RESET_TTL),
        created_by_user_id=created_by,
    )
    session.add(row)
    await session.flush()
    return token, row


async def find_link(session: AsyncSession, token: str) -> tuple[UserLink, User] | None:
    """Caller must have the platform bypass set. Expired, used or unknown → None."""
    if not token or len(token) > 128:
        return None
    row = (
        await session.execute(select(UserLink).where(UserLink.token_hash == _hash(token)))
    ).scalar_one_or_none()
    if row is None or row.used_at is not None or row.expires_at <= datetime.now(UTC):
        return None
    user = await session.get(User, row.user_id)
    if user is None or user.deleted_at is not None or not user.is_active:
        return None
    return row, user


async def revoke_sessions(session: AsyncSession, user_id: uuid.UUID, keep: uuid.UUID | None = None) -> None:
    stmt = update(UserSession).where(UserSession.user_id == user_id, UserSession.revoked_at.is_(None))
    if keep is not None:
        stmt = stmt.where(UserSession.id != keep)
    await session.execute(stmt.values(revoked_at=datetime.now(UTC)))


def new_recovery_codes() -> tuple[list[str], list[str]]:
    """(codes to show once, hashes to store). Codes look like ``a1b2c-3d4e5``."""
    alphabet = "abcdefghjkmnpqrstuvwxyz23456789"
    codes = []
    for _ in range(RECOVERY_CODES):
        raw = "".join(secrets.choice(alphabet) for _ in range(10))
        codes.append(f"{raw[:5]}-{raw[5:]}")
    return codes, [_hash(c.replace("-", "")) for c in codes]


def use_recovery_code(user: User, code: str) -> bool:
    """Consume one recovery code (constant work per stored hash)."""
    norm = (code or "").strip().lower().replace("-", "").replace(" ", "")
    if len(norm) != 10:
        return False
    h = _hash(norm)
    stored = list(user.recovery_codes or [])
    match = None
    for s in stored:
        if secrets.compare_digest(s, h):
            match = s
    if match is None:
        return False
    stored.remove(match)
    user.recovery_codes = stored
    return True


async def secret_holder(session: AsyncSession, user: User) -> Firm | None:
    """The row whose data key seals the user's 2FA secret: the user's firm, or the platform row for
    the super admin. Caller has the platform bypass or the user's tenant set."""
    if user.firm_id is not None:
        return await session.get(Firm, user.firm_id)
    return (await session.execute(select(Firm).where(Firm.kind == "platform"))).scalar_one_or_none()


def placeholder_label(user_id: uuid.UUID) -> str:
    return f"Deleted user #{user_id.hex[:4]}"
