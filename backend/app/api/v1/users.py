"""User management: invite links, password reset, 2FA reset, role changes,
deactivate/reactivate, delete with a sealed trace, and the user's own 2FA setup with a QR code."""

from __future__ import annotations

import io
import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    FirmDep,
    LicenseServiceDep,
    MailerDep,
    SessionDep,
    SettingsDep,
    UserDep,
    require,
)
from app.api.v1.auth import UserOut, invite_mail_text, reset_mail_text, user_out
from app.audit.service import record_audit
from app.auth.accounts import (
    NO_PASSWORD,
    create_link,
    link_url,
    new_recovery_codes,
    placeholder_label,
    revoke_sessions,
    secret_holder,
)
from app.auth.rbac import allowed
from app.auth.totp import new_secret, provisioning_uri, verify_code
from app.db.rls import set_platform_bypass, set_tenant
from app.licensing.gate import check_feature, check_limit
from app.licensing.store import FirmSecrets
from app.mail import OutgoingMail
from app.models.account import UserTombstone
from app.models.channel import SenderMapping
from app.models.client_company import ClientCompany
from app.models.firm import Firm
from app.models.user import CLIENT_ROLES, FIRM_STAFF_ROLES, Role, User
from app.tenancy.context import Actor

router = APIRouter(prefix="/users", tags=["users"])


class InviteIn(BaseModel):
    email: EmailStr
    display_name: str = Field(default="", max_length=200)
    role: Role
    client_company_id: uuid.UUID | None = None
    send_email: bool = True


class InviteOut(BaseModel):
    user: UserOut
    link: str  # the copy-link fallback (the token is in the fragment)
    email_sent: bool
    expires_at: str


class ReasonIn(BaseModel):
    reason: str = Field(min_length=3, max_length=1000)


class UserPatch(BaseModel):
    display_name: str | None = Field(default=None, max_length=200)
    role: Role | None = None
    client_company_id: uuid.UUID | None = None


def _perm_for(role: Role) -> str:
    return "users.manage_clients" if role in CLIENT_ROLES else "users.manage_staff"


def _may_manage(actor: Actor, role: Role) -> None:
    if role in (Role.super_admin, Role.api_key):
        raise HTTPException(403, {"error": "forbidden", "message": "Platform admins are not managed here."})
    if not allowed(actor.role, _perm_for(role)):
        raise HTTPException(
            403, {"error": "forbidden", "message": "You are not allowed to manage this user."}
        )


async def _target(session: AsyncSession, user_id: uuid.UUID, actor: Actor) -> User:
    u = await session.get(User, user_id)
    if u is None or u.firm_id != actor.firm_id or u.deleted_at is not None:
        raise HTTPException(404, {"error": "not_found", "message": "User not found."})
    if u.id == actor.user_id:
        raise HTTPException(409, {"error": "self", "message": "You cannot do this to your own account."})
    _may_manage(actor, u.role)
    return u


async def _active_owners(session: Any, exclude: uuid.UUID | None = None) -> int:
    stmt = (
        select(func.count())
        .select_from(User)
        .where(User.role == Role.firm_owner, User.is_active.is_(True), User.deleted_at.is_(None))
    )
    if exclude is not None:
        stmt = stmt.where(User.id != exclude)
    return int((await session.execute(stmt)).scalar_one())


async def _staff_count(session: Any) -> int:
    return int(
        (
            await session.execute(
                select(func.count())
                .select_from(User)
                .where(
                    User.role.in_([r.value for r in FIRM_STAFF_ROLES]),
                    User.is_active.is_(True),
                    User.deleted_at.is_(None),
                )
            )
        ).scalar_one()
    )


@router.get("", response_model=list[UserOut])
async def list_users(session: SessionDep, actor: Actor = Depends(require("users.view"))) -> list[UserOut]:
    rows = (
        (await session.execute(select(User).where(User.deleted_at.is_(None)).order_by(User.created_at)))
        .scalars()
        .all()
    )
    return [user_out(u) for u in rows]


async def _send_invite(
    request: Request, session: Any, mailer: Any, firm: Firm, user: User, actor: Actor, send: bool
) -> InviteOut:
    token, link = await create_link(session, user, "invite", actor.user_id)
    url = link_url(request.app.state.settings, "invite", token)
    sent = False
    if send:
        sent = await mailer.send(
            firm.id,
            OutgoingMail(
                user.email, f"Your InvoiceFlow access ({firm.name})", invite_mail_text(user, firm.name, url)
            ),
        )
    return InviteOut(user=user_out(user), link=url, email_sent=sent, expires_at=link.expires_at.isoformat())


