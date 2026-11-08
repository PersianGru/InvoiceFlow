from __future__ import annotations

import secrets
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.api.deps import FirmDep, LicenseServiceDep, SessionDep, SettingsDep, require
from app.audit.service import record_audit
from app.licensing.gate import check_feature, check_limit
from app.models.client_company import ClientCompany
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


class ClientCompanyPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    country: str | None = Field(default=None, min_length=2, max_length=2)
    peppol_id: str | None = Field(default=None, max_length=64)
    tax_number: str | None = Field(default=None, max_length=32)
    email_accept_any_sender: bool | None = None
    is_active: bool | None = None


def out(c: ClientCompany, domain: str | None = None) -> ClientCompanyOut:
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
    )


def _slug(name: str) -> str:
    base = "".join(ch for ch in name.lower() if ch.isalnum())[:12] or "client"
    return f"{base}.{secrets.token_hex(2)}"


@router.get("", response_model=list[ClientCompanyOut])
async def list_clients(
    session: SessionDep, settings: SettingsDep, actor: Actor = Depends(require("clients.view"))
) -> list[ClientCompanyOut]:
    rows = (await session.execute(select(ClientCompany).order_by(ClientCompany.name))).scalars().all()
    return [out(c, settings.inbound_email_domain) for c in rows]


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
    return out(c, settings.inbound_email_domain)


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
        inbound_email_local_part=_slug(body.name),
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
    return out(c, settings.inbound_email_domain)
