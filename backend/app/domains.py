"""Inbound email domains.

* The platform admin verifies the **root domain** the installation runs under (TXT record
  ``_invoiceflow.<root>`` = ``ivf-verify=<token>``).
* Every firm gets a **platform subdomain** ``<label>.<root>`` (random 4 characters, renamable while
  free). It needs no record of its own: it is as verified as the root.
* A firm may verify **its own domain** the same way; once verified it becomes the firm's default.
* Domains are re-checked daily. A record that disappears turns the domain to ``warning``; mail keeps
  flowing (a failed check never switches anything off). Retired domains keep receiving from known
  senders until their grace period ends.
* Installations without any domain keep the legacy ``INBOUND_EMAIL_DOMAIN`` for every firm.
"""

from __future__ import annotations

import logging
import re
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.domain import InboundDomain
from app.models.firm import Firm

log = logging.getLogger("invoiceflow.domains")

TXT_PREFIX = "_invoiceflow"
TXT_VALUE = "ivf-verify="
LABEL_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
DOMAIN_RE = re.compile(r"^(?=.{4,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")
RESERVED_LABELS = frozenset({"www", "mail", "smtp", "imap", "mx", "_invoiceflow", "api", "store"})
MAX_GRACE_DAYS = 14


class DomainError(ValueError):
    pass


class DnsChecker(Protocol):
    async def txt(self, name: str) -> list[str]: ...

    async def mx(self, name: str) -> list[str]: ...


class DnsPythonChecker:
    """Looks records up with the system resolver (dnspython). Errors read as "not found"."""

    async def txt(self, name: str) -> list[str]:
        import dns.asyncresolver
        import dns.exception

        try:
            answer = await dns.asyncresolver.resolve(name, "TXT", lifetime=8)
        except (dns.exception.DNSException, OSError):
            return []
        out = []
        for rdata in answer:
            out.append(b"".join(getattr(rdata, "strings", [])).decode("utf-8", "replace"))
        return out

    async def mx(self, name: str) -> list[str]:
        import dns.asyncresolver
        import dns.exception

        try:
            answer = await dns.asyncresolver.resolve(name, "MX", lifetime=8)
        except (dns.exception.DNSException, OSError):
            return []
        return [str(getattr(r, "exchange", "")).rstrip(".") for r in answer]


def normalise_domain(value: str) -> str:
    d = (value or "").strip().lower().rstrip(".")
    if d.startswith(("http://", "https://")) or "@" in d or "/" in d:
        raise DomainError("Enter a domain name only, for example in.example.com.")
    try:
        d = d.encode("idna").decode("ascii")
    except UnicodeError as e:
        raise DomainError("This is not a valid domain name.") from e
    if not DOMAIN_RE.match(d):
        raise DomainError("This is not a valid domain name.")
    return d


def normalise_label(value: str) -> str:
    label = (value or "").strip().lower()
    if not LABEL_RE.match(label) or label in RESERVED_LABELS:
        raise DomainError("Use 1-63 letters, digits or dashes (not at the start or end).")
    return label


def txt_name(domain: str) -> str:
    return f"{TXT_PREFIX}.{domain}"


def txt_value(token: str) -> str:
    return f"{TXT_VALUE}{token}"


def _token() -> str:
    return secrets.token_urlsafe(18)


def _random_label() -> str:
    alphabet = "abcdefghjkmnpqrstuvwxyz23456789"
    return "".join(secrets.choice(alphabet) for _ in range(4))


async def root_domain(session: AsyncSession) -> InboundDomain | None:
    """Caller has the platform bypass."""
    return (
        await session.execute(
            select(InboundDomain).where(InboundDomain.kind == "root", InboundDomain.retired_at.is_(None))
        )
    ).scalar_one_or_none()


async def firm_domains(
    session: AsyncSession, firm_id: uuid.UUID, *, include_retired: bool = False
) -> list[InboundDomain]:
    stmt = select(InboundDomain).where(InboundDomain.firm_id == firm_id, InboundDomain.kind != "root")
    if not include_retired:
        stmt = stmt.where(InboundDomain.retired_at.is_(None))
    return list((await session.execute(stmt.order_by(InboundDomain.created_at))).scalars().all())


async def default_domain(session: AsyncSession, firm_id: uuid.UUID) -> InboundDomain | None:
    for d in await firm_domains(session, firm_id):
        if d.is_default and d.usable:
            return d
    for d in await firm_domains(session, firm_id):
        if d.kind == "platform_sub":
            return d
    return None


async def _taken(session: AsyncSession, domain: str) -> bool:
    row = (await session.execute(select(InboundDomain.id).where(InboundDomain.domain == domain))).first()
    return row is not None