@router.post("/invite", response_model=InviteOut, status_code=201)
async def invite(
    body: InviteIn,
    request: Request,
    session: SessionDep,
    firm: FirmDep,
    svc: LicenseServiceDep,
    mailer: MailerDep,
    actor: Actor = Depends(require("users.manage_clients")),
) -> InviteOut:
    _may_manage(actor, body.role)
    if body.role in FIRM_STAFF_ROLES:
        snap = check_feature(svc.snapshot(firm), "core")
        check_limit(snap, "max_staff_users", await _staff_count(session))
    elif body.client_company_id is None:
        raise HTTPException(
            422, {"error": "invalid_request", "message": "Client users need a client company."}
        )
    if body.client_company_id is not None:
        client = await session.get(ClientCompany, body.client_company_id)
        if client is None or client.firm_id != firm.id:
            raise HTTPException(404, {"error": "not_found", "message": "Client company not found."})
    await set_platform_bypass(session)
    taken = (await session.execute(select(User.id).where(User.email == body.email.lower()))).first()
    await set_tenant(session, firm.id)
    if taken:
        raise HTTPException(
            409, {"error": "email_taken", "message": "An account with this email already exists."}
        )
    u = User(
        firm_id=firm.id,
        client_company_id=body.client_company_id if body.role in CLIENT_ROLES else None,
        email=body.email.lower(),
        display_name=body.display_name or body.email.split("@")[0],
        password_hash=NO_PASSWORD,
        role=body.role,
    )
    session.add(u)
    await session.flush()
    out = await _send_invite(request, session, mailer, firm, u, actor, body.send_email)
    await record_audit(
        session,
        firm_id=firm.id,
        actor=actor,
        action="user.invite",
        target_type="user",
        target_id=str(u.id),
        after={"role": u.role.value, "email_sent": out.email_sent},
    )
    return out


@router.post("/{user_id}/resend-invite", response_model=InviteOut)
async def resend_invite(
    user_id: uuid.UUID,
    request: Request,
    session: SessionDep,
    firm: FirmDep,
    mailer: MailerDep,
    actor: Actor = Depends(require("users.manage_clients")),
) -> InviteOut:
    u = await _target(session, user_id, actor)
    if u.has_password:
        raise HTTPException(
            409, {"error": "already_active", "message": "This user has already set a password."}
        )
    out = await _send_invite(request, session, mailer, firm, u, actor, True)
    await record_audit(
        session,
        firm_id=firm.id,
        actor=actor,
        action="user.invite_resend",
        target_type="user",
        target_id=str(u.id),
        after={"email_sent": out.email_sent},
    )
    return out


@router.post("/{user_id}/reset-password", response_model=InviteOut)
async def reset_password(
    user_id: uuid.UUID,
    request: Request,
    session: SessionDep,
    firm: FirmDep,
    mailer: MailerDep,
    actor: Actor = Depends(require("users.manage_clients")),
) -> InviteOut:
    u = await _target(session, user_id, actor)
    if not u.is_active:
        raise HTTPException(409, {"error": "inactive", "message": "Reactivate the user first."})
    kind = "reset" if u.has_password else "invite"
    token, link = await create_link(session, u, kind, actor.user_id)
    url = link_url(request.app.state.settings, kind, token)
    if kind == "reset":
        await revoke_sessions(session, u.id)
    text = reset_mail_text(u, url) if kind == "reset" else invite_mail_text(u, firm.name, url)
    subject = (
        "Reset your InvoiceFlow password" if kind == "reset" else f"Your InvoiceFlow access ({firm.name})"
    )
    sent = await mailer.send(firm.id, OutgoingMail(u.email, subject, text))
    await record_audit(
        session,
        firm_id=firm.id,
        actor=actor,
        action="user.password_reset_sent",
        target_type="user",
        target_id=str(u.id),
        after={"email_sent": sent},
    )
    return InviteOut(user=user_out(u), link=url, email_sent=sent, expires_at=link.expires_at.isoformat())


