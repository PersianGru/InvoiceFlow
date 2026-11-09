"""API keys (firm owner) and the OAuth2 client-credentials token endpoint.

Issuing a key is a point of use of the ``api`` entitlement: ``402 license_required``
without it. Keys are shown once; only a digest is stored. Revocation is immediate for the key and
for every access token derived from it.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.api.deps import FirmDep, LicenseServiceDep, SessionDep, SettingsDep, require
from app.api.principals import key_usable, load_key
from app.audit.service import record_audit
from app.auth.api_keys import (
    KEY_PREFIX,
    generate_key,
    issue_access_token,
    mask,
    secret_matches,
    split_key,
)
from app.db.rls import set_platform_bypass
from app.licensing.gate import LicenseRequired, check_feature
from app.models.firm import Firm
from app.models.integration import API_SCOPES, ApiKey
from app.tenancy.context import Actor

router = APIRouter(tags=["api-keys"])

MAX_KEYS = 50


class ApiKeyIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    scopes: list[str] = Field(default_factory=lambda: ["documents:read", "documents:write", "exports:read"])
    rate_limit_per_minute: int = Field(default=120, ge=1, le=6000)
    expires_at: datetime | None = None
    client_company_ids: list[uuid.UUID] = Field(default_factory=list, max_length=500)
    allowed_cidrs: list[str] = Field(default_factory=list, max_length=50)
    description: str = Field(default="", max_length=300)


class ApiKeyPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    scopes: list[str] | None = None
    rate_limit_per_minute: int | None = Field(default=None, ge=1, le=6000)
    expires_at: datetime | None = None
    client_company_ids: list[uuid.UUID] | None = Field(default=None, max_length=500)
    allowed_cidrs: list[str] | None = Field(default=None, max_length=50)
    description: str | None = Field(default=None, max_length=300)


class ApiKeyOut(BaseModel):
    id: str
    name: str
    key_hint: str
    scopes: list[str]
    rate_limit_per_minute: int
    created_at: str
    last_used_at: str | None
    expires_at: str | None
    revoked_at: str | None
    client_company_ids: list[str] = []
    allowed_cidrs: list[str] = []
    description: str = ""
    last_used_ip: str | None = None


class ApiKeyCreated(ApiKeyOut):
    key: str  # shown once


def key_out(k: ApiKey) -> ApiKeyOut:
    return ApiKeyOut(
        id=str(k.id),
        name=k.name,
        key_hint=mask(k.key_id),
        scopes=list(k.scopes or []),
        rate_limit_per_minute=k.rate_limit_per_minute,
        created_at=k.created_at.isoformat() if k.created_at else "",
        last_used_at=k.last_used_at.isoformat() if k.last_used_at else None,
        expires_at=k.expires_at.isoformat() if k.expires_at else None,
        revoked_at=k.revoked_at.isoformat() if k.revoked_at else None,
        client_company_ids=list(k.client_company_ids or []),
        allowed_cidrs=list(k.allowed_cidrs or []),
        description=k.description or "",
        last_used_ip=k.last_used_ip,
    )


def _cidrs(values: list[str]) -> list[str]:
    import ipaddress

    out = []
    for v in values:
        try:
            out.append(str(ipaddress.ip_network(v.strip(), strict=False)))
        except ValueError as e:
            raise HTTPException(
                422, {"error": "invalid_request", "message": f"Not an IP or network: {v}"}
            ) from e
    return out


async def _clients(session: Any, ids: list[uuid.UUID]) -> list[str]:
    from app.models.client_company import ClientCompany

    for cid in ids:
        if await session.get(ClientCompany, cid) is None:
            raise HTTPException(404, {"error": "not_found", "message": "Client company not found."})
    return sorted(str(c) for c in ids)


@router.get("/api-keys", response_model=list[ApiKeyOut])
async def list_keys(
    session: SessionDep, actor: Actor = Depends(require("api_keys.manage"))
) -> list[ApiKeyOut]:
    rows = (await session.execute(select(ApiKey).order_by(ApiKey.created_at.desc()))).scalars().all()
    return [key_out(k) for k in rows]


@router.post("/api-keys", response_model=ApiKeyCreated, status_code=201)
async def create_key(
    body: ApiKeyIn,
    session: SessionDep,
    firm: FirmDep,
    svc: LicenseServiceDep,
    actor: Actor = Depends(require("api_keys.manage")),
) -> ApiKeyCreated:
    check_feature(svc.snapshot(firm), "api")  # point of use of the entitlement
    scopes = sorted({s for s in body.scopes if s in API_SCOPES})
    if not scopes:
        raise HTTPException(422, {"error": "invalid_request", "message": "Pick at least one known scope."})
    count = (
        await session.execute(select(func.count()).select_from(ApiKey).where(ApiKey.revoked_at.is_(None)))
    ).scalar_one()
    if count >= MAX_KEYS:
        raise HTTPException(422, {"error": "too_many", "message": f"At most {MAX_KEYS} active keys."})
    new = generate_key()
    k = ApiKey(
        firm_id=firm.id,
        key_id=new.key_id,
        secret_hash=new.secret_hash,
        name=body.name.strip(),
        scopes=scopes,
        rate_limit_per_minute=body.rate_limit_per_minute,
        created_by_user_id=actor.user_id,
        expires_at=body.expires_at,
        client_company_ids=await _clients(session, body.client_company_ids),
        allowed_cidrs=_cidrs(body.allowed_cidrs),
        description=body.description.strip(),
    )
    session.add(k)
    await session.flush()
    await record_audit(
        session,
        firm_id=firm.id,
        actor=actor,
        action="api_key.create",
        target_type="api_key",
        target_id=str(k.id),
        after={
            "name": k.name,
            "scopes": scopes,
            "key_hint": mask(k.key_id),
            "client_company_ids": k.client_company_ids,
            "allowed_cidrs": k.allowed_cidrs,
        },
    )
    return ApiKeyCreated(**key_out(k).model_dump(), key=new.plain)


@router.patch("/api-keys/{key_id}", response_model=ApiKeyOut)
async def update_key(
    key_id: uuid.UUID,
    body: ApiKeyPatch,
    session: SessionDep,
    firm: FirmDep,
    actor: Actor = Depends(require("api_keys.manage")),
) -> ApiKeyOut:
    k = await session.get(ApiKey, key_id)
    if k is None or k.revoked_at is not None:
        raise HTTPException(404, {"error": "not_found", "message": "API key not found."})
    before = key_out(k).model_dump()
    if body.name is not None:
        k.name = body.name.strip()
    if body.scopes is not None:
        scopes = sorted({s for s in body.scopes if s in API_SCOPES})
        if not scopes:
            raise HTTPException(
                422, {"error": "invalid_request", "message": "Pick at least one known scope."}
            )
        k.scopes = scopes
    if body.rate_limit_per_minute is not None:
        k.rate_limit_per_minute = body.rate_limit_per_minute
    if "expires_at" in body.model_fields_set:
        k.expires_at = body.expires_at
    if body.client_company_ids is not None:
        k.client_company_ids = await _clients(session, body.client_company_ids)
    if body.allowed_cidrs is not None:
        k.allowed_cidrs = _cidrs(body.allowed_cidrs)
    if body.description is not None:
        k.description = body.description.strip()
    await record_audit(
        session,
        firm_id=firm.id,
        actor=actor,
        action="api_key.update",
        target_type="api_key",
        target_id=str(k.id),
        before={
            x: before[x]
            for x in ("name", "scopes", "client_company_ids", "allowed_cidrs", "rate_limit_per_minute")
        },
        after={
            "name": k.name,
            "scopes": k.scopes,
            "client_company_ids": k.client_company_ids,
            "allowed_cidrs": k.allowed_cidrs,
            "rate_limit_per_minute": k.rate_limit_per_minute,
        },
    )
    return key_out(k)


@router.post("/api-keys/{key_id}/rotate", response_model=ApiKeyCreated)
async def rotate_key(
    key_id: uuid.UUID, session: SessionDep, firm: FirmDep, actor: Actor = Depends(require("api_keys.manage"))
) -> ApiKeyCreated:
    """A new secret with the same settings; the old key stops working at once."""
    old = await session.get(ApiKey, key_id)
    if old is None or old.revoked_at is not None:
        raise HTTPException(404, {"error": "not_found", "message": "API key not found."})
    new = generate_key()
    k = ApiKey(
        firm_id=firm.id,
        key_id=new.key_id,
        secret_hash=new.secret_hash,
        name=old.name,
        scopes=list(old.scopes or []),
        rate_limit_per_minute=old.rate_limit_per_minute,
        created_by_user_id=actor.user_id,
        expires_at=old.expires_at,
        client_company_ids=list(old.client_company_ids or []),
        allowed_cidrs=list(old.allowed_cidrs or []),
        description=old.description,
    )
    old.revoked_at = datetime.now(UTC)
    session.add(k)
    await session.flush()
    await record_audit(
        session,
        firm_id=firm.id,
        actor=actor,
        action="api_key.rotate",
        target_type="api_key",
        target_id=str(k.id),
        before={"key": str(old.id), "key_hint": mask(old.key_id)},
        after={"key_hint": mask(k.key_id)},
    )
    return ApiKeyCreated(**key_out(k).model_dump(), key=new.plain)


@router.delete("/api-keys/{key_id}", response_model=ApiKeyOut)
async def revoke_key(
    key_id: uuid.UUID, session: SessionDep, firm: FirmDep, actor: Actor = Depends(require("api_keys.manage"))
) -> ApiKeyOut:
    k = await session.get(ApiKey, key_id)
    if k is None:
        raise HTTPException(404, {"error": "not_found", "message": "API key not found."})
    if k.revoked_at is None:
        k.revoked_at = datetime.now(UTC)
        await record_audit(
            session,
            firm_id=firm.id,
            actor=actor,
            action="api_key.revoke",
            target_type="api_key",
            target_id=str(k.id),
        )
    return key_out(k)


# ---- OAuth2 client credentials ---------------------------------------------------------------------
def _oauth_error(code: str, description: str, status: int = 400) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"error": code, "error_description": description, "message": description},
        headers={"cache-control": "no-store", "pragma": "no-cache"},
    )


@router.post("/oauth/token")
async def oauth_token(
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    svc: LicenseServiceDep,
    grant_type: str | None = Form(default=None),
    client_id: str | None = Form(default=None),
    client_secret: str | None = Form(default=None),
    scope: str | None = Form(default=None),
) -> JSONResponse:
    """RFC 6749 §4.4: ``grant_type=client_credentials`` with the API key as client id/secret
    (form body, HTTP Basic, or JSON). Returns a bearer token valid for ``API_ACCESS_TOKEN_TTL_SECONDS``."""
    body: dict[str, Any] = {}
    if request.headers.get("content-type", "").startswith("application/json"):
        try:
            body = await request.json()
        except ValueError:
            body = {}
    grant_type = grant_type or body.get("grant_type")
    client_id = client_id or body.get("client_id")
    client_secret = client_secret or body.get("client_secret")
    scope = scope or body.get("scope")
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("basic "):
        import base64

        try:
            decoded = base64.b64decode(auth[6:]).decode()
            client_id, client_secret = decoded.split(":", 1)
        except (ValueError, UnicodeDecodeError):
            return _oauth_error("invalid_client", "Malformed Basic credentials.", 401)
    if grant_type != "client_credentials":
        return _oauth_error("unsupported_grant_type", "Only client_credentials is supported.")
    cid, csec = str(client_id or ""), str(client_secret or "")
    if cid.startswith(KEY_PREFIX) and csec:
        plain = f"{cid}_{csec}"  # client_id = "ivfk_<id>", client_secret = "<secret>"
    elif cid.startswith(KEY_PREFIX):
        plain = cid  # the whole key as client_id
    elif cid and csec:
        plain = f"{KEY_PREFIX}{cid}_{csec}"  # client_id = "<id>"
    else:
        plain = ""
    parts = split_key(plain)
    if parts is None:
        return _oauth_error(
            "invalid_client", "Use the API key id as client_id and its secret as client_secret.", 401
        )
    await set_platform_bypass(session)
    key = await load_key(session, parts[0])
    now = datetime.now(UTC)
    if key is None or not secret_matches(parts[1], key.secret_hash) or not key_usable(key, now):
        return _oauth_error("invalid_client", "Unknown or revoked API key.", 401)
    firm = await session.get(Firm, key.firm_id)
    if firm is None:
        return _oauth_error("invalid_client", "Unknown firm.", 401)
    try:
        check_feature(svc.snapshot(firm), "api")
    except LicenseRequired as e:
        raw: Any = e.detail
        detail: dict[str, Any] = raw if isinstance(raw, dict) else {}
        return JSONResponse(status_code=402, content=detail, headers={"cache-control": "no-store"})
    wanted = [s for s in (scope or "").split() if s]
    if any(s not in (key.scopes or []) for s in wanted):
        return _oauth_error("invalid_scope", "The key does not carry every requested scope.")
    token, exp = issue_access_token(settings.secret_key, key.key_id, settings.api_access_token_ttl_seconds)
    await record_audit(
        session,
        firm_id=firm.id,
        actor=None,
        action="oauth.token",
        target_type="api_key",
        target_id=str(key.id),
    )
    return JSONResponse(
        content={
            "access_token": token,
            "token_type": "Bearer",
            "expires_in": settings.api_access_token_ttl_seconds,
            "scope": " ".join(wanted or key.scopes or []),
        },
        headers={"cache-control": "no-store", "pragma": "no-cache"},
    )
