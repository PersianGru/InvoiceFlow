from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.addresses import INTAKE_CHANNELS, new_local_part, regenerate
from app.api.deps import FirmDep, LicenseServiceDep, SessionDep, SettingsDep, require
from app.audit.service import record_audit
from app.config import Settings
from app.db.rls import set_platform_bypass, set_tenant
from app.licensing.gate import check_feature, check_limit
from app.models.client_company import ClientCompany, InboundAddress
from app.tenancy.context import Actor

router = APIRouter(prefix="/client-companies", tags=["client-companies"])


class ClientCompanyIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    country: str = Field(min_length=2, max_length=2, default="DE")
    peppol_id: str | None = Field(default=None, max_length=64)
    tax_number: str | None = Field(default=None, max_length=32)


class ClientCompanyOut(BaseModel):
    id: str
    name: str
    country: str
    inbound_email_local_part: str | None
    inbound_email_address: str | None = None
    email_accept_any_sender: bool = False
    peppol_id: str | None = None
    tax_number: str | None = None
    is_active: bool
    intake_paused: dict[str, bool] = {}
    retired_addresses: list[dict[str, str | None]] = []


class ClientCompanyPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    country: str | None = Field(default=None, min_length=2, max_length=2)
    peppol_id: str | None = Field(default=None, max_length=64)
    tax_number: str | None = Field(default=None, max_length=32)
    email_accept_any_sender: bool | None = None
    is_active: bool | None = None


def out(
    c: ClientCompany, domain: str | None = None, retired: list[InboundAddress] | None = None
) -> ClientCompanyOut:
    paused = (c.settings or {}).get("intake_paused") or {}
    return ClientCompanyOut(
        id=str(c.id),
        name=c.name,
        country=c.country,
        inbound_email_local_part=c.inbound_email_local_part,
        inbound_email_address=(
            f"{c.inbound_email_local_part}@{domain}" if domain and c.inbound_email_local_part else None
        ),
        email_accept_any_sender=bool((c.settings or {}).get("email_accept_any_sender")),
        peppol_id=c.peppol_id,
        tax_number=c.tax_number,
        is_active=c.is_active,
        intake_paused={ch: bool(paused.get(ch)) for ch in INTAKE_CHANNELS},
        retired_addresses=[
            {
                "address": f"{r.local_part}@{domain}" if domain else r.local_part,
                "retired_at": r.retired_at.isoformat() if r.retired_at else None,
                "grace_until": r.grace_until.isoformat() if r.grace_until else None,
                "reason": r.reason,
            }
            for r in (retired or [])
        ],
    )


async def firm_inbound_domain(session: AsyncSession, settings: Settings, firm_id: uuid.UUID) -> str | None:
    """The firm's default inbound domain; the legacy INBOUND_EMAIL_DOMAIN while none is configured."""
    from app.domains import any_domain_configured, default_domain

    d = await default_domain(session, firm_id)
    if d is not None:
        return d.domain
    await set_platform_bypass(session)
    configured = await any_domain_configured(session)
    await set_tenant(session, firm_id)
    return None if configured else settings.inbound_email_domain


@router.get("", response_model=list[ClientCompanyOut])
async def list_clients(
    session: SessionDep, settings: SettingsDep, actor: Actor = Depends(require("clients.view"))
) -> list[ClientCompanyOut]:
    rows = (await session.execute(select(ClientCompany).order_by(ClientCompany.name))).scalars().all()
    if not rows:
        return []
    domain = await firm_inbound_domain(session, settings, rows[0].firm_id)
    retired = (
        (await session.execute(select(InboundAddress).order_by(InboundAddress.retired_at.desc())))
        .scalars()
        .all()
    )
    return [out(c, domain, [r for r in retired if r.client_company_id == c.id]) for c in rows]


@router.patch("/{client_id}", response_model=ClientCompanyOut)
async def update_client(
    client_id: uuid.UUID,
    body: ClientCompanyPatch,
    session: SessionDep,
    firm: FirmDep,
    settings: SettingsDep,
    actor: Actor = Depends(require("clients.manage")),
) -> ClientCompanyOut:
    c = await session.get(ClientCompany, client_id)
    if c is None:
        raise HTTPException(404, {"error": "not_found", "message": "Client company not found."})
    if body.name is not None:
        c.name = body.name.strip()
    if body.country is not None:
        c.country = body.country.upper()
    if body.is_active is not None:
        c.is_active = body.is_active
    if body.peppol_id is not None:
        c.peppol_id = body.peppol_id.strip() or None
    if body.tax_number is not None:
        c.tax_number = "".join(ch for ch in body.tax_number if ch.isalnum()) or None
    if body.email_accept_any_sender is not None:
        c.settings = {**(c.settings or {}), "email_accept_any_sender": body.email_accept_any_sender}
    await record_audit(
        session,
        firm_id=firm.id,
        actor=actor,
        action="client_company.update",
        target_type="client_company",
        target_id=str(c.id),
        after=body.model_dump(exclude_none=True),
    )
    return out(c, await firm_inbound_domain(session, settings, firm.id))


