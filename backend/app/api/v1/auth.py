from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select

from app.api.deps import ActorDep, SessionDep, SettingsDep, UserDep, _token_from_request
from app.audit.service import record_audit
from app.auth.passwords import verify_password
from app.auth.sessions import COOKIE_NAME, create_session, revoke_session
from app.auth.totp import verify_code
from app.db.rls import set_platform_bypass
from app.licensing.store import FirmSecrets
from app.models.firm import Firm
from app.models.user import User
from app.tenancy.context import Actor

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=1024)
    totp_code: str | None = None


class UserOut(BaseModel):
    id: str
    email: str
    display_name: str
    role: str
    firm_id: str | None
    client_company_id: str | None
    locale: str
    totp_enabled: bool


def user_out(u: User) -> UserOut:
    return UserOut(
        id=str(u.id),
        email=u.email,
        display_name=u.display_name,
        role=u.role.value,
        firm_id=str(u.firm_id) if u.firm_id else None,
        client_company_id=str(u.client_company_id) if u.client_company_id else None,
        locale=u.locale,
        totp_enabled=u.totp_enabled,
    )


@router.post("/login", response_model=UserOut)
async def login(
    body: LoginIn, request: Request, response: Response, session: SessionDep, settings: SettingsDep
) -> UserOut:
    ip = request.client.host if request.client else "unknown"
    decision = request.app.state.rate_limiter.check(f"login-ip:{ip}", settings.login_ip_rate_per_minute)
    if not decision.allowed:
        raise HTTPException(
            429,
            {"error": "rate_limited", "message": "Too many sign-in attempts. Try again in a minute."},
            headers=decision.headers(),
        )
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
    if user is None or not user.is_active or not verify_password(user.password_hash, body.password):
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
        if not body.totp_code:
            raise HTTPException(
                401, {"error": "totp_required", "message": "Enter the code from your authenticator app."}
            )
        firm = await session.get(Firm, user.firm_id) if user.firm_id else None
        secret = (
            FirmSecrets(request.app.state.master_key, firm).decrypt("totp", user.totp_secret_enc)
            if firm is not None and user.totp_secret_enc
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
            raise HTTPException(401, {"error": "totp_invalid", "message": "The authenticator code is wrong."})
        mfa_passed = True
    token = await create_session(session, user, settings.session_ttl_seconds, mfa_passed=mfa_passed)
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
    return user_out(user)


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
async def me(user: UserDep) -> UserOut:
    return user_out(user)