def _retire(d: InboundDomain, grace_days: int) -> None:
    now = datetime.now(UTC)
    d.retired_at = now
    d.is_default = False
    d.grace_until = now + timedelta(days=max(0, min(grace_days, MAX_GRACE_DAYS))) if grace_days > 0 else None


async def set_root(session: AsyncSession, platform: Firm, domain: str, grace_days: int = 0) -> InboundDomain:
    """Set (or change) the platform root; every firm's subdomain moves to the new root."""
    domain = normalise_domain(domain)
    current = await root_domain(session)
    if current is not None and current.domain == domain:
        return current
    if await _taken(session, domain):
        raise DomainError("This domain is already in use.")
    root = InboundDomain(firm_id=platform.id, kind="root", domain=domain, verification_token=_token())
    session.add(root)
    if current is not None:
        _retire(current, 0)
    await session.flush()
    subs = (
        (
            await session.execute(
                select(InboundDomain).where(
                    InboundDomain.kind == "platform_sub", InboundDomain.retired_at.is_(None)
                )
            )
        )
        .scalars()
        .all()
    )
    for sub in subs:
        new = InboundDomain(
            firm_id=sub.firm_id,
            kind="platform_sub",
            label=sub.label,
            domain=f"{sub.label}.{domain}",
            verification_token=_token(),
            status=root.status,
            verified_at=root.verified_at,
            is_default=sub.is_default,
        )
        _retire(sub, grace_days)
        session.add(new)
    await session.flush()
    firms = (await session.execute(select(Firm).where(Firm.kind == "firm"))).scalars().all()
    for firm in firms:
        await assign_platform_subdomain(session, firm)
    return root


async def assign_platform_subdomain(session: AsyncSession, firm: Firm) -> InboundDomain | None:
    """The firm's ``<random4>.<root>`` (made once; nothing to do without a root)."""
    root = await root_domain(session)
    if root is None or firm.kind != "firm":
        return None
    existing = [d for d in await firm_domains(session, firm.id) if d.kind == "platform_sub"]
    if existing:
        return existing[0]
    for _ in range(20):
        label = _random_label()
        if not await _taken(session, f"{label}.{root.domain}"):
            break
    else:  # pragma: no cover - 31^4 labels
        raise DomainError("No free subdomain found.")
    has_default = any(d.is_default for d in await firm_domains(session, firm.id))
    sub = InboundDomain(
        firm_id=firm.id,
        kind="platform_sub",
        label=label,
        domain=f"{label}.{root.domain}",
        verification_token=_token(),
        status=root.status,
        verified_at=root.verified_at,
        is_default=not has_default,
    )
    session.add(sub)
    await session.flush()
    return sub


async def rename_subdomain(session: AsyncSession, firm: Firm, label: str, grace_days: int) -> InboundDomain:
    root = await root_domain(session)
    if root is None:
        raise DomainError("The platform has no root domain yet.")
    label = normalise_label(label)
    domain = f"{label}.{root.domain}"
    current = next((d for d in await firm_domains(session, firm.id) if d.kind == "platform_sub"), None)
    if current is not None and current.domain == domain:
        return current
    if await _taken(session, domain):
        raise DomainError("This name is already taken.")
    new = InboundDomain(
        firm_id=firm.id,
        kind="platform_sub",
        label=label,
        domain=domain,
        verification_token=_token(),
        status=root.status,
        verified_at=root.verified_at,
        is_default=current.is_default if current is not None else True,
    )
    if current is not None:
        _retire(current, grace_days)
    session.add(new)
    await session.flush()
    return new


async def set_custom(session: AsyncSession, firm: Firm, domain: str, grace_days: int) -> InboundDomain:
    domain = normalise_domain(domain)
    root = await root_domain(session)
    if root is not None and (domain == root.domain or domain.endswith("." + root.domain)):
        raise DomainError("Use the platform subdomain setting for names under the platform domain.")
    current = next((d for d in await firm_domains(session, firm.id) if d.kind == "custom"), None)
    if current is not None and current.domain == domain:
        return current
    if await _taken(session, domain):
        raise DomainError("This domain is already in use.")
    new = InboundDomain(firm_id=firm.id, kind="custom", domain=domain, verification_token=_token())
    if current is not None:
        was_default = current.is_default
        _retire(current, grace_days)
        if was_default:
            sub = next((d for d in await firm_domains(session, firm.id) if d.kind == "platform_sub"), None)
            if sub is not None:
                sub.is_default = True
    session.add(new)
    await session.flush()
    return new


async def remove_custom(session: AsyncSession, firm: Firm, grace_days: int) -> bool:
    current = next((d for d in await firm_domains(session, firm.id) if d.kind == "custom"), None)
    if current is None:
        return False
    _retire(current, grace_days)
    sub = next((d for d in await firm_domains(session, firm.id) if d.kind == "platform_sub"), None)
    if sub is not None:
        sub.is_default = True
    await session.flush()
    return True


