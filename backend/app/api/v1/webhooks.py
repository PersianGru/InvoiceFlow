"""Webhook endpoints, deliveries, test pings. Creating an endpoint is a point of use of the
``api`` entitlement. Secrets are shown once and stored encrypted with the firm's data key."""

from __future__ import annotations

import ipaddress
import uuid
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import FirmDep, LicenseServiceDep, SessionDep, SettingsDep, require
from app.audit.service import record_audit
from app.config import Settings
from app.licensing.gate import check_feature
from app.licensing.store import FirmSecrets
from app.models.integration import WEBHOOK_EVENTS, WebhookDelivery, WebhookEndpoint
from app.tenancy.context import Actor
from app.webhooks import emit_event, new_secret

router = APIRouter(prefix="/webhooks", tags=["webhooks"])

MAX_ENDPOINTS = 20
LOOPBACK = ("localhost", "127.0.0.1", "::1")


def validate_url(url: str, settings: Settings) -> str:
    parts = urlsplit(url.strip())
    host = (parts.hostname or "").lower()
    if parts.scheme not in ("http", "https") or not host or parts.username or parts.password:
        raise HTTPException(
            422, {"error": "invalid_url", "message": "Use an https:// URL without credentials."}
        )
    if parts.scheme == "http" and (settings.is_production or host not in LOOPBACK):
        raise HTTPException(422, {"error": "invalid_url", "message": "Webhook URLs must use https://."})
    if settings.is_production:
        try:
            ip = ipaddress.ip_address(host)
            private = ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
        except ValueError:
            private = host in LOOPBACK or host.endswith((".local", ".internal", ".localhost"))
        if private:
            raise HTTPException(
                422, {"error": "invalid_url", "message": "Private and loopback hosts are refused."}
            )
    return url.strip()[:1024]


class EndpointIn(BaseModel):
    url: str = Field(min_length=8, max_length=1024)
    description: str = Field(default="", max_length=200)
    events: list[str] = Field(default_factory=lambda: list(WEBHOOK_EVENTS))
    active: bool = True


class EndpointOut(BaseModel):
    id: str
    url: str
    description: str
    events: list[str]
    active: bool
    consecutive_failures: int
    disabled_at: str | None
    disabled_reason: str | None
    created_at: str


class EndpointCreated(EndpointOut):
    secret: str  # shown once


class DeliveryOut(BaseModel):
    id: str
    endpoint_id: str
    event_id: str
    event_type: str
    status: str
    attempts: int
    next_attempt_at: str | None
    last_attempt_at: str | None
    last_status_code: int | None
    last_error: str | None
    delivered_at: str | None
    created_at: str
    payload: dict[str, Any]


def endpoint_out(e: WebhookEndpoint) -> EndpointOut:
    return EndpointOut(
        id=str(e.id),
        url=e.url,
        description=e.description,
        events=list(e.events or []),
        active=e.active,
        consecutive_failures=e.consecutive_failures,
        disabled_at=e.disabled_at.isoformat() if e.disabled_at else None,
        disabled_reason=e.disabled_reason,
        created_at=e.created_at.isoformat() if e.created_at else "",
    )


def delivery_out(d: WebhookDelivery) -> DeliveryOut:
    return DeliveryOut(
        id=str(d.id),
        endpoint_id=str(d.endpoint_id),
        event_id=str(d.event_id),
        event_type=d.event_type,
        status=d.status,
        attempts=d.attempts,
        next_attempt_at=d.next_attempt_at.isoformat() if d.next_attempt_at else None,
        last_attempt_at=d.last_attempt_at.isoformat() if d.last_attempt_at else None,
        last_status_code=d.last_status_code,
        last_error=d.last_error,
        delivered_at=d.delivered_at.isoformat() if d.delivered_at else None,
        created_at=d.created_at.isoformat() if d.created_at else "",
        payload=dict(d.payload or {}),
    )


def _events(events: list[str]) -> list[str]:
    cleaned = sorted({e for e in events if e in WEBHOOK_EVENTS})
    if not cleaned:
        raise HTTPException(
            422, {"error": "invalid_request", "message": "Subscribe to at least one known event."}
        )
    return cleaned


async def _get(session: SessionDep, endpoint_id: uuid.UUID) -> WebhookEndpoint:
    e = await session.get(WebhookEndpoint, endpoint_id)
    if e is None:
        raise HTTPException(404, {"error": "not_found", "message": "Webhook endpoint not found."})
    return e


@router.get("", response_model=list[EndpointOut])
async def list_endpoints(
    session: SessionDep, actor: Actor = Depends(require("webhooks.manage"))
) -> list[EndpointOut]:
    rows = (
        (await session.execute(select(WebhookEndpoint).order_by(WebhookEndpoint.created_at))).scalars().all()
    )
    return [endpoint_out(e) for e in rows]


@router.post("", response_model=EndpointCreated, status_code=201)
async def create_endpoint(
    request: Request,
    body: EndpointIn,
    session: SessionDep,
    firm: FirmDep,
    svc: LicenseServiceDep,
    settings: SettingsDep,
    actor: Actor = Depends(require("webhooks.manage")),
) -> EndpointCreated:
    check_feature(svc.snapshot(firm), "api")
    url = validate_url(body.url, settings)
    count = len((await session.execute(select(WebhookEndpoint.id))).all())
    if count >= MAX_ENDPOINTS:
        raise HTTPException(422, {"error": "too_many", "message": f"At most {MAX_ENDPOINTS} endpoints."})
    secret = new_secret()
    secrets_ = FirmSecrets(request.app.state.master_key, firm)
    e = WebhookEndpoint(
        firm_id=firm.id,
        url=url,
        description=body.description.strip(),
        events=_events(body.events),
        secret_enc=secrets_.seal("webhook_secret", secret),
        active=body.active,
        created_by_user_id=actor.user_id,
    )
    session.add(e)
    await session.flush()
    await record_audit(
        session,
        firm_id=firm.id,
        actor=actor,
        action="webhook.create",
        target_type="webhook_endpoint",
        target_id=str(e.id),
        after={"url": url, "events": e.events},
    )
    return EndpointCreated(**endpoint_out(e).model_dump(), secret=secret)