@router.post("/{user_id}/reset-2fa", response_model=UserOut)
async def reset_2fa(
    user_id: uuid.UUID,
    body: ReasonIn,
    session: SessionDep,
    firm: FirmDep,
    actor: Actor = Depends(require("users.manage_staff")),
) -> UserOut:
    """Firm owner (or super admin): the user sets up 2FA again with a QR code at the next sign-in."""
    u = await session.get(User, user_id)
    if u is None or u.firm_id != firm.id or u.deleted_at is not None:
        raise HTTPException(404, {"error": "not_found", "message": "User not found."})
    if u.id == actor.user_id:
        raise HTTPException(409, {"error": "self", "message": "You cannot do this to your own account."})
    u.totp_secret_enc, u.totp_enabled, u.recovery_codes = None, False, []
    await revoke_sessions(session, u.id)
    await record_audit(
        session,
        firm_id=firm.id,
        actor=actor,
        action="user.2fa_reset",
        target_type="user",
        target_id=str(u.id),
        after={"reason": body.reason},
    )
    return user_out(u)


@router.patch("/{user_id}", response_model=UserOut)
async def update_user(
    user_id: uuid.UUID,
    body: UserPatch,
    session: SessionDep,
    firm: FirmDep,
    svc: LicenseServiceDep,
    actor: Actor = Depends(require("users.manage_clients")),
) -> UserOut:
    u = await _target(session, user_id, actor)
    before = {
        "role": u.role.value,
        "display_name": u.display_name,
        "client_company_id": str(u.client_company_id),
    }
    if body.role is not None and body.role != u.role:
        _may_manage(actor, body.role)
        if (body.role in CLIENT_ROLES) != (u.role in CLIENT_ROLES):
            raise HTTPException(
                409,
                {
                    "error": "role_change",
                    "message": "Staff and client accounts cannot be swapped. Invite again.",
                },
            )
        if u.role == Role.firm_owner and await _active_owners(session, exclude=u.id) == 0:
            raise HTTPException(409, {"error": "last_owner", "message": "The firm needs at least one owner."})
        u.role = body.role
    if body.display_name is not None:
        u.display_name = body.display_name
    if body.client_company_id is not None and u.role in CLIENT_ROLES:
        client = await session.get(ClientCompany, body.client_company_id)
        if client is None or client.firm_id != firm.id:
            raise HTTPException(404, {"error": "not_found", "message": "Client company not found."})
        u.client_company_id = client.id
    await record_audit(
        session,
        firm_id=firm.id,
        actor=actor,
        action="user.update",
        target_type="user",
        target_id=str(u.id),
        before=before,
        after={
            "role": u.role.value,
            "display_name": u.display_name,
            "client_company_id": str(u.client_company_id),
        },
    )
    return user_out(u)


async def _set_pairings(session: Any, user_id: uuid.UUID, active: bool) -> int:
    res = await session.execute(
        update(SenderMapping).where(SenderMapping.user_id == user_id).values(active=active)
    )
    return int(res.rowcount or 0)


@router.post("/{user_id}/deactivate", response_model=UserOut)
async def deactivate(
    user_id: uuid.UUID,
    body: ReasonIn,
    session: SessionDep,
    firm: FirmDep,
    actor: Actor = Depends(require("users.manage_clients")),
) -> UserOut:
    u = await _target(session, user_id, actor)
    if u.role == Role.firm_owner and await _active_owners(session, exclude=u.id) == 0:
        raise HTTPException(
            409, {"error": "last_owner", "message": "The firm needs at least one active owner."}
        )
    u.is_active, u.deactivated_at = False, datetime.now(UTC)
    await revoke_sessions(session, u.id)
    pairings = await _set_pairings(session, u.id, False)
    await record_audit(
        session,
        firm_id=firm.id,
        actor=actor,
        action="user.deactivate",
        target_type="user",
        target_id=str(u.id),
        after={"reason": body.reason, "pairings_deactivated": pairings},
    )
    return user_out(u)


@router.post("/{user_id}/reactivate", response_model=UserOut)
async def reactivate(
    user_id: uuid.UUID,
    body: ReasonIn,
    session: SessionDep,
    firm: FirmDep,
    svc: LicenseServiceDep,
    actor: Actor = Depends(require("users.manage_clients")),
) -> UserOut:
    u = await _target(session, user_id, actor)
    if u.role in FIRM_STAFF_ROLES:
        snap = check_feature(svc.snapshot(firm), "core")
        check_limit(snap, "max_staff_users", await _staff_count(session))
    u.is_active, u.deactivated_at = True, None
    pairings = await _set_pairings(session, u.id, True)
    await record_audit(
        session,
        firm_id=firm.id,
        actor=actor,
        action="user.reactivate",
        target_type="user",
        target_id=str(u.id),
        after={"reason": body.reason, "pairings_reactivated": pairings},
    )
    return user_out(u)


