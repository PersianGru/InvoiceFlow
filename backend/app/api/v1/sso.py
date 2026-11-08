"""Firm SSO (OIDC): configuration (firm owner, gated by ``sso``), start and
callback. Deny by default: an SSO user must already be invited unless auto-provisioning is on, and
only the configured email domains may sign in."""

from __future__ import annotations

import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select

from app.api.deps import FirmPathDep, LicenseServiceDep, SessionDep, SettingsDep, require
from app.audit.service import record_audit
from app.auth.oidc import OidcError, discover, fetch_jwks, pkce_pair, verify_id_token
from app.auth.sessions import COOKIE_NAME, create_session
from app.db.rls import set_platform_bypass, set_tenant
from app.licensing.gate import check_feature
from app.licensing.store import FirmSecrets
from app.models.compliance import FirmSso, SsoLogin
from app.models.firm import Firm
from app.models.user import FIRM_STAFF_ROLES, Role, User
from app.tenancy.context import Actor

router = APIRouter(tags=["sso"])

LOGIN_TTL = timedelta(minutes=10)


class SsoConfigIn(BaseModel):
    enabled: bool = False
    issuer: str = Field(default="", max_length=500)
    client_id: str = Field(default="", max_length=300)
    client_secret: str | None = Field(default=None, max_length=500)  # omitted = keep the stored one
    allowed_domains: list[str] = Field(default_factory=list, max_length=20)
    provisioning: str = Field(default="invited_only", pattern="^(invited_only|auto)$")
    default_role: str = Field(default="junior_accountant", pattern="^(accountant|junior_accountant)$")


class SsoConfigOut(BaseModel):
    enabled: bool
    issuer: str
    client_id: str
    has_client_secret: bool
    allowed_domains: list[str]
    provisioning: str
    default_role: str
    callback_url: str


def _callback_url(request: Request) -> str:
    return str(request.app.state.settings.public_url).rstrip("/") + "/api/v1/auth/sso/callback"


def _out(cfg: FirmSso | None, request: Request) -> SsoConfigOut:
    return SsoConfigOut(
        enabled=bool(cfg and cfg.enabled),
        issuer=cfg.issuer if cfg else "",
        client_id=cfg.client_id if cfg else "",
        has_client_secret=bool(cfg and cfg.client_secret_enc),
        allowed_domains=list(cfg.allowed_domains) if cfg else [],
        provisioning=cfg.provisioning if cfg else "invited_only",
        default_role=cfg.default_role if cfg else "junior_accountant",
        callback_url=_callback_url(request),
    )


@router.get("/firms/{firm_id}/sso", response_model=SsoConfigOut)
async def get_sso(
    request: Request, firm: FirmPathDep, session: SessionDep, actor: Actor = Depends(require("firm.manage"))
) -> SsoConfigOut:
    return _out(await session.get(FirmSso, firm.id), request)


@router.put("/firms/{firm_id}/sso", response_model=SsoConfigOut)
async def put_sso(
    request: Request,
    body: SsoConfigIn,
    firm: FirmPathDep,
    session: SessionDep,
    svc: LicenseServiceDep,
    actor: Actor = Depends(require("firm.manage")),
) -> SsoConfigOut:
    if body.enabled:
        check_feature(svc.snapshot(firm), "sso")  # point of use of the entitlement
        if not body.issuer.startswith("https://") or not body.client_id:
            raise HTTPException(
                422, {"error": "invalid_request", "message": "An https issuer and a client id are required."}
            )
    domains = sorted({d.strip().lower().lstrip("@") for d in body.allowed_domains if d.strip()})
    cfg = await session.get(FirmSso, firm.id)
    if cfg is None:
        cfg = FirmSso(firm_id=firm.id)
        session.add(cfg)
    cfg.enabled = body.enabled
    cfg.issuer = body.issuer.strip().rstrip("/")
    cfg.client_id = body.client_id.strip()
    cfg.allowed_domains = domains
    cfg.provisioning = body.provisioning
    cfg.default_role = body.default_role
    cfg.updated_at = datetime.now(UTC)
    if body.client_secret:
        cfg.client_secret_enc = FirmSecrets(request.app.state.master_key, firm).seal(
            "sso_client_secret", body.client_secret
        )
    if body.enabled and not cfg.client_secret_enc:
        raise HTTPException(422, {"error": "invalid_request", "message": "A client secret is required."})
    await session.flush()
    await record_audit(
        session,
        firm_id=firm.id,
        actor=actor,
        action="sso.configure",
        target_type="firm",
        target_id=str(firm.id),
        after={
            "enabled": cfg.enabled,
            "issuer": cfg.issuer,
            "domains": domains,
            "provisioning": cfg.provisioning,
        },
    )
    return _out(cfg, request)


