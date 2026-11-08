"""Supplier profiles: matched by VAT id, then IBAN, then normalised name.
Each processed document updates the profile's history (count, amounts, IBANs, address) which the
validation cross-checks use; corrections add field rules and few-shot examples."""

from __future__ import annotations

import re
import unicodedata
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.pipeline import SupplierProfile
from app.schemas.invoice import Invoice

_LEGAL_FORMS = re.compile(
    r"\b(gmbh|ag|kg|ohg|ug|e\.?k\.?|sa|sas|sarl|sàrl|eurl|sl|slu|srl|spa|s\.?p\.?a\.?|bv|b\.?v\.?|nv"
    r"|ltd|limited|llc|inc|plc|sp\.? z o\.?o\.?|s\.?a\.?|oy|ab|as|aps)\b\.?"
)


def normalise_name(name: str | None) -> str:
    if not name:
        return ""
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    s = _LEGAL_FORMS.sub(" ", s)
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return " ".join(s.split())[:200]


@dataclass(slots=True)
class SupplierHistory:
    profile_id: str | None
    document_count: int
    known_ibans: list[str] = field(default_factory=list)
    average_gross: Decimal | None = None
    last_address: dict[str, Any] = field(default_factory=dict)
    examples: list[dict[str, Any]] = field(default_factory=list)
    field_rules: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def empty(cls) -> SupplierHistory:
        return cls(None, 0)


async def find_profile(session: AsyncSession, firm_id: uuid.UUID, invoice: Invoice) -> SupplierProfile | None:
    vat = (invoice.supplier.vat_id or "").upper()
    if vat:
        p = (
            (
                await session.execute(
                    select(SupplierProfile).where(
                        SupplierProfile.firm_id == firm_id, SupplierProfile.vat_id == vat
                    )
                )
            )
            .scalars()
            .first()
        )
        if p is not None:
            return p
    iban = (invoice.supplier.iban or "").upper()
    if iban:
        rows = (
            (await session.execute(select(SupplierProfile).where(SupplierProfile.firm_id == firm_id)))
            .scalars()
            .all()
        )
        for p in rows:
            if iban in (p.ibans or []):
                return p
    norm = normalise_name(invoice.supplier.name)
    if norm:
        p = (
            (
                await session.execute(
                    select(SupplierProfile).where(
                        SupplierProfile.firm_id == firm_id, SupplierProfile.name_normalized == norm
                    )
                )
            )
            .scalars()
            .first()
        )
        if p is not None:
            return p
    return None


def history_of(profile: SupplierProfile | None) -> SupplierHistory:
    if profile is None:
        return SupplierHistory.empty()
    stats = profile.stats or {}
    avg = Decimal(str(stats["average_gross"])) if stats.get("average_gross") is not None else None
    return SupplierHistory(
        str(profile.id),
        profile.document_count,
        list(profile.ibans or []),
        avg,
        dict(profile.address or {}),
        list(profile.examples or [])[-3:],
        dict(profile.field_rules or {}),
    )


async def upsert_profile(
    session: AsyncSession,
    firm_id: uuid.UUID,
    client_company_id: uuid.UUID | None,
    invoice: Invoice,
    profile: SupplierProfile | None,
) -> SupplierProfile | None:
    """Create or update the supplier profile from a processed document (after validation ran)."""
    name = invoice.supplier.name
    if not name and not invoice.supplier.vat_id:
        return None
    if profile is None:
        profile = SupplierProfile(
            firm_id=firm_id,
            client_company_id=client_company_id,
            name=(name or invoice.supplier.vat_id or "")[:200],
            name_normalized=normalise_name(name) or (invoice.supplier.vat_id or "").lower(),
            vat_id=(invoice.supplier.vat_id or None),
            country=invoice.supplier.address.country if invoice.supplier.address else None,
        )
        session.add(profile)
    if invoice.supplier.vat_id and not profile.vat_id:
        profile.vat_id = invoice.supplier.vat_id.upper()
    ibans = list(profile.ibans or [])
    if invoice.supplier.iban and invoice.supplier.iban.upper() not in ibans:
        ibans.append(invoice.supplier.iban.upper())
        profile.ibans = ibans[-5:]
    if invoice.supplier.address:
        addr = {k: v for k, v in invoice.supplier.address.model_dump().items() if v}
        if addr:
            profile.address = addr
    stats = dict(profile.stats or {})
    gross = invoice.totals.gross_total
    if gross is not None:
        n = int(stats.get("n_gross", 0))
        avg = Decimal(str(stats.get("average_gross", "0")))
        stats["average_gross"] = str(((avg * n) + gross) / (n + 1))
        stats["n_gross"] = n + 1
        stats["max_gross"] = str(max(gross, Decimal(str(stats.get("max_gross", "0")))))
    stats["last_seen"] = datetime.now(UTC).isoformat()
    if invoice.currency:
        stats["currency"] = invoice.currency
    profile.stats = stats
    profile.document_count = (profile.document_count or 0) + 1
    profile.updated_at = datetime.now(UTC)
    await session.flush()
    return profile
