"""Public-API principals: resolve an API key (``x-api-key`` header or ``Authorization: Bearer
ivfk_…``) or an OAuth access token (``Bearer ivfat_…``) to an ``Actor``.

Every API-key request re-checks the key (revoked/expired), the firm's license (core + ``api``,
else ``402 license_required``) and the per-key rate limit (``429`` with ``Retry-After``).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.api_keys import KEY_PREFIX, TOKEN_PREFIX, secret_matches, split_key, verify_access_token
from app.config import Settings
from app.db.rls import set_client_scope, set_platform_bypass, set_tenant
from app.licensing.gate import check_feature
from app.licensing.service import LicenseService
from app.models.firm import Firm
from app.models.integration import ApiKey
from app.models.user import Role
from app.tenancy.context import Actor


def api_credential(request: Request) -> str | None:
    """The raw credential when the request carries a public-API credential, else None."""
    header = request.headers.get("x-api-key")
    if header:
        return header.strip()
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        token = auth[7:].strip()
        if token.startswith((KEY_PREFIX, TOKEN_PREFIX)):
            return token
    return None


def _unauth(message: str) -> HTTPException:
    return HTTPException(401, {"error": "unauthenticated", "message": message})


async def load_key(session: AsyncSession, key_id: str) -> ApiKey | None:
    return (await session.execute(select(ApiKey).where(ApiKey.key_id == key_id))).scalar_one_or_none()


def key_usable(key: ApiKey, now: datetime) -> bool:
    return key.revoked_at is None and (key.expires_at is None or key.expires_at > now)


async def resolve_api_actor(
    request: Request, session: AsyncSession, settings: Settings, service: LicenseService, credential: str
) -> Actor:
    now = datetime.now(UTC)
    await set_platform_bypass(session)
    if credential.startswith(TOKEN_PREFIX):
        key_id = verify_access_token(settings.secret_key, credential)
        if key_id is None:
            raise _unauth("The access token is invalid or expired.")
        key = await load_key(session, key_id)
        if key is None or not key_usable(key, now):
            raise _unauth("The API key behind this token is revoked.")
    else:
        parts = split_key(credential)
        if parts is None:
            raise _unauth("The API key is malformed.")
        key = await load_key(session, parts[0])
        if key is None or not secret_matches(parts[1], key.secret_hash) or not key_usable(key, now):
            raise _unauth("The API key is invalid or revoked.")
    firm = await session.get(Firm, key.firm_id)
    if firm is None or not firm.is_active:
        raise _unauth("The firm behind this key is not active.")
    check_feature(service.snapshot(firm), "api")  # 402 license_required for unlicensed firms / no api
    limiter = request.app.state.rate_limiter
    decision = limiter.check(
        f"key:{key.id}", key.rate_limit_per_minute or settings.api_default_rate_limit_per_minute
    )
    request.state.ratelimit_headers = decision.headers()
    if not decision.allowed:
        raise HTTPException(
            429,
            {"error": "rate_limited", "message": "Too many requests for this API key. Slow down."},
            headers=decision.headers(),
        )
    if key.last_used_at is None or now - key.last_used_at > timedelta(seconds=60):
        key.last_used_at = now
    ip = request.client.host if request.client else None
    if key.allowed_cidrs and not _ip_allowed(ip, key.allowed_cidrs):
        await set_tenant(session, firm.id)
        from app.audit.service import record_audit

        await record_audit(
            session,
            firm_id=firm.id,
            actor=None,
            action="api_key.denied_ip",
            target_type="api_key",
            target_id=str(key.id),
            after={"ip": ip},
            result="denied",
        )
        raise HTTPException(
            403, {"error": "ip_not_allowed", "message": "This API key is not allowed from this address."}
        )
    key.last_used_ip = ip
    scope = frozenset(uuid.UUID(c) for c in (key.client_company_ids or []))
    if scope:
        await set_client_scope(session, scope)
    await set_tenant(session, firm.id)
    actor = Actor(
        user_id=key.id,
        role=Role.api_key,
        firm_id=firm.id,
        client_company_id=None,
        email=f"api-key:{key.name}",
        ip=request.client.host if request.client else None,
        api_key_id=key.id,
        scopes=frozenset(key.scopes or []),
        client_scope=scope,
    )
    request.state.actor = actor
    return actor


def _ip_allowed(ip: str | None, cidrs: list[str]) -> bool:
    import ipaddress

    if not ip:
        return False
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    for c in cidrs:
        try:
            if addr in ipaddress.ip_network(c, strict=False):
                return True
        except ValueError:
            continue
    return False


def key_uuid(value: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(value)
    except ValueError:
        return None