@router.post("", response_model=ClientCompanyOut, status_code=201)
async def create_client(
    body: ClientCompanyIn,
    session: SessionDep,
    firm: FirmDep,
    svc: LicenseServiceDep,
    settings: SettingsDep,
    actor: Actor = Depends(require("clients.manage")),
) -> ClientCompanyOut:
    snap = check_feature(svc.snapshot(firm), "core")
    current = (
        await session.execute(
            select(func.count()).select_from(ClientCompany).where(ClientCompany.is_active.is_(True))
        )
    ).scalar_one()
    check_limit(snap, "max_client_companies", int(current))
    c = ClientCompany(
        firm_id=firm.id,
        name=body.name.strip(),
        country=body.country.upper(),
        inbound_email_local_part=await _new_local(session, firm.id, body.name),
        peppol_id=(body.peppol_id or "").strip() or None,
        tax_number="".join(ch for ch in (body.tax_number or "") if ch.isalnum()) or None,
    )
    session.add(c)
    await session.flush()
    await record_audit(
        session,
        firm_id=firm.id,
        actor=actor,
        action="client_company.create",
        target_type="client_company",
        target_id=str(c.id),
        after={"name": c.name},
    )
    return out(c, await firm_inbound_domain(session, settings, firm.id))


async def _new_local(session: AsyncSession, firm_id: uuid.UUID, name: str) -> str:
    await set_platform_bypass(session)
    local = await new_local_part(session, name)
    await set_tenant(session, firm_id)
    return local


class RegenerateIn(BaseModel):
    reason: str = Field(min_length=3, max_length=500)
    grace_days: int = Field(default=0, ge=0, le=14)


@router.post("/{client_id}/inbound-address/regenerate", response_model=ClientCompanyOut)
async def regenerate_address(
    client_id: uuid.UUID,
    body: RegenerateIn,
    session: SessionDep,
    firm: FirmDep,
    settings: SettingsDep,
    actor: Actor = Depends(require("clients.manage")),
) -> ClientCompanyOut:
    """A new address for the client company; all its data stays. The old address is never reissued and
    stops receiving (or, during the grace period, accepts known senders only, flagged)."""
    c = await session.get(ClientCompany, client_id)
    if c is None:
        raise HTTPException(404, {"error": "not_found", "message": "Client company not found."})
    await set_platform_bypass(session)
    old, new = await regenerate(session, c, body.reason, body.grace_days, actor.user_id)
    await set_tenant(session, firm.id)
    await record_audit(
        session,
        firm_id=firm.id,
        actor=actor,
        action="client_company.inbound_address_regenerate",
        target_type="client_company",
        target_id=str(c.id),
        before={"local_part": old},
        after={"local_part": new, "reason": body.reason, "grace_days": body.grace_days},
    )
    retired = (
        (await session.execute(select(InboundAddress).where(InboundAddress.client_company_id == c.id)))
        .scalars()
        .all()
    )
    return out(c, await firm_inbound_domain(session, settings, firm.id), list(retired))


class PauseIn(BaseModel):
    channels: dict[str, bool]
    reason: str = Field(min_length=3, max_length=500)


@router.post("/{client_id}/intake-pause", response_model=ClientCompanyOut)
async def pause_intake(
    client_id: uuid.UUID,
    body: PauseIn,
    session: SessionDep,
    firm: FirmDep,
    settings: SettingsDep,
    actor: Actor = Depends(require("intake.pause")),
) -> ClientCompanyOut:
    unknown = [ch for ch in body.channels if ch not in INTAKE_CHANNELS]
    if unknown:
        raise HTTPException(
            422, {"error": "invalid_request", "message": f"Unknown channel(s): {', '.join(unknown)}"}
        )
    c = await session.get(ClientCompany, client_id)
    if c is None:
        raise HTTPException(404, {"error": "not_found", "message": "Client company not found."})
    before = dict((c.settings or {}).get("intake_paused") or {})
    paused = {**before, **body.channels}
    c.settings = {**(c.settings or {}), "intake_paused": paused}
    await record_audit(
        session,
        firm_id=firm.id,
        actor=actor,
        action="client_company.intake_pause",
        target_type="client_company",
        target_id=str(c.id),
        before={"intake_paused": before},
        after={"intake_paused": paused, "reason": body.reason},
    )
    return out(c, await firm_inbound_domain(session, settings, firm.id))
