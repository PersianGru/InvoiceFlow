from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select

from app.api.deps import ActorDep, MailerDep, SessionDep, SettingsDep, UserDep, _token_from_request
from app.audit.service import record_audit
from app.auth.accounts import (
    create_link,
    find_link,
    link_url,
    revoke_sessions,
    secret_holder,
    use_recovery_code,
)
from app.auth.passwords import WeakPasswordError, check_policy, hash_password, verify_password
from app.auth.sessions import COOKIE_NAME, create_session_row, revoke_session
from app.auth.totp import verify_code
from app.config import Settings
from app.db.rls import set_platform_bypass
from app.licensing.store import FirmSecrets
from app.mail import OutgoingMail
from app.models.user import User, UserSession
from app.tenancy.context import Actor

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=1024)
    totp_code: str | None = None
    recovery_code: str | None = Field(default=None, max_length=32)


class UserOut(BaseModel):
    id: str
    email: str
    display_name: str
    role: str
    firm_id: str | None
    client_company_id: str | None
    locale: str
    totp_enabled: bool
    status: str = "active"
    mfa_setup_required: bool = False
    acting_firm_id: str | None = None
    recovery_codes_left: int = 0
    created_at: str | None = None
    last_login_at: str | None = None


def user_out(u: User, row: UserSession | None = None, settings: Settings | None = None) -> UserOut:
    mfa_required = bool(
        row is not None and settings is not None and settings.mfa_enforced and not row.mfa_passed
    )
    acting = row.acting_firm_id if row is not None else None
    return UserOut(
        id=str(u.id),
        email=u.email,
        display_name=u.display_name,
        role=u.role.value,
        firm_id=str(acting) if acting else (str(u.firm_id) if u.firm_id else None),
        client_company_id=str(u.client_company_id) if u.client_company_id else None,
        locale=u.locale,
        totp_enabled=u.totp_enabled,
        status=u.status,
        mfa_setup_required=mfa_required,
        acting_firm_id=str(acting) if acting else None,
        recovery_codes_left=len(u.recovery_codes or []),
        created_at=u.created_at.isoformat() if u.created_at else None,
        last_login_at=u.last_login_at.isoformat() if u.last_login_at else None,
    )


def set_session_cookie(response: Response, settings: Settings, token: str) -> None:
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=settings.session_ttl_seconds,
        httponly=True,
        secure=settings.cookie_secure_effective,
        samesite="lax",
        path="/",
    )
    response.headers["x-session-token"] = token  # for API clients and tests; never logged


def _rate(request: Request, key: str, per_minute: int) -> None:
    decision = request.app.state.rate_limiter.check(key, per_minute)
    if not decision.allowed:
        raise HTTPException(
            429,
            {"error": "rate_limited", "message": "Too many attempts. Try again in a minute."},
            headers=decision.headers(),
        )


