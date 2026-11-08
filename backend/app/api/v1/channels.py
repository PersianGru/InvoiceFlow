"""Channel webhooks (platform-level, authenticated by provider secrets) and the firm's channel
settings: sender mappings, pairing codes, message log, quarantine release/discard."""

from __future__ import annotations

import hmac
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.api.deps import FirmDep, LicenseServiceDep, SessionDep, SettingsDep, require
from app.audit.service import record_audit
from app.channels.base import InboundMessage, normalise_identity
from app.channels.email import parse_inbound_json, parse_mime
from app.channels.intake import PAIRING_TTL, IntakeDeps, channel_status, handle_inbound, new_pairing_code
from app.channels.registry import ChannelHub
from app.db.rls import set_tenant
from app.licensing.gate import check_feature
from app.models.channel import CHANNELS, ChannelMessage, SenderMapping
from app.models.client_company import ClientCompany
from app.models.document import Document, DocumentStatus
from app.tenancy.context import Actor
from app.webhooks import document_payload, emit_event

router = APIRouter(tags=["channels"])


def _hub(request: Request) -> ChannelHub:
    return request.app.state.channels  # type: ignore[no-any-return]


def _deps(request: Request) -> IntakeDeps:
    st = request.app.state
    return IntakeDeps(
        settings=st.settings,
        storage=st.storage,
        scanner=st.scanner,
        service=st.license_service,
        queue=st.queue,
        transports=_hub(request).transports(),
    )


# ---- provider webhooks -----------------------------------------------------------------------------
@router.post("/channels/email/inbound")
async def email_inbound(request: Request, session: SessionDep, settings: SettingsDep) -> dict[str, Any]:
    """Inbound-email webhook: JSON (from, to, subject, text/html, attachments[]) or raw
    ``message/rfc822``. Authenticated with ``Authorization: Bearer <INBOUND_EMAIL_WEBHOOK_TOKEN>``."""
    token = settings.inbound_email_webhook_token or ""
    auth = request.headers.get("authorization", "")
    if (
        not token
        or not auth.lower().startswith("bearer ")
        or not hmac.compare_digest(token, auth[7:].strip())
    ):
        raise HTTPException(401, {"error": "unauthenticated", "message": "Invalid inbound-email token."})
    raw = await request.body()
    ctype = request.headers.get("content-type", "")
    if ctype.startswith("message/rfc822") or ctype.startswith("text/plain"):
        msg = parse_mime(raw)
    else:
        try:
            payload = await request.json()
        except ValueError as e:
            raise HTTPException(
                422, {"error": "invalid_request", "message": "Body must be JSON or message/rfc822."}
            ) from e
        msg = parse_inbound_json(payload)
    result = await handle_inbound(session, _deps(request), msg)
    return {"outcome": result.outcome, "documents": [str(d) for d in result.document_ids]}


@router.post("/channels/telegram/webhook")
async def telegram_webhook(request: Request, session: SessionDep) -> dict[str, Any]:
    hub = _hub(request)
    if hub.telegram is None:
        raise HTTPException(404, {"error": "not_found", "message": "Telegram is not configured."})
    if not hub.telegram.webhook_authentic(request.headers.get("x-telegram-bot-api-secret-token")):
        raise HTTPException(403, {"error": "forbidden", "message": "Bad webhook secret."})
    try:
        update = await request.json()
    except ValueError:
        return {"ok": True}
    msg = hub.telegram.parse_update(update) if isinstance(update, dict) else None
    if msg is None:
        return {"ok": True}
    await hub.telegram.fetch_files(msg)
    result = await handle_inbound(session, _deps(request), msg)
    return {"ok": True, "outcome": result.outcome}


