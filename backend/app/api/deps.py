"""Request-scoped dependencies: one transaction per request, authentication, tenant context, RBAC.

The transaction is committed on success and on *handled refusals* (``LicenseError``,
``HTTPException``) so the audit row written before a refusal is kept; any other exception rolls back.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Annotated

from fastapi import Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.principals import api_credential, resolve_api_actor
from app.auth.rbac import SCOPE_FOR_PERMISSION, allowed
from app.auth.sessions import COOKIE_NAME, resolve_session
from app.config import Settings
from app.db.rls import set_platform_bypass, set_tenant
from app.db.session import get_sessionmaker
from app.licensing.service import LicenseError, LicenseService
from app.mail import Mailer
from app.models.firm import Firm
from app.models.user import Role, User
from app.settings_store import SettingsStore
from app.tenancy.context import Actor


def get_settings_dep(request: Request) -> Settings:
    return request.app.state.settings  # type: ignore[no-any-return]


def get_store(request: Request) -> SettingsStore:
    return request.app.state.settings_store  # type: ignore[no-any-return]


def get_mailer(request: Request) -> Mailer:
    return request.app.state.mailer  # type: ignore[no-any-return]


def get_license_service(request: Request) -> LicenseService:
    return request.app.state.license_service  # type: ignore[no-any-return]


async def get_session() -> AsyncIterator[AsyncSession]:
    async with get_sessionmaker()() as session:
        await session.begin()
        try:
            yield session
        except (LicenseError, HTTPException):
            await session.commit()
            raise
        except BaseException:
            await session.rollback()
            raise
        else:
            await session.commit()


SessionDep = Annotated[AsyncSession, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings_dep)]
LicenseServiceDep = Annotated[LicenseService, Depends(get_license_service)]
StoreDep = Annotated[SettingsStore, Depends(get_store)]
MailerDep = Annotated[Mailer, Depends(get_mailer)]


def _token_from_request(request: Request) -> str | None:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return request.cookies.get(COOKIE_NAME)


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


NO_FIRM = uuid.UUID(int=0)

# Paths an account may use before its 2FA is set up (mandatory 2FA)
MFA_SETUP_PATHS = frozenset(
    {
        "/api/v1/auth/me",
        "/api/v1/auth/logout",
        "/api/v1/users/me/totp/setup",
        "/api/v1/users/me/totp/confirm",
        "/api/v1/firms/me/current",
    }
)


async def current_user(request: Request, session: SessionDep) -> User:
    token = _token_from_request(request)
    if not token:
        raise HTTPException(401, {"error": "unauthenticated", "message": "Sign in first."})
    await set_platform_bypass(session)
    found = await resolve_session(session, token)
    if found is None:
        raise HTTPException(
            401, {"error": "unauthenticated", "message": "Your session has ended. Sign in again."}
        )
    user, row = found
    settings: Settings = request.app.state.settings
    if settings.mfa_enforced and not row.mfa_passed and request.url.path not in MFA_SETUP_PATHS:
        raise HTTPException(
            403,
            {
                "error": "mfa_setup_required",
                "message": "Set up two-factor authentication first.",
            },
        )
    if user.role == Role.super_admin:
        # inside a firm: that firm's tenant context; in the platform view: no firm at all (the nil id
        # matches no row), so a firm route never mixes data of several firms. Platform routes set the
        # bypass themselves.
        await set_tenant(session, row.acting_firm_id or NO_FIRM)
    else:
        assert user.firm_id is not None
        firm = await session.get(Firm, user.firm_id)
        if firm is None or not firm.is_active or firm.deleted_at is not None:
            raise HTTPException(
                403,
                {
                    "error": "firm_inactive",
                    "message": "This firm is deactivated. Contact the platform admin.",
                },
            )
        await set_tenant(session, user.firm_id)
    request.state.user = user
    request.state.session_row = row
    return user


UserDep = Annotated[User, Depends(current_user)]


async def current_actor(
    request: Request, session: SessionDep, settings: SettingsDep, svc: LicenseServiceDep
) -> Actor:
    """Session principals (cookie or bearer session token) and public-API principals (API key or
    OAuth access token) both end as an ``Actor``; routes never see the difference except through
    ``actor.is_api_key`` and the scopes checked by ``require``."""
    credential = api_credential(request)
    if credential is not None:
        return await resolve_api_actor(request, session, settings, svc, credential)
    user = await current_user(request, session)
    row = request.state.session_row
    return Actor.from_user(user, _client_ip(request), row.acting_firm_id)


ActorDep = Annotated[Actor, Depends(current_actor)]


def require(permission: str) -> Callable[..., Awaitable[Actor]]:
    async def dep(actor: ActorDep) -> Actor:
        if not allowed(actor.role, permission):
            raise HTTPException(403, {"error": "forbidden", "message": "You are not allowed to do this."})
        if actor.is_api_key:
            scope = SCOPE_FOR_PERMISSION.get(permission)
            if scope is None or not actor.has_scope(scope):
                raise HTTPException(
                    403,
                    {
                        "error": "insufficient_scope",
                        "message": f"This API key lacks the scope '{scope or permission}'.",
                        "scope": scope,
                    },
                )
        return actor

    return dep


async def current_firm(actor: ActorDep, session: SessionDep) -> Firm:
    if actor.firm_id is None:
        if actor.is_platform_admin:
            raise HTTPException(
                409, {"error": "choose_firm", "message": "Choose a firm first (Platform → Firms → Open)."}
            )
        raise HTTPException(404, {"error": "no_firm", "message": "This account belongs to no firm."})
    firm = await session.get(Firm, actor.firm_id)
    if firm is None:
        raise HTTPException(404, {"error": "no_firm", "message": "Firm not found."})
    return firm


FirmDep = Annotated[Firm, Depends(current_firm)]


async def firm_by_path(firm_id: uuid.UUID, actor: ActorDep, session: SessionDep) -> Firm:
    """``/firms/{firm_id}/...``: a tenant may only name its own firm; the super admin any (the
    platform row only through the platform routes)."""
    if not actor.is_platform_admin and actor.firm_id != firm_id:
        raise HTTPException(404, {"error": "not_found", "message": "Firm not found."})
    if actor.is_platform_admin and actor.firm_id is None:
        await set_platform_bypass(session)
    firm = await session.get(Firm, firm_id)
    if firm is None or (firm.kind != "firm" and not actor.is_platform_admin):
        raise HTTPException(404, {"error": "not_found", "message": "Firm not found."})
    return firm


FirmPathDep = Annotated[Firm, Depends(firm_by_path)]
