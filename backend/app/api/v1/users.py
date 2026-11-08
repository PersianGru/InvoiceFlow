from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, select

from app.api.deps import FirmDep, LicenseServiceDep, SessionDep, SettingsDep, UserDep, require
from app.api.v1.auth import UserOut, user_out
from app.audit.service import record_audit
from app.auth.passwords import WeakPasswordError, check_policy, hash_password
from app.auth.totp import new_secret, provisioning_uri, verify_code
from app.db.rls import set_platform_bypass, set_tenant
from app.licensing.gate import check_feature, check_limit
from app.licensing.store import FirmSecrets
from app.models.user import FIRM_STAFF_ROLES, Role, User
from app.tenancy.context import Actor

router = APIRouter(prefix="/users", tags=["users"])


class InviteIn(BaseModel):
    email: EmailStr
    display_name: str = Field(default="", max_length=200)
    role: Role
    password: str = Field(min_length=1, max_length=1024)
    client_company_id: uuid.UUID | None = None


@router.get("", response_model=list[UserOut])
async def list_users(session: SessionDep, actor: Actor = Depends(require("firm.manage"))) -> list[UserOut]:
    rows = (await session.execute(select(User).order_by(User.created_at))).scalars().all()
    return [user_out(u) for u in rows]


@router.post("/invite", response_model=UserOut, status_code=201)
async def invite(
    body: InviteIn,
    session: SessionDep,
    firm: FirmDep,
    svc: LicenseServiceDep,
    settings: SettingsDep,
    actor: Actor = Depends(require("staff.invite")),
) -> UserOut:
    if body.role == Role.super_admin:
        raise HTTPException(403, {"error": "forbidden", "message": "Platform admins are not created here."})
    try:
        check_policy(body.password, settings.password_min_length)
    except WeakPasswordError as e:
        raise HTTPException(422, {"error": "weak_password", "message": str(e)}) from e
    if body.role in FIRM_STAFF_ROLES:
        snap = check_feature(svc.snapshot(firm), "core")
        current = (
            await session.execute(
                select(func.count())
                .select_from(User)
                .where(User.role.in_([r.value for r in FIRM_STAFF_ROLES]), User.is_active.is_(True))
            )
        ).scalar_one()
        check_limit(snap, "max_staff_users", int(current))
    elif body.client_company_id is None:
        raise HTTPException(
            422, {"error": "invalid_request", "message": "Client users need a client company."}
        )
    await set_platform_bypass(session)
    taken = (await session.execute(select(User.id).where(User.email == body.email.lower()))).first()
    await set_tenant(session, firm.id)
    if taken:
        raise HTTPException(
            409, {"error": "email_taken", "message": "An account with this email already exists."}
        )
    u = User(
        firm_id=firm.id,
        client_company_id=body.client_company_id,
        email=body.email.lower(),
        display_name=body.display_name,
        password_hash=hash_password(body.password),
        role=body.role,
    )
    session.add(u)
    await session.flush()
    await record_audit(
        session,
        firm_id=firm.id,
        actor=actor,
        action="user.invite",
        target_type="user",
        target_id=str(u.id),
        after={"role": u.role.value},
    )
    return user_out(u)


class TotpSetupOut(BaseModel):
    otpauth_uri: str


@router.post("/me/totp/setup", response_model=TotpSetupOut)
async def totp_setup(request: Request, user: UserDep, firm: FirmDep, session: SessionDep) -> TotpSetupOut:
    secret = new_secret()
    user.totp_secret_enc = FirmSecrets(request.app.state.master_key, firm).encrypt("totp", secret)
    user.totp_enabled = False
    return TotpSetupOut(otpauth_uri=provisioning_uri(secret, user.email))


class TotpConfirmIn(BaseModel):
    code: str = Field(min_length=6, max_length=8)


@router.post("/me/totp/confirm", status_code=204)
async def totp_confirm(
    body: TotpConfirmIn, request: Request, user: UserDep, firm: FirmDep, session: SessionDep
) -> None:
    if not user.totp_secret_enc:
        raise HTTPException(409, {"error": "totp_not_set_up", "message": "Start the setup first."})
    secret = FirmSecrets(request.app.state.master_key, firm).decrypt("totp", user.totp_secret_enc)
    if not verify_code(secret, body.code):
        raise HTTPException(400, {"error": "totp_invalid", "message": "The authenticator code is wrong."})
    user.totp_enabled = True
    await record_audit(
        session,
        firm_id=firm.id,
        actor=Actor.from_user(user),
        action="user.totp_enable",
        target_type="user",
        target_id=str(user.id),
    )