@router.get("/channels/whatsapp/webhook")
async def whatsapp_verify(
    request: Request,
    hub_mode: str | None = Query(default=None, alias="hub.mode"),
    hub_verify_token: str | None = Query(default=None, alias="hub.verify_token"),
    hub_challenge: str | None = Query(default=None, alias="hub.challenge"),
) -> PlainTextResponse:
    hub = _hub(request)
    if hub.whatsapp is None:
        raise HTTPException(404, {"error": "not_found", "message": "WhatsApp is not configured."})
    challenge = hub.whatsapp.verify_challenge(hub_mode, hub_verify_token, hub_challenge)
    if challenge is None:
        raise HTTPException(403, {"error": "forbidden", "message": "Bad verify token."})
    return PlainTextResponse(challenge)


@router.post("/channels/whatsapp/webhook")
async def whatsapp_webhook(request: Request, session: SessionDep) -> dict[str, Any]:
    hub = _hub(request)
    if hub.whatsapp is None:
        raise HTTPException(404, {"error": "not_found", "message": "WhatsApp is not configured."})
    raw = await request.body()
    if not hub.whatsapp.signature_valid(request.headers.get("x-hub-signature-256"), raw):
        raise HTTPException(403, {"error": "forbidden", "message": "Bad signature."})
    try:
        payload = await request.json()
    except ValueError:
        return {"ok": True}
    outcomes = []
    for msg in hub.whatsapp.parse_notification(payload if isinstance(payload, dict) else {}):
        await hub.whatsapp.fetch_files(msg)
        outcomes.append((await handle_inbound(session, _deps(request), msg)).outcome)
    return {"ok": True, "outcomes": outcomes}


# ---- firm settings -----------------------------------------------------------------------------------
class MappingIn(BaseModel):
    client_company_id: uuid.UUID
    channel: str = Field(pattern="^(email|telegram|whatsapp)$")
    identity: str = Field(min_length=1, max_length=320)
    display_name: str = Field(default="", max_length=200)


class PairingIn(BaseModel):
    client_company_id: uuid.UUID
    channel: str = Field(pattern="^(telegram|whatsapp)$")
    display_name: str = Field(default="", max_length=200)


class MappingOut(BaseModel):
    id: str
    client_company_id: str
    channel: str
    identity: str | None
    display_name: str
    pairing_code: str | None
    pairing_expires_at: str | None
    paired_at: str | None
    last_seen_at: str | None
    created_at: str


def mapping_out(m: SenderMapping) -> MappingOut:
    return MappingOut(
        id=str(m.id),
        client_company_id=str(m.client_company_id),
        channel=m.channel,
        identity=m.identity,
        display_name=m.display_name,
        pairing_code=m.pairing_code,
        pairing_expires_at=m.pairing_expires_at.isoformat() if m.pairing_expires_at else None,
        paired_at=m.paired_at.isoformat() if m.paired_at else None,
        last_seen_at=m.last_seen_at.isoformat() if m.last_seen_at else None,
        created_at=m.created_at.isoformat() if m.created_at else "",
    )


@router.get("/channels/status")
async def status(
    request: Request, settings: SettingsDep, actor: Actor = Depends(require("clients.view"))
) -> dict[str, Any]:
    return channel_status(settings, _hub(request).transports())


@router.get("/sender-mappings", response_model=list[MappingOut])
async def list_mappings(
    session: SessionDep, actor: Actor = Depends(require("clients.view"))
) -> list[MappingOut]:
    rows = (
        (await session.execute(select(SenderMapping).order_by(SenderMapping.created_at.desc())))
        .scalars()
        .all()
    )
    return [mapping_out(m) for m in rows]