@router.post("/login", response_model=UserOut)
async def login(
    body: LoginIn, request: Request, response: Response, session: SessionDep, settings: SettingsDep
) -> UserOut:
    ip = request.client.host if request.client else "unknown"
    _rate(request, f"login-ip:{ip}", settings.login_ip_rate_per_minute)
    await set_platform_bypass(session)
    user = (await session.execute(select(User).where(User.email == body.email.lower()))).scalar_one_or_none()
    bad = HTTPException(401, {"error": "invalid_credentials", "message": "Email or password is wrong."})
    now = datetime.now(UTC)
    if user is not None and user.locked_until is not None and user.locked_until > now:
        await record_audit(
            session,
            firm_id=user.firm_id,
            actor=None,
            action="auth.login",
            target_type="user",
            target_id=str(user.id),
            result="locked",
        )
        raise HTTPException(
            423,
            {
                "error": "account_locked",
                "message": "Too many failed sign-ins. The account is locked for a few minutes.",
                "locked_until": user.locked_until.isoformat(),
            },
        )
    usable = user is not None and user.is_active and user.deleted_at is None and user.has_password
    if user is None or not usable or not verify_password(user.password_hash, body.password):
        if user is not None:
            window = timedelta(minutes=settings.login_lock_minutes)
            recent = user.last_failed_login_at is not None and now - user.last_failed_login_at < window
            user.failed_logins = (user.failed_logins or 0) + 1 if recent else 1
            user.last_failed_login_at = now
            if user.failed_logins >= settings.login_lock_threshold:
                user.locked_until = now + window
                user.failed_logins = 0
                await record_audit(
                    session,
                    firm_id=user.firm_id,
                    actor=None,
                    action="auth.locked",
                    target_type="user",
                    target_id=str(user.id),
                    after={"until": user.locked_until.isoformat()},
                )
        await record_audit(
            session,
            firm_id=user.firm_id if user else None,
            actor=None,
            action="auth.login",
            target_type="user",
            target_id=body.email.lower(),
            result="invalid_credentials",
        )
        raise bad
    mfa_passed = False
    if user.totp_enabled:
        if body.recovery_code:
            if not use_recovery_code(user, body.recovery_code):
                await record_audit(
                    session,
                    firm_id=user.firm_id,
                    actor=Actor.from_user(user),
                    action="auth.login",
                    target_type="user",
                    target_id=str(user.id),
                    result="recovery_code_failed",
                )
                raise HTTPException(
                    401, {"error": "recovery_code_invalid", "message": "The recovery code is wrong or used."}
                )
            await record_audit(
                session,
                firm_id=user.firm_id,
                actor=Actor.from_user(user),
                action="auth.recovery_code_used",
                target_type="user",
                target_id=str(user.id),
                after={"left": len(user.recovery_codes or [])},
            )
        else:
            if not body.totp_code:
                raise HTTPException(
                    401, {"error": "totp_required", "message": "Enter the code from your authenticator app."}
                )
            holder = await secret_holder(session, user)
            secret = (
                FirmSecrets(request.app.state.master_key, holder).decrypt("totp", user.totp_secret_enc)
                if holder is not None and user.totp_secret_enc
                else None
            )
            if not secret or not verify_code(secret, body.totp_code):
                await record_audit(
                    session,
                    firm_id=user.firm_id,
                    actor=Actor.from_user(user),
                    action="auth.login",
                    target_type="user",
                    target_id=str(user.id),
                    result="totp_failed",
                )
                raise HTTPException(
                    401, {"error": "totp_invalid", "message": "The authenticator code is wrong."}
                )
        mfa_passed = True
    token, row = await create_session_row(session, user, settings.session_ttl_seconds, mfa_passed=mfa_passed)
    user.last_login_at = datetime.now(UTC)
    user.failed_logins, user.locked_until = 0, None
    await record_audit(
        session,
        firm_id=user.firm_id,
        actor=Actor.from_user(user),
        action="auth.login",
        target_type="user",
        target_id=str(user.id),
    )
    set_session_cookie(response, settings, token)
    return user_out(user, row, settings)


@router.post("/logout", status_code=204)
async def logout(request: Request, response: Response, session: SessionDep, actor: ActorDep) -> Response:
    token = _token_from_request(request)
    await set_platform_bypass(session)
    if token:
        await revoke_session(session, token)
    await record_audit(
        session,
        firm_id=actor.firm_id,
        actor=actor,
        action="auth.logout",
        target_type="user",
        target_id=str(actor.user_id),
    )
    response.delete_cookie(COOKIE_NAME, path="/")
    return Response(status_code=204)


@router.get("/me", response_model=UserOut)
async def me(request: Request, user: UserDep, settings: SettingsDep) -> UserOut:
    return user_out(user, request.state.session_row, settings)


# ---- one-time links (invite, password reset) ----------------------------------------------------
class LinkIn(BaseModel):
    token: str = Field(min_length=10, max_length=128)


class LinkOut(BaseModel):
    kind: str
    email: str
    display_name: str
    expires_at: str


class LinkCompleteIn(LinkIn):
    password: str = Field(min_length=1, max_length=1024)


