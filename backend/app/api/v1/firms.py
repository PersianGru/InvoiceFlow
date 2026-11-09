"""Firm views and settings. Firms are created by the super admin (``/platform/firms``)."""

from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.api.deps import ActorDep, FirmPathDep, SessionDep, require
from app.audit.service import record_audit
from app.db.rls import set_platform_bypass
from app.licensing.installid import format_grouped
from app.models.firm import Firm
from app.tenancy.context import Actor

router = APIRouter(prefix="/firms", tags=["firms"])


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
    await set_platform_bypass(session)
    rows = (
        (await session.execute(select(Firm).where(Firm.kind == "firm").order_by(Firm.created_at)))
        .scalars()
        .all()
    )
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
    await set_platform_bypass(session)
    rows = await session.execute(
        select(Firm.license_state, func.count()).where(Firm.kind == "firm").group_by(Firm.license_state)
    )
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
    trash_retention_days: int | None = Field(default=None, ge=7, le=90)


class FirmSettingsOut(BaseModel):
    confidence_threshold: float
    auto_approve: bool
    ai_routing_policy: str
    cloud_ai_opt_in: bool
    cloud_ai_monthly_cap: int
    retention_days: int
    base_currency: str
    trash_retention_days: int = 30


def _settings_out(f: Firm) -> FirmSettingsOut:
    return FirmSettingsOut(
        confidence_threshold=float(f.confidence_threshold),
        auto_approve=f.auto_approve,
        ai_routing_policy=f.ai_routing_policy,
        cloud_ai_opt_in=f.cloud_ai_opt_in,
        cloud_ai_monthly_cap=f.cloud_ai_monthly_cap,
        retention_days=f.retention_days,
        base_currency=f.base_currency,
        trash_retention_days=int((f.settings or {}).get("trash_retention_days", 30)),
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
        elif name == "trash_retention_days":
            firm.settings = {**(firm.settings or {}), "trash_retention_days": int(value)}
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
