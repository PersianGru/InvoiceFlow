"""First-run setup and the platform admin: overview, firms, switching into a firm."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import LicenseServiceDep, MailerDep, SessionDep, SettingsDep, StoreDep, require
from app.api.v1.auth import UserOut, invite_mail_text, set_session_cookie, user_out
from app.audit.service import record_audit
from app.auth.accounts import NO_PASSWORD, create_link, link_url
from app.auth.passwords import WeakPasswordError, check_policy, hash_password
from app.auth.sessions import create_session_row
from app.db.rls import set_platform_bypass, set_tenant
from app.licensing.installid import format_grouped
from app.licensing.service import LicenseError
from app.mail import OutgoingMail
from app.models.firm import Firm
from app.models.user import Role, User
from app.platform import (
    check_setup_code,
    complete_setup,
    create_firm_record,
    ensure_platform,
    firm_count,
    get_platform,
    max_firms,
    super_admin_exists,
)
from app.settings_store import import_environment
from app.tenancy.context import Actor

log = logging.getLogger("invoiceflow.platform")

router = APIRouter(tags=["platform"])


# ---- first run -------------------------------------------------------------------------------------
class SetupStatus(BaseModel):
    setup_required: bool


@router.get("/setup/status", response_model=SetupStatus)
async def setup_status(session: SessionDep) -> SetupStatus:
    return SetupStatus(setup_required=not await super_admin_exists(session))


class SetupIn(BaseModel):
    setup_code: str = Field(min_length=12, max_length=20)
    email: EmailStr
    display_name: str = Field(default="", max_length=200)
    password: str = Field(min_length=1, max_length=1024)


@router.post("/setup", response_model=UserOut, status_code=201)
async def setup(
    body: SetupIn,
    request: Request,
    response: Response,
    session: SessionDep,
    settings: SettingsDep,
    svc: LicenseServiceDep,
    store: StoreDep,
) -> UserOut:
    """Creates the platform and its super admin. Works once; afterwards there is no sign-up."""
    ip = request.client.host if request.client else "unknown"
    decision = request.app.state.rate_limiter.check(f"setup-ip:{ip}", 10)
    if not decision.allowed:
        raise HTTPException(
            429, {"error": "rate_limited", "message": "Too many attempts. Try again in a minute."}
        )
    if await super_admin_exists(session):
        raise HTTPException(409, {"error": "setup_done", "message": "This installation is already set up."})
    if not await check_setup_code(session, settings, body.setup_code):
        await record_audit(
            session,
            firm_id=None,
            actor=None,
            action="platform.setup",
            target_type="platform",
            result="bad_code",
        )
        raise HTTPException(403, {"error": "setup_code_invalid", "message": "The setup code is wrong."})
    try:
        check_policy(body.password, settings.password_min_length)
    except WeakPasswordError as e:
        raise HTTPException(422, {"error": "weak_password", "message": str(e)}) from e
    taken = (await session.execute(select(User.id).where(User.email == body.email.lower()))).first()
    if taken:
        raise HTTPException(
            409, {"error": "email_taken", "message": "An account with this email already exists."}
        )
    platform = await ensure_platform(session, settings, request.app.state.master_key, svc.clock())
    admin = User(
        firm_id=None,
        email=body.email.lower(),
        display_name=body.display_name or body.email.split("@")[0],
        password_hash=hash_password(body.password),
        password_set_at=datetime.now(UTC),
        role=Role.super_admin,
    )
    session.add(admin)
    await session.flush()
    await complete_setup(session)
    imported = await import_environment(store, session, platform, settings)
    await record_audit(
        session,
        firm_id=None,
        actor=Actor.from_user(admin),
        action="platform.setup",
        target_type="platform",
        target_id=str(platform.id),
        after={"super_admin": str(admin.id), "imported_from_environment": imported},
    )
    token, row = await create_session_row(session, admin, settings.session_ttl_seconds, mfa_passed=False)
    set_session_cookie(response, settings, token)
    log.info("platform set up; the setup code is no longer valid")
    return user_out(admin, row, settings)


# ---- overview --------------------------------------------------------------------------------------
class FirmRow(BaseModel):
    id: str
    name: str
    country: str
    is_active: bool
    deleted_at: str | None
    license_state: str
    key_hint: str | None
    hub_error: str | None
    users: int
    client_companies: int
    created_at: str
    installation_id_grouped: str
    last_backup_at: str | None = None
    inbound_domain: str | None = None


class Overview(BaseModel):
    firms: list[FirmRow]
    firm_count: int
    max_firms: int
    platform_license_state: str
    platform_firm_id: str
    health: dict[str, Any]


async def _firm_rows(session: Any) -> list[FirmRow]:
    from app.models.client_company import ClientCompany

    users = dict(
        (
            await session.execute(
                select(User.firm_id, func.count()).where(User.deleted_at.is_(None)).group_by(User.firm_id)
            )
        ).all()
    )
    clients = dict(
        (
            await session.execute(select(ClientCompany.firm_id, func.count()).group_by(ClientCompany.firm_id))
        ).all()
    )
    extras = await _row_extras(session)
    firms = (
        (await session.execute(select(Firm).where(Firm.kind == "firm").order_by(Firm.created_at)))
        .scalars()
        .all()
    )
    return [
        FirmRow(
            id=str(f.id),
            name=f.name,
            country=f.country,
            is_active=f.is_active,
            deleted_at=f.deleted_at.isoformat() if f.deleted_at else None,
            license_state=f.license_state,
            key_hint=f.key_hint,
            hub_error=f.hub_error,
            users=int(users.get(f.id, 0)),
            client_companies=int(clients.get(f.id, 0)),
            created_at=f.created_at.isoformat() if f.created_at else "",
            installation_id_grouped=format_grouped(f.instance_id),
            **extras.get(f.id, {}),
        )
        for f in firms
    ]


async def _row_extras(session: Any) -> dict[uuid.UUID, dict[str, Any]]:
    """Per-firm extras: the default inbound domain and the last backup."""
    from app.backup.service import last_backups
    from app.domains import firm_domains_summary

    out: dict[uuid.UUID, dict[str, Any]] = {}
    for fid, dom in (await firm_domains_summary(session)).items():
        out.setdefault(fid, {})["inbound_domain"] = dom
    for fid, at in (await last_backups(session)).items():
        out.setdefault(fid, {})["last_backup_at"] = at
    return out


@router.get("/platform/overview", response_model=Overview)
async def overview(
    request: Request,
    session: SessionDep,
    svc: LicenseServiceDep,
    store: StoreDep,
    actor: Actor = Depends(require("platform.admin")),
) -> Overview:
    await set_platform_bypass(session)
    platform = await get_platform(session)
    if platform is None:
        raise HTTPException(409, {"error": "no_platform", "message": "The platform is not set up yet."})
    snap = svc.snapshot(platform)
    smtp = await store.effective("smtp", None)
    imap = await store.effective("imap", None)
    health: dict[str, Any] = {
        "database": "ok",
        "smtp": "configured" if smtp.get("host") else "not_configured",
        "imap": "configured" if imap.get("host") else "not_configured",
        "master_key": "set",
        "mfa_enforced": request.app.state.settings.mfa_enforced,
    }
    return Overview(
        firms=await _firm_rows(session),
        firm_count=await firm_count(session),
        max_firms=max_firms(snap),
        platform_license_state=snap.state,
        platform_firm_id=str(platform.id),
        health=health,
    )


# ---- firms -----------------------------------------------------------------------------------------
class FirmCreateIn(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    country: str = Field(min_length=2, max_length=2, default="DE")
    locale: str = Field(default="en", max_length=8)
    owner_email: EmailStr
    owner_name: str = Field(default="", max_length=200)
    license_key: str | None = Field(default=None, max_length=200)
    send_email: bool = True


class FirmCreated(BaseModel):
    firm: FirmRow
    owner: UserOut
    invite_link: str
    email_sent: bool


@router.post("/platform/firms", response_model=FirmCreated, status_code=201)
async def create_firm(
    body: FirmCreateIn,
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    svc: LicenseServiceDep,
    mailer: MailerDep,
    actor: Actor = Depends(require("platform.admin")),
) -> FirmCreated:
    """The first firm may start without a key; every further firm needs a valid license key, checked with
    the hub before the firm exists, and the platform license must allow one more firm."""
    await set_platform_bypass(session)
    platform = await get_platform(session)
    if platform is None:
        raise HTTPException(409, {"error": "no_platform", "message": "The platform is not set up yet."})
    count = await firm_count(session)
    limit = max_firms(svc.snapshot(platform))
    if count >= limit:
        await record_audit(
            session,
            firm_id=None,
            actor=actor,
            action="platform.firm_create",
            target_type="platform",
            result="max_firms_reached",
        )
        raise HTTPException(
            402,
            {
                "error": "max_firms_reached",
                "message": (
                    f"This platform allows {limit} firm(s). More firms need a platform license from 21CEL "
                    "(on request)."
                ),
                "allowed": limit,
                "current": count,
            },
        )
    if count >= 1 and not (body.license_key or "").strip():
        raise HTTPException(
            422,
            {
                "error": "license_key_required",
                "message": "Every firm after the first needs its own license key.",
            },
        )
    taken = (await session.execute(select(User.id).where(User.email == body.owner_email.lower()))).first()
    if taken:
        raise HTTPException(
            409, {"error": "email_taken", "message": "An account with this email already exists."}
        )
    now = svc.clock()
    try:
        async with session.begin_nested():
            firm = await create_firm_record(
                session, settings, request.app.state.master_key, now, body.name, body.country, body.locale
            )
            if body.license_key and body.license_key.strip():
                await svc.activate(session, firm, body.license_key, actor)
    except LicenseError as e:
        await set_platform_bypass(session)
        await record_audit(
            session,
            firm_id=None,
            actor=actor,
            action="platform.firm_create",
            target_type="platform",
            result=f"license:{e.code}",
        )
        raise
    await set_tenant(session, firm.id)
    owner = User(
        firm_id=firm.id,
        email=body.owner_email.lower(),
        display_name=body.owner_name or body.owner_email.split("@")[0],
        password_hash=NO_PASSWORD,
        role=Role.firm_owner,
        locale=body.locale,
    )
    session.add(owner)
    await session.flush()
    token, _link = await create_link(session, owner, "invite", actor.user_id)
    url = link_url(settings, "invite", token)
    sent = False
    if body.send_email:
        sent = await mailer.send(
            firm.id,
            OutgoingMail(
                owner.email, f"Your InvoiceFlow access ({firm.name})", invite_mail_text(owner, firm.name, url)
            ),
        )
    await record_audit(
        session,
        firm_id=firm.id,
        actor=actor,
        action="firm.create",
        target_type="firm",
        target_id=str(firm.id),
        after={
            "name": firm.name,
            "installation_id": firm.instance_id,
            "owner": str(owner.id),
            "email_sent": sent,
        },
    )
    await _after_firm_created(session, request, firm)
    await set_platform_bypass(session)
    await session.refresh(firm)
    row = next(r for r in await _firm_rows(session) if r.id == str(firm.id))
    return FirmCreated(firm=row, owner=user_out(owner), invite_link=url, email_sent=sent)


async def _after_firm_created(session: AsyncSession, request: Request, firm: Firm) -> None:
    """The firm's platform subdomain under the root domain, when a root is set."""
    from app.domains import assign_platform_subdomain

    await set_platform_bypass(session)
    await assign_platform_subdomain(session, firm)