class SsoStartIn(BaseModel):
    email: EmailStr
    redirect_to: str = Field(default="/inbox", max_length=500)


async def _config_for_email(session: SessionDep, email: str) -> tuple[Firm, FirmSso] | None:
    domain = email.lower().rsplit("@", 1)[-1]
    rows = (await session.execute(select(FirmSso).where(FirmSso.enabled.is_(True)))).scalars().all()
    for cfg in rows:
        if domain in (cfg.allowed_domains or []):
            firm = await session.get(Firm, cfg.firm_id)
            if firm is not None and firm.is_active:
                return firm, cfg
    return None


@router.post("/auth/sso/start")
async def sso_start(
    request: Request, body: SsoStartIn, session: SessionDep, svc: LicenseServiceDep, settings: SettingsDep
) -> JSONResponse:
    """Finds the firm by the email domain and answers the provider's authorization URL."""
    await set_platform_bypass(session)
    found = await _config_for_email(session, str(body.email))
    if found is None:
        raise HTTPException(
            404,
            {"error": "sso_not_available", "message": "Single sign-on is not set up for this email domain."},
        )
    firm, cfg = found
    check_feature(svc.snapshot(firm), "sso")  # an unlicensed or downgraded firm cannot start SSO
    try:
        disc = await discover(request.app.state.channels.http, cfg.issuer)
    except OidcError as e:
        raise HTTPException(502, {"error": e.code, "message": str(e)}) from e
    verifier, challenge = pkce_pair()
    state, nonce = secrets.token_urlsafe(32), secrets.token_urlsafe(24)
    redirect_to = (
        body.redirect_to
        if body.redirect_to.startswith("/") and not body.redirect_to.startswith("//")
        else "/inbox"
    )
    session.add(
        SsoLogin(
            firm_id=firm.id,
            state=state,
            nonce=nonce,
            code_verifier=verifier,
            redirect_to=redirect_to,
            expires_at=datetime.now(UTC) + LOGIN_TTL,
        )
    )
    await session.flush()
    params = {
        "response_type": "code",
        "client_id": cfg.client_id,
        "redirect_uri": _callback_url(request),
        "scope": "openid email profile",
        "state": state,
        "nonce": nonce,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "login_hint": str(body.email),
    }
    return JSONResponse({"authorization_url": f"{disc.authorization_endpoint}?{urlencode(params)}"})


def _web_url(settings: Any, path: str) -> str:
    base = (settings.cors_origins.split(",")[0].strip() if settings.cors_origins else "").rstrip("/")
    return f"{base}{path}"