@router.patch("/{endpoint_id}", response_model=EndpointOut)
async def update_endpoint(
    endpoint_id: uuid.UUID,
    body: EndpointIn,
    session: SessionDep,
    firm: FirmDep,
    settings: SettingsDep,
    actor: Actor = Depends(require("webhooks.manage")),
) -> EndpointOut:
    e = await _get(session, endpoint_id)
    e.url = validate_url(body.url, settings)
    e.description = body.description.strip()
    e.events = _events(body.events)
    if body.active and not e.active:
        e.active, e.disabled_at, e.disabled_reason, e.consecutive_failures = True, None, None, 0
    elif not body.active and e.active:
        e.active, e.disabled_at, e.disabled_reason = False, datetime.now(UTC), "by_user"
    await record_audit(
        session,
        firm_id=firm.id,
        actor=actor,
        action="webhook.update",
        target_type="webhook_endpoint",
        target_id=str(e.id),
    )
    return endpoint_out(e)


@router.delete("/{endpoint_id}", status_code=204)
async def delete_endpoint(
    endpoint_id: uuid.UUID,
    session: SessionDep,
    firm: FirmDep,
    actor: Actor = Depends(require("webhooks.manage")),
) -> None:
    e = await _get(session, endpoint_id)
    await session.delete(e)
    await record_audit(
        session,
        firm_id=firm.id,
        actor=actor,
        action="webhook.delete",
        target_type="webhook_endpoint",
        target_id=str(endpoint_id),
    )


@router.post("/{endpoint_id}/rotate-secret", response_model=EndpointCreated)
async def rotate_secret(
    request: Request,
    endpoint_id: uuid.UUID,
    session: SessionDep,
    firm: FirmDep,
    actor: Actor = Depends(require("webhooks.manage")),
) -> EndpointCreated:
    e = await _get(session, endpoint_id)
    secret = new_secret()
    e.secret_enc = FirmSecrets(request.app.state.master_key, firm).seal("webhook_secret", secret)
    await record_audit(
        session,
        firm_id=firm.id,
        actor=actor,
        action="webhook.rotate_secret",
        target_type="webhook_endpoint",
        target_id=str(e.id),
    )
    return EndpointCreated(**endpoint_out(e).model_dump(), secret=secret)


@router.post("/{endpoint_id}/test", response_model=DeliveryOut, status_code=201)
async def test_endpoint(
    request: Request,
    endpoint_id: uuid.UUID,
    session: SessionDep,
    firm: FirmDep,
    svc: LicenseServiceDep,
    actor: Actor = Depends(require("webhooks.manage")),
) -> DeliveryOut:
    check_feature(svc.snapshot(firm), "api")
    e = await _get(session, endpoint_id)
    n = await emit_event(
        session,
        firm.id,
        None,
        "ping",
        {"message": "InvoiceFlow webhook test", "endpoint_id": str(e.id)},
        only_endpoint=e.id,
    )
    if n == 0:
        raise HTTPException(409, {"error": "inactive", "message": "The endpoint is disabled."})
    d = (
        await session.execute(
            select(WebhookDelivery)
            .where(WebhookDelivery.endpoint_id == e.id, WebhookDelivery.event_type == "ping")
            .order_by(WebhookDelivery.created_at.desc())
            .limit(1)
        )
    ).scalar_one()
    queue = request.app.state.queue
    if queue is not None:
        await queue.kick_webhooks()
    return delivery_out(d)


@router.get("/{endpoint_id}/deliveries", response_model=list[DeliveryOut])
async def list_deliveries(
    endpoint_id: uuid.UUID,
    session: SessionDep,
    actor: Actor = Depends(require("webhooks.manage")),
    limit: int = Query(default=50, ge=1, le=200),
) -> list[DeliveryOut]:
    await _get(session, endpoint_id)
    rows = (
        (
            await session.execute(
                select(WebhookDelivery)
                .where(WebhookDelivery.endpoint_id == endpoint_id)
                .order_by(WebhookDelivery.created_at.desc())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return [delivery_out(d) for d in rows]


@router.post("/{endpoint_id}/deliveries/{delivery_id}/retry", response_model=DeliveryOut)
async def retry_delivery(
    request: Request,
    endpoint_id: uuid.UUID,
    delivery_id: uuid.UUID,
    session: SessionDep,
    firm: FirmDep,
    actor: Actor = Depends(require("webhooks.manage")),
) -> DeliveryOut:
    await _get(session, endpoint_id)
    d = await session.get(WebhookDelivery, delivery_id)
    if d is None or d.endpoint_id != endpoint_id:
        raise HTTPException(404, {"error": "not_found", "message": "Delivery not found."})
    d.status, d.next_attempt_at, d.attempts = "pending", datetime.now(UTC), 0
    await record_audit(
        session,
        firm_id=firm.id,
        actor=actor,
        action="webhook.retry",
        target_type="webhook_delivery",
        target_id=str(d.id),
    )
    queue = request.app.state.queue
    if queue is not None:
        await queue.kick_webhooks()
    return delivery_out(d)
