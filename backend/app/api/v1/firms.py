"""Firm sign-up (SaaS onboarding) and firm views."""

from __future__ import annotations

import uuid
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, select

from app.api.deps import ActorDep, FirmPathDep, LicenseServiceDep, SessionDep, SettingsDep, require
from app.audit.service import record_audit
from app.auth.passwords import WeakPasswordError, check_policy, hash_password
from app.db.rls import set_platform_bypass
from app.licensing.installid import format_grouped
from app.licensing.instance import new_instance
from app.licensing.store import new_firm_data_key
from app.models.firm import Firm
from app.models.user import Role, User
from app.tenancy.context import Actor

router = APIRouter(prefix="/firms", tags=["firms"])


class SignupIn(BaseModel):
    firm_name: str = Field(min_length=2, max_length=200)
    country: str = Field(min_length=2, max_length=2, default="DE")
    owner_email: EmailStr
    owner_name: str = Field(min_length=1, max_length=200, default="")
    password: str = Field(min_length=1, max_length=1024)
    locale: str = Field(default="en", max_length=8)


class FirmOut(BaseModel):
    id: str
    name: str
    country: str
    locale: str
    installation_id: str
    installation_id_grouped: str
    license_state: str
    created_at: str


def firm_out(f: Firm) -> FirmOut:
    return FirmOut(
        id=str(f.id),
        name=f.name,
        country=f.country,
        locale=f.locale,
        installation_id=f.instance_id,
        installation_id_grouped=format_grouped(f.instance_id),
        license_state=f.license_state,
        created_at=f.created_at.isoformat() if f.created_at else "",
    )


@router.post("", response_model=FirmOut, status_code=201)
async def signup(
    body: SignupIn, request: Request, session: SessionDep, settings: SettingsDep, svc: LicenseServiceDep
) -> FirmOut:
    try:
        check_policy(body.password, settings.password_min_length)
    except WeakPasswordError as e:
        raise HTTPException(422, {"error": "weak_password", "message": str(e)}) from e
    await set_platform_bypass(session)
    exists = (await session.execute(select(User.id).where(User.email == body.owner_email.lower()))).first()
    if exists:
        raise HTTPException(
            409, {"error": "email_taken", "message": "An account with this email already exists."}
        )
    now = svc.clock()
    iid, fp, source = new_instance(settings, now)
    firm = Firm(
        id=uuid.uuid4(), name=body.firm_name.strip(), country=body.country.upper(), locale=body.locale
    )
    firm.data_key_wrapped = new_firm_data_key(request.app.state.master_key, str(firm.id))
    firm.instance_id, firm.instance_fp, firm.instance_fp_source = iid, fp, source
    firm.last_seen = now
    session.add(firm)
    await session.flush()
    owner = User(
        firm_id=firm.id,
        email=body.owner_email.lower(),
        display_name=body.owner_name or body.owner_email.split("@")[0],
        password_hash=hash_password(body.password),
        role=Role.firm_owner,
        locale=body.locale,
    )
    session.add(owner)
    await session.flush()
    await record_audit(
        session,
        firm_id=firm.id,
        actor=Actor.from_user(owner),
        action="firm.create",
        target_type="firm",
        target_id=str(firm.id),
        after={"name": firm.name, "installation_id": firm.instance_id},
    )
    await session.refresh(firm)
    return firm_out(firm)


@router.get("/{firm_id}", response_model=FirmOut)
async def get_firm(firm: FirmPathDep) -> FirmOut:
    return firm_out(firm)


class FirmAdminRow(BaseModel):
    id: str
    name: str
    license_state: str
    key_hint: str | None
    next_refresh_at: int | None
    hub_error: str | None


@router.get("", response_model=list[FirmAdminRow])
async def list_firms(
    session: SessionDep, actor: Actor = Depends(require("platform.admin"))
) -> list[FirmAdminRow]:
    rows = (await session.execute(select(Firm).order_by(Firm.created_at))).scalars().all()
    return [
        FirmAdminRow(
            id=str(f.id),
            name=f.name,
            license_state=f.license_state,
            key_hint=f.key_hint,
            next_refresh_at=f.next_refresh_at,
            hub_error=f.hub_error,
        )
        for f in rows
    ]


@router.get("/admin/license-states")
async def license_states(
    session: SessionDep, actor: Actor = Depends(require("platform.admin"))
) -> dict[str, int]:
    rows = await session.execute(select(Firm.license_state, func.count()).group_by(Firm.license_state))
    return {state: int(n) for state, n in rows.all()}


@router.get("/me/current", response_model=FirmOut)
async def my_firm(actor: ActorDep, session: SessionDep) -> FirmOut:
    if actor.firm_id is None:
        raise HTTPException(404, {"error": "no_firm", "message": "This account belongs to no firm."})
    firm = await session.get(Firm, actor.firm_id)
    if firm is None:
        raise HTTPException(404, {"error": "no_firm", "message": "Firm not found."})
    return firm_out(firm)


class FirmSettingsIn(BaseModel):
    confidence_threshold: float | None = Field(default=None, ge=0.5, le=1.0)
    auto_approve: bool | None = None
    ai_routing_policy: str | None = Field(
        default=None, pattern=r"^(local_only|local_first_cloud_fallback|cloud_first)$"
    )
    cloud_ai_opt_in: bool | None = None
    cloud_ai_monthly_cap: int | None = Field(default=None, ge=0, le=1_000_000)
    retention_days: int | None = Field(default=None, ge=365, le=36500)
    base_currency: str | None = Field(default=None, min_length=3, max_length=3)


class FirmSettingsOut(BaseModel):
    confidence_threshold: float
    auto_approve: bool
    ai_routing_policy: str
    cloud_ai_opt_in: bool
    cloud_ai_monthly_cap: int
    retention_days: int
    base_currency: str


def _settings_out(f: Firm) -> FirmSettingsOut:
    return FirmSettingsOut(
        confidence_threshold=float(f.confidence_threshold),
        auto_approve=f.auto_approve,
        ai_routing_policy=f.ai_routing_policy,
        cloud_ai_opt_in=f.cloud_ai_opt_in,
        cloud_ai_monthly_cap=f.cloud_ai_monthly_cap,
        retention_days=f.retention_days,
        base_currency=f.base_currency,
    )


@router.get("/{firm_id}/settings", response_model=FirmSettingsOut)
async def get_settings_(firm: FirmPathDep, actor: Actor = Depends(require("firm.manage"))) -> FirmSettingsOut:
    return _settings_out(firm)


@router.patch("/{firm_id}/settings", response_model=FirmSettingsOut)
async def patch_settings(
    body: FirmSettingsIn,
    firm: FirmPathDep,
    session: SessionDep,
    actor: Actor = Depends(require("firm.manage")),
) -> FirmSettingsOut:
    before = _settings_out(firm).model_dump()
    for name, value in body.model_dump(exclude_none=True).items():
        if name == "confidence_threshold":
            firm.confidence_threshold = Decimal(str(round(value, 3)))
        elif name == "base_currency":
            firm.base_currency = str(value).upper()
        else:
            setattr(firm, name, value)
    await record_audit(
        session,
        firm_id=firm.id,
        actor=actor,
        action="firm.settings",
        target_type="firm",
        target_id=str(firm.id),
        before=before,
        after=_settings_out(firm).model_dump(),
    )
    return _settings_out(firm)