async def check_domain(session: AsyncSession, d: InboundDomain, dns: DnsChecker) -> InboundDomain:
    """TXT check (root, custom) and MX lookup. Never switches a verified domain off."""
    now = datetime.now(UTC)
    d.last_check_at = now
    mx = await dns.mx(d.domain)
    d.mx_hosts = ", ".join(mx)[:500] if mx else None
    if d.kind == "platform_sub":
        root = await root_domain(session)
        d.status = root.status if root is not None else "pending"
        d.verified_at = root.verified_at if root is not None else None
        d.last_check_error = None
        return d
    records = await dns.txt(txt_name(d.domain))
    if any(r.strip() == txt_value(d.verification_token) for r in records):
        d.status = "verified"
        d.verified_at = d.verified_at or now
        d.last_check_error = None
        if d.kind == "custom" and d.retired_at is None:
            for other in await firm_domains(session, d.firm_id):
                other.is_default = other.id == d.id
        if d.kind == "root":
            subs = (
                (
                    await session.execute(
                        select(InboundDomain).where(
                            InboundDomain.kind == "platform_sub", InboundDomain.retired_at.is_(None)
                        )
                    )
                )
                .scalars()
                .all()
            )
            for sub in subs:
                sub.status, sub.verified_at = "verified", sub.verified_at or now
    else:
        d.last_check_error = "txt_record_missing"
        d.status = "warning" if d.verified_at is not None else "pending"
        if d.kind == "root" and d.verified_at is not None:
            subs = (
                (
                    await session.execute(
                        select(InboundDomain).where(
                            InboundDomain.kind == "platform_sub", InboundDomain.retired_at.is_(None)
                        )
                    )
                )
                .scalars()
                .all()
            )
            for sub in subs:
                sub.status = "warning"
    await session.flush()
    return d


async def find_domain(session: AsyncSession, domain: str) -> InboundDomain | None:
    """A recipient domain: an active, usable domain, or a retired one still inside its grace period."""
    domain = (domain or "").strip().lower().rstrip(".")
    rows = (
        (await session.execute(select(InboundDomain).where(InboundDomain.domain == domain))).scalars().all()
    )
    now = datetime.now(UTC)
    for d in rows:
        if d.retired_at is None and d.usable and d.kind != "root":
            return d
    for d in rows:
        if (
            d.retired_at is not None
            and d.grace_until is not None
            and d.grace_until > now
            and d.kind != "root"
        ):
            return d
    return None


async def any_domain_configured(session: AsyncSession) -> bool:
    return (await session.execute(select(InboundDomain.id).limit(1))).first() is not None


async def firm_domains_summary(session: AsyncSession) -> dict[uuid.UUID, str]:
    rows = (
        (
            await session.execute(
                select(InboundDomain).where(InboundDomain.kind != "root", InboundDomain.retired_at.is_(None))
            )
        )
        .scalars()
        .all()
    )
    out: dict[uuid.UUID, str] = {}
    for d in rows:
        if d.is_default or d.firm_id not in out:
            out[d.firm_id] = d.domain
    return out


async def check_all(session: AsyncSession, dns: DnsChecker) -> dict[str, int]:
    """Daily: the root first, then every firm's own domain."""
    counts = {"checked": 0, "verified": 0}
    root = await root_domain(session)
    if root is not None:
        await check_domain(session, root, dns)
        counts["checked"] += 1
        counts["verified"] += int(root.status == "verified")
    rows = (
        (
            await session.execute(
                select(InboundDomain).where(
                    InboundDomain.kind == "custom", InboundDomain.retired_at.is_(None)
                )
            )
        )
        .scalars()
        .all()
    )
    for d in rows:
        await check_domain(session, d, dns)
        counts["checked"] += 1
        counts["verified"] += int(d.status == "verified")
    return counts


def domain_view(d: InboundDomain) -> dict[str, object]:
    return {
        "id": str(d.id),
        "kind": d.kind,
        "domain": d.domain,
        "label": d.label,
        "status": d.status,
        "is_default": d.is_default,
        "txt_name": txt_name(d.domain),
        "txt_value": txt_value(d.verification_token) if d.kind != "platform_sub" else None,
        "mx_hosts": d.mx_hosts,
        "verified_at": d.verified_at.isoformat() if d.verified_at else None,
        "last_check_at": d.last_check_at.isoformat() if d.last_check_at else None,
        "last_check_error": d.last_check_error,
        "retired_at": d.retired_at.isoformat() if d.retired_at else None,
        "grace_until": d.grace_until.isoformat() if d.grace_until else None,
    }