class ReasonIn(BaseModel):
    reason: str = Field(min_length=3, max_length=1000)


async def _platform_firm(session: AsyncSession, firm_id: uuid.UUID) -> Firm:
    await set_platform_bypass(session)
    firm = await session.get(Firm, firm_id)
    if firm is None or firm.kind != "firm":
        raise HTTPException(404, {"error": "not_found", "message": "Firm not found."})
    return firm


@router.post("/platform/firms/{firm_id}/deactivate", response_model=FirmRow)
async def deactivate_firm(
    firm_id: uuid.UUID, body: ReasonIn, session: SessionDep, actor: Actor = Depends(require("platform.admin"))
) -> FirmRow:
    firm = await _platform_firm(session, firm_id)
    firm.is_active = False
    await record_audit(
        session,
        firm_id=firm.id,
        actor=actor,
        action="firm.deactivate",
        target_type="firm",
        target_id=str(firm.id),
        after={"reason": body.reason},
    )
    return next(r for r in await _firm_rows(session) if r.id == str(firm.id))


@router.post("/platform/firms/{firm_id}/reactivate", response_model=FirmRow)
async def reactivate_firm(
    firm_id: uuid.UUID, body: ReasonIn, session: SessionDep, actor: Actor = Depends(require("platform.admin"))
) -> FirmRow:
    firm = await _platform_firm(session, firm_id)
    if firm.deleted_at is not None:
        firm.deleted_at, firm.erase_after, firm.deletion_reason = None, None, None
    firm.is_active = True
    await record_audit(
        session,
        firm_id=firm.id,
        actor=actor,
        action="firm.reactivate",
        target_type="firm",
        target_id=str(firm.id),
        after={"reason": body.reason},
    )
    return next(r for r in await _firm_rows(session) if r.id == str(firm.id))