@router.post("/{user_id}/delete", response_model=UserOut)
async def delete_user(
    user_id: uuid.UUID,
    body: ReasonIn,
    request: Request,
    session: SessionDep,
    firm: FirmDep,
    actor: Actor = Depends(require("users.manage_clients")),
) -> UserOut:
    """Erases the person (name, email, password, 2FA, sessions, pairings); history keeps the anonymous
    placeholder. Who it was stays in one sealed tombstone for the bookkeeping retention period, readable
    by the firm owner only."""
    # TODO(legal): keeping the identity of a deleted user for the retention period (GDPR art. 17(3)(b),
    # GoBD); safe default: sealed, owner-only, erased afterwards.
    u = await _target(session, user_id, actor)
    if u.role == Role.firm_owner and await _active_owners(session, exclude=u.id) == 0:
        raise HTTPException(
            409, {"error": "last_owner", "message": "The firm needs at least one active owner."}
        )
    label = placeholder_label(u.id)
    identity = json.dumps({"email": u.email, "display_name": u.display_name, "role": u.role.value})
    sealed = FirmSecrets(request.app.state.master_key, firm).seal(f"tombstone:{u.id}", identity)
    now = datetime.now(UTC)
    session.add(
        UserTombstone(
            firm_id=firm.id,
            user_id=u.id,
            label=label,
            identity_enc=sealed,
            reason=body.reason,
            deleted_by_user_id=actor.user_id,
            erase_after=now + timedelta(days=max(firm.retention_days, 365)),
        )
    )
    pairings = (
        (await session.execute(select(SenderMapping).where(SenderMapping.user_id == u.id))).scalars().all()
    )
    for m in pairings:
        await session.delete(m)
    await revoke_sessions(session, u.id)
    u.email = f"deleted-{u.id.hex}@deleted.invalid"
    u.display_name = label
    u.password_hash = NO_PASSWORD
    u.totp_secret_enc, u.totp_enabled, u.recovery_codes = None, False, []
    u.is_active, u.deleted_at = False, now
    await record_audit(
        session,
        firm_id=firm.id,
        actor=actor,
        action="user.delete",
        target_type="user",
        target_id=str(u.id),
        after={"label": label, "reason": body.reason, "pairings_deleted": len(pairings)},
    )
    return user_out(u)


class TombstoneOut(BaseModel):
    id: str
    user_id: str
    label: str
    reason: str
    deleted_by_user_id: str | None
    deleted_at: str
    erase_after: str


@router.get("/deleted", response_model=list[TombstoneOut])
async def deleted_users(
    session: SessionDep, actor: Actor = Depends(require("users.reveal_deleted"))
) -> list[TombstoneOut]:
    rows = (
        (await session.execute(select(UserTombstone).order_by(UserTombstone.deleted_at.desc())))
        .scalars()
        .all()
    )
    return [
        TombstoneOut(
            id=str(t.id),
            user_id=str(t.user_id),
            label=t.label,
            reason=t.reason,
            deleted_by_user_id=str(t.deleted_by_user_id) if t.deleted_by_user_id else None,
            deleted_at=t.deleted_at.isoformat(),
            erase_after=t.erase_after.isoformat(),
        )
        for t in rows
    ]


@router.post("/deleted/{tombstone_id}/reveal")
async def reveal_deleted(
    tombstone_id: uuid.UUID,
    request: Request,
    session: SessionDep,
    firm: FirmDep,
    actor: Actor = Depends(require("users.reveal_deleted")),
) -> dict[str, Any]:
    t = await session.get(UserTombstone, tombstone_id)
    if t is None or t.firm_id != firm.id:
        raise HTTPException(404, {"error": "not_found", "message": "Not found."})
    opened = FirmSecrets(request.app.state.master_key, firm).open(f"tombstone:{t.user_id}", t.identity_enc)
    await record_audit(
        session,
        firm_id=firm.id,
        actor=actor,
        action="user.tombstone_reveal",
        target_type="user",
        target_id=str(t.user_id),
    )
    identity = json.loads(opened) if opened else {}
    return {"label": t.label, **identity}