@router.post("/link/inspect", response_model=LinkOut)
async def inspect_link(body: LinkIn, request: Request, session: SessionDep, settings: SettingsDep) -> LinkOut:
    ip = request.client.host if request.client else "unknown"
    _rate(request, f"link-ip:{ip}", 30)
    await set_platform_bypass(session)
    found = await find_link(session, body.token)
    if found is None:
        raise HTTPException(
            404, {"error": "link_invalid", "message": "This link has expired or was already used."}
        )
    link, user = found
    return LinkOut(
        kind=link.kind,
        email=user.email,
        display_name=user.display_name,
        expires_at=link.expires_at.isoformat(),
    )


@router.post("/link/complete", response_model=UserOut)
async def complete_link(
    body: LinkCompleteIn, request: Request, response: Response, session: SessionDep, settings: SettingsDep
) -> UserOut:
    """Sets the password from an invite or reset link and signs the person in. Mandatory 2FA then
    asks for the authenticator setup before anything else."""
    ip = request.client.host if request.client else "unknown"
    _rate(request, f"link-ip:{ip}", 30)
    await set_platform_bypass(session)
    found = await find_link(session, body.token)
    if found is None:
        raise HTTPException(
            404, {"error": "link_invalid", "message": "This link has expired or was already used."}
        )
    link, user = found
    try:
        check_policy(body.password, settings.password_min_length)
    except WeakPasswordError as e:
        raise HTTPException(422, {"error": "weak_password", "message": str(e)}) from e
    now = datetime.now(UTC)
    link.used_at = now
    user.password_hash = hash_password(body.password)
    user.password_set_at = now
    user.failed_logins, user.locked_until = 0, None
    await revoke_sessions(session, user.id)
    await record_audit(
        session,
        firm_id=user.firm_id,
        actor=Actor.from_user(user),
        action="user.password_set" if link.kind == "invite" else "user.password_reset_done",
        target_type="user",
        target_id=str(user.id),
    )
    # a reset link proves only the mailbox: when 2FA is on, the next sign-in still asks for the code
    if user.totp_enabled:
        return user_out(user)
    token, row = await create_session_row(session, user, settings.session_ttl_seconds, mfa_passed=False)
    set_session_cookie(response, settings, token)
    return user_out(user, row, settings)


class ForgotIn(BaseModel):
    email: EmailStr


@router.post("/forgot-password", status_code=202)
async def forgot_password(
    body: ForgotIn, request: Request, session: SessionDep, settings: SettingsDep, mailer: MailerDep
) -> dict[str, str]:
    """Always the same answer, known email or not."""
    ip = request.client.host if request.client else "unknown"
    _rate(request, f"forgot-ip:{ip}", 5)
    _rate(request, f"forgot-email:{body.email.lower()}", 3)
    await set_platform_bypass(session)
    user = (await session.execute(select(User).where(User.email == body.email.lower()))).scalar_one_or_none()
    answer = {"message": "If the address belongs to an account, a reset link is on its way."}
    if user is None or not user.is_active or user.deleted_at is not None or not user.has_password:
        return answer
    token, _link = await create_link(session, user, "reset", None)
    sent = await mailer.send(
        user.firm_id,
        OutgoingMail(
            user.email,
            "Reset your InvoiceFlow password",
            reset_mail_text(user, link_url(settings, "reset", token)),
        ),
    )
    await record_audit(
        session,
        firm_id=user.firm_id,
        actor=None,
        action="user.password_reset_requested",
        target_type="user",
        target_id=str(user.id),
        result="sent" if sent else "not_sent",
    )
    return answer


def invite_mail_text(user: User, firm_name: str, url: str) -> str:
    return (
        f"Hello {user.display_name or user.email},\n\n"
        f"you were invited to InvoiceFlow ({firm_name}).\n\n"
        f"Open this link to set your password and your two-factor authentication:\n{url}\n\n"
        "The link works once and expires in 24 hours. If you did not expect this email, ignore it.\n"
    )


def reset_mail_text(user: User, url: str) -> str:
    return (
        f"Hello {user.display_name or user.email},\n\n"
        f"open this link to choose a new InvoiceFlow password:\n{url}\n\n"
        "The link works once and expires in 15 minutes. If you did not ask for it, ignore this email: "
        "your password stays as it is.\n"
    )