@router.get("/auth/sso/callback")
async def sso_callback(
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    svc: LicenseServiceDep,
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    error: str | None = Query(default=None),
) -> Response:
    await set_platform_bypass(session)
    login = (
        (await session.execute(select(SsoLogin).where(SsoLogin.state == state))).scalar_one_or_none()
        if state
        else None
    )
    now = datetime.now(UTC)
    if login is None or login.used_at is not None or login.expires_at < now:
        return RedirectResponse(_web_url(settings, "/login?sso=invalid_state"), status_code=303)
    login.used_at = now
    firm = await session.get(Firm, login.firm_id)
    cfg = await session.get(FirmSso, login.firm_id)
    if firm is None or cfg is None or not cfg.enabled or error or not code:
        return RedirectResponse(_web_url(settings, "/login?sso=denied"), status_code=303)
    try:
        check_feature(svc.snapshot(firm), "sso")
        http = request.app.state.channels.http
        disc = await discover(http, cfg.issuer)
        secret = FirmSecrets(request.app.state.master_key, firm).open(
            "sso_client_secret", cfg.client_secret_enc or b""
        )
        token_resp = await http.post(
            disc.token_endpoint,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": _callback_url(request),
                "client_id": cfg.client_id,
                "client_secret": secret or "",
                "code_verifier": login.code_verifier,
            },
            headers={"accept": "application/json"},
            timeout=15,
        )
        if token_resp.status_code != 200:
            raise OidcError("token_exchange", "The identity provider refused the code.")
        id_token = token_resp.json().get("id_token")
        if not isinstance(id_token, str):
            raise OidcError("no_id_token", "No ID token was returned.")
        claims = verify_id_token(
            id_token,
            await fetch_jwks(http, disc.jwks_uri),
            issuer=disc.issuer,
            client_id=cfg.client_id,
            nonce=login.nonce,
        )
    except (OidcError, HTTPException) as e:
        code_ = e.code if isinstance(e, OidcError) else "license_required"
        await record_audit(
            session,
            firm_id=firm.id,
            actor=None,
            action="auth.sso",
            target_type="firm",
            target_id=str(firm.id),
            result=code_,
        )
        return RedirectResponse(_web_url(settings, f"/login?sso={code_}"), status_code=303)
    email = str(claims.get("email") or "").lower()
    domain = email.rsplit("@", 1)[-1] if "@" in email else ""
    if not email or claims.get("email_verified") is False or domain not in (cfg.allowed_domains or []):
        await record_audit(
            session,
            firm_id=firm.id,
            actor=None,
            action="auth.sso",
            target_type="user",
            target_id=email[:120],
            result="email_not_allowed",
        )
        return RedirectResponse(_web_url(settings, "/login?sso=email_not_allowed"), status_code=303)
    user = (await session.execute(select(User).where(User.email == email))).scalar_one_or_none()
    if user is not None and (
        user.firm_id != firm.id or not user.is_active or user.role not in FIRM_STAFF_ROLES
    ):
        await record_audit(
            session,
            firm_id=firm.id,
            actor=None,
            action="auth.sso",
            target_type="user",
            target_id=email[:120],
            result="denied",
        )
        return RedirectResponse(_web_url(settings, "/login?sso=denied"), status_code=303)
    if user is None:
        if cfg.provisioning != "auto":
            await record_audit(
                session,
                firm_id=firm.id,
                actor=None,
                action="auth.sso",
                target_type="user",
                target_id=email[:120],
                result="not_invited",
            )
            return RedirectResponse(_web_url(settings, "/login?sso=not_invited"), status_code=303)
        user = User(
            firm_id=firm.id,
            email=email,
            display_name=str(claims.get("name") or "")[:200],
            password_hash="!sso",  # noqa: S106 - no password sign-in for provisioned users
            role=Role(cfg.default_role),
            locale=firm.locale,
        )
        session.add(user)
        await session.flush()
        await record_audit(
            session,
            firm_id=firm.id,
            actor=None,
            action="user.sso_provision",
            target_type="user",
            target_id=str(user.id),
            after={"role": cfg.default_role},
        )
    await set_tenant(session, firm.id)
    token = await create_session(session, user, settings.session_ttl_seconds, mfa_passed=True)
    user.last_login_at = now
    await record_audit(
        session,
        firm_id=firm.id,
        actor=Actor.from_user(user),
        action="auth.login",
        target_type="user",
        target_id=str(user.id),
        after={"method": "sso", "sub": str(claims.get("sub"))[:120]},
    )
    response = RedirectResponse(_web_url(settings, login.redirect_to or "/inbox"), status_code=303)
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=settings.session_ttl_seconds,
        httponly=True,
        secure=settings.cookie_secure_effective,
        samesite="lax",
        path="/",
    )
    return response


_ = uuid