# ---- the signed-in user's own 2FA ---------------------------------------------------------------
class TotpSetupOut(BaseModel):
    otpauth_uri: str
    secret: str  # for authenticator apps without a camera; shown once
    qr_svg: str


def qr_svg(data: str) -> str:
    import qrcode
    import qrcode.image.svg

    img = qrcode.make(data, image_factory=qrcode.image.svg.SvgPathImage, box_size=8, border=2)
    buf = io.BytesIO()
    img.save(buf)
    return buf.getvalue().decode("utf-8")


async def _holder(request: Request, session: Any, user: User) -> Firm:
    await set_platform_bypass(session)
    holder = await secret_holder(session, user)
    row = request.state.session_row
    if user.role == Role.super_admin:
        if row.acting_firm_id is not None:
            await set_tenant(session, row.acting_firm_id)
    elif user.firm_id is not None:
        await set_tenant(session, user.firm_id)
    if holder is None:
        raise HTTPException(409, {"error": "no_platform", "message": "The platform is not set up yet."})
    return holder


@router.post("/me/totp/setup", response_model=TotpSetupOut)
async def totp_setup(request: Request, user: UserDep, session: SessionDep) -> TotpSetupOut:
    if user.totp_enabled:
        raise HTTPException(
            409,
            {
                "error": "totp_active",
                "message": "Two-factor authentication is already on. Ask an owner to reset it.",
            },
        )
    holder = await _holder(request, session, user)
    secret = new_secret()
    user.totp_secret_enc = FirmSecrets(request.app.state.master_key, holder).encrypt("totp", secret)
    user.totp_enabled = False
    uri = provisioning_uri(secret, user.email)
    return TotpSetupOut(otpauth_uri=uri, secret=secret, qr_svg=qr_svg(uri))


class TotpConfirmIn(BaseModel):
    code: str = Field(min_length=6, max_length=8)


class TotpConfirmOut(BaseModel):
    recovery_codes: list[str]


@router.post("/me/totp/confirm", response_model=TotpConfirmOut)
async def totp_confirm(
    body: TotpConfirmIn, request: Request, user: UserDep, session: SessionDep, settings: SettingsDep
) -> TotpConfirmOut:
    if not user.totp_secret_enc:
        raise HTTPException(409, {"error": "totp_not_set_up", "message": "Start the setup first."})
    holder = await _holder(request, session, user)
    secret = FirmSecrets(request.app.state.master_key, holder).decrypt("totp", user.totp_secret_enc)
    if not verify_code(secret, body.code):
        raise HTTPException(400, {"error": "totp_invalid", "message": "The authenticator code is wrong."})
    user.totp_enabled = True
    codes, hashes = new_recovery_codes()
    user.recovery_codes = hashes
    row = request.state.session_row
    row.mfa_passed = True
    await revoke_sessions(session, user.id, keep=row.id)
    await record_audit(
        session,
        firm_id=user.firm_id,
        actor=Actor.from_user(user),
        action="user.totp_enable",
        target_type="user",
        target_id=str(user.id),
    )
    return TotpConfirmOut(recovery_codes=codes)


class CodeIn(BaseModel):
    code: str = Field(min_length=6, max_length=8)


async def require_fresh_totp(request: Request, session: Any, user: User, code: str) -> None:
    """A fresh authenticator code for sensitive steps (backup passwords, recovery codes)."""
    if not user.totp_enabled or not user.totp_secret_enc:
        raise HTTPException(
            409, {"error": "totp_required", "message": "Set up two-factor authentication first."}
        )
    holder = await _holder(request, session, user)
    secret = FirmSecrets(request.app.state.master_key, holder).decrypt("totp", user.totp_secret_enc)
    if not verify_code(secret, code):
        raise HTTPException(400, {"error": "totp_invalid", "message": "The authenticator code is wrong."})


@router.post("/me/recovery-codes", response_model=TotpConfirmOut)
async def regenerate_recovery_codes(
    body: CodeIn, request: Request, user: UserDep, session: SessionDep
) -> TotpConfirmOut:
    await require_fresh_totp(request, session, user, body.code)
    codes, hashes = new_recovery_codes()
    user.recovery_codes = hashes
    await record_audit(
        session,
        firm_id=user.firm_id,
        actor=Actor.from_user(user),
        action="user.recovery_codes_regenerated",
        target_type="user",
        target_id=str(user.id),
    )
    return TotpConfirmOut(recovery_codes=codes)