@router.post("/platform/firms/{firm_id}/delete", response_model=FirmRow)
async def delete_firm(
    firm_id: uuid.UUID, body: ReasonIn, session: SessionDep, actor: Actor = Depends(require("platform.admin"))
) -> FirmRow:
    """Marks the firm deleted (users locked out); its data is erased 30 days later. Needs a backup that
    is less than 24 hours old."""
    firm = await _platform_firm(session, firm_id)
    from app.backup.service import has_recent_backup

    if not await has_recent_backup(session, firm.id, timedelta(hours=24)):
        raise HTTPException(
            409,
            {
                "error": "backup_required",
                "message": "Make a backup of this firm first (less than 24 hours old).",
            },
        )
    now = datetime.now(UTC)
    firm.is_active = False
    firm.deleted_at, firm.erase_after, firm.deletion_reason = now, now + timedelta(days=30), body.reason
    await record_audit(
        session,
        firm_id=firm.id,
        actor=actor,
        action="firm.delete",
        target_type="firm",
        target_id=str(firm.id),
        after={"reason": body.reason, "erase_after": firm.erase_after.isoformat()},
    )
    return next(r for r in await _firm_rows(session) if r.id == str(firm.id))


# ---- switching into a firm ---------------------------------------------------------------------------
class SwitchIn(BaseModel):
    firm_id: uuid.UUID | None = None


@router.post("/platform/switch", response_model=UserOut)
async def switch_firm(
    body: SwitchIn,
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    actor: Actor = Depends(require("platform.admin")),
) -> UserOut:
    """Enter a firm (full access for setup and troubleshooting) or go back to the platform view. Both
    are written to the firm's audit log, so its owner sees them."""
    row = request.state.session_row
    user = request.state.user
    await set_platform_bypass(session)
    previous = row.acting_firm_id
    if body.firm_id is not None:
        firm = await session.get(Firm, body.firm_id)
        if firm is None or firm.kind != "firm" or firm.deleted_at is not None:
            raise HTTPException(404, {"error": "not_found", "message": "Firm not found."})
        row.acting_firm_id = firm.id
        await record_audit(
            session,
            firm_id=firm.id,
            actor=actor,
            action="platform.enter_firm",
            target_type="firm",
            target_id=str(firm.id),
        )
    else:
        row.acting_firm_id = None
        if previous is not None:
            await record_audit(
                session,
                firm_id=previous,
                actor=actor,
                action="platform.leave_firm",
                target_type="firm",
                target_id=str(previous),
            )
    return user_out(user, row, settings)