@router.post("/sender-mappings", response_model=MappingOut, status_code=201)
async def create_mapping(
    body: MappingIn, session: SessionDep, firm: FirmDep, actor: Actor = Depends(require("clients.manage"))
) -> MappingOut:
    if body.channel not in CHANNELS or await session.get(ClientCompany, body.client_company_id) is None:
        raise HTTPException(404, {"error": "not_found", "message": "Client company not found."})
    identity = normalise_identity(body.channel, body.identity)
    existing = (
        await session.execute(
            select(SenderMapping).where(
                SenderMapping.channel == body.channel, SenderMapping.identity == identity
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(409, {"error": "conflict", "message": "This sender is already mapped."})
    m = SenderMapping(
        firm_id=firm.id,
        client_company_id=body.client_company_id,
        channel=body.channel,
        identity=identity,
        display_name=body.display_name.strip(),
        paired_at=datetime.now(UTC),
        created_by_user_id=actor.user_id,
    )
    try:
        async with session.begin_nested():  # savepoint: a unique-index hit leaves the transaction usable
            session.add(m)
            await session.flush()
    except IntegrityError as e:  # the partial unique index guards other firms' identities (RLS hides them)
        raise HTTPException(409, {"error": "conflict", "message": "This sender is already mapped."}) from e
    await record_audit(
        session,
        firm_id=firm.id,
        actor=actor,
        action="sender_mapping.create",
        target_type="sender_mapping",
        target_id=str(m.id),
        after={"channel": body.channel, "client_company_id": str(body.client_company_id)},
    )
    return mapping_out(m)


@router.post("/sender-mappings/pairing-code", response_model=MappingOut, status_code=201)
async def create_pairing_code(
    body: PairingIn,
    session: SessionDep,
    firm: FirmDep,
    svc: LicenseServiceDep,
    actor: Actor = Depends(require("clients.manage")),
) -> MappingOut:
    check_feature(svc.snapshot(firm), "channels")
    if await session.get(ClientCompany, body.client_company_id) is None:
        raise HTTPException(404, {"error": "not_found", "message": "Client company not found."})
    m = SenderMapping(
        firm_id=firm.id,
        client_company_id=body.client_company_id,
        channel=body.channel,
        identity=None,
        display_name=body.display_name.strip(),
        pairing_code=new_pairing_code(),
        pairing_expires_at=datetime.now(UTC) + PAIRING_TTL,
        created_by_user_id=actor.user_id,
    )
    session.add(m)
    await session.flush()
    await record_audit(
        session,
        firm_id=firm.id,
        actor=actor,
        action="sender_mapping.pairing_code",
        target_type="sender_mapping",
        target_id=str(m.id),
        after={"channel": body.channel},
    )
    return mapping_out(m)


@router.delete("/sender-mappings/{mapping_id}", status_code=204)
async def delete_mapping(
    mapping_id: uuid.UUID,
    session: SessionDep,
    firm: FirmDep,
    actor: Actor = Depends(require("clients.manage")),
) -> None:
    m = await session.get(SenderMapping, mapping_id)
    if m is None:
        raise HTTPException(404, {"error": "not_found", "message": "Mapping not found."})
    await session.delete(m)
    await record_audit(
        session,
        firm_id=firm.id,
        actor=actor,
        action="sender_mapping.delete",
        target_type="sender_mapping",
        target_id=str(mapping_id),
    )


class ChannelMessageOut(BaseModel):
    id: int
    channel: str
    direction: str
    sender_identity: str
    document_id: str | None
    kind: str
    body: str
    created_at: str


@router.get("/channels/messages", response_model=list[ChannelMessageOut])
async def list_messages(
    session: SessionDep,
    actor: Actor = Depends(require("documents.view")),
    document_id: uuid.UUID | None = None,
    limit: int = Query(default=50, ge=1, le=200),
) -> list[ChannelMessageOut]:
    stmt = select(ChannelMessage).order_by(ChannelMessage.id.desc()).limit(limit)
    if document_id is not None:
        stmt = stmt.where(ChannelMessage.document_id == document_id)
    if actor.is_client:
        stmt = stmt.where(ChannelMessage.kind.in_(("question", "answer", "receipt")))
    rows = (await session.execute(stmt)).scalars().all()
    return [
        ChannelMessageOut(
            id=m.id,
            channel=m.channel,
            direction=m.direction,
            sender_identity=m.sender_identity if not actor.is_client else "",
            document_id=str(m.document_id) if m.document_id else None,
            kind=m.kind,
            body=m.body,
            created_at=m.created_at.isoformat() if m.created_at else "",
        )
        for m in rows
    ]


# ---- quarantine --------------------------------------------------------------------------------------
class ReleaseIn(BaseModel):
    client_company_id: uuid.UUID
    remember_sender: bool = False


@router.post("/documents/{document_id}/release")
async def release_document(
    request: Request,
    document_id: uuid.UUID,
    body: ReleaseIn,
    session: SessionDep,
    firm: FirmDep,
    svc: LicenseServiceDep,
    actor: Actor = Depends(require("documents.review")),
) -> JSONResponse:
    """A quarantined (unknown sender) document goes to the chosen client and into processing."""
    snap = check_feature(svc.snapshot(firm), "core")
    d = await session.get(Document, document_id)
    if d is None or d.deleted_at is not None:
        raise HTTPException(404, {"error": "not_found", "message": "Document not found."})
    if d.status != DocumentStatus.quarantined or d.error not in ("unknown_sender", "unknown_receiver"):
        raise HTTPException(
            409,
            {
                "error": "not_quarantined",
                "message": "Only unknown-sender or unknown-receiver quarantine can be released.",
            },
        )
    if await session.get(ClientCompany, body.client_company_id) is None:
        raise HTTPException(404, {"error": "not_found", "message": "Client company not found."})
    d.client_company_id, d.status, d.error = body.client_company_id, DocumentStatus.received, None
    if body.remember_sender and d.sender_identity and d.channel.value in CHANNELS:
        exists = (
            await session.execute(
                select(SenderMapping).where(
                    SenderMapping.channel == d.channel.value, SenderMapping.identity == d.sender_identity
                )
            )
        ).scalar_one_or_none()
        if exists is None:
            session.add(
                SenderMapping(
                    firm_id=firm.id,
                    client_company_id=body.client_company_id,
                    channel=d.channel.value,
                    identity=d.sender_identity,
                    paired_at=datetime.now(UTC),
                    created_by_user_id=actor.user_id,
                )
            )
    await record_audit(
        session,
        firm_id=firm.id,
        actor=actor,
        action="document.release",
        target_type="document",
        target_id=str(d.id),
        after={"client_company_id": str(body.client_company_id), "remember_sender": body.remember_sender},
    )
    await emit_event(session, firm.id, snap, "document.received", document_payload(d))
    await session.commit()
    await session.begin()
    await set_tenant(session, firm.id)
    queue = request.app.state.queue
    if queue is not None:
        await queue.enqueue_process_document(d.id)
        await queue.kick_webhooks()
    await session.refresh(d)
    return JSONResponse(
        {"id": str(d.id), "status": d.status.value, "client_company_id": str(d.client_company_id)}
    )


@router.post("/documents/{document_id}/discard")
async def discard_document(
    document_id: uuid.UUID,
    session: SessionDep,
    firm: FirmDep,
    actor: Actor = Depends(require("documents.review")),
) -> dict[str, str]:
    d = await session.get(Document, document_id)
    if d is None or d.deleted_at is not None:
        raise HTTPException(404, {"error": "not_found", "message": "Document not found."})
    if d.status != DocumentStatus.quarantined:
        raise HTTPException(
            409, {"error": "not_quarantined", "message": "Only quarantined documents can be discarded."}
        )
    d.status, d.review_status = DocumentStatus.rejected, "rejected"
    d.reviewed_by_user_id, d.reviewed_at = actor.user_id, datetime.now(UTC)
    await record_audit(
        session,
        firm_id=firm.id,
        actor=actor,
        action="document.discard",
        target_type="document",
        target_id=str(d.id),
    )
    return {"id": str(d.id), "status": d.status.value}


_ = InboundMessage
