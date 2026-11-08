"""Channel intake: route an inbound message to a firm and client, pass the license gate
and the monthly limit before storing anything, store through the one intake function, answer on
the channel, and handle the task-specific bot texts (pairing, status, answers)."""

from __future__ import annotations

import logging
import secrets
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.service import record_audit
from app.channels.base import InboundMessage, Transport, is_status_command, pairing_code_in
from app.channels.texts import text as bot_text
from app.config import Settings
from app.db.rls import set_platform_bypass, set_tenant
from app.intake import store_document
from app.jobs.queue import JobQueue
from app.licensing.gate import LicenseLimitReached, check_limit
from app.licensing.service import LicenseService
from app.models.channel import ChannelMessage, SenderMapping
from app.models.client_company import ClientCompany
from app.models.comment import DocumentComment
from app.models.document import Document, DocumentChannel, DocumentStatus
from app.models.firm import Firm
from app.pipeline import security
from app.storage.base import Storage
from app.webhooks import document_payload, emit_event

log = logging.getLogger("invoiceflow.channels")

PAIRING_TTL = timedelta(days=7)
MAX_FILES_PER_MESSAGE = 20


@dataclass(slots=True)
class IntakeDeps:
    settings: Settings
    storage: Storage
    scanner: security.VirusScanner
    service: LicenseService
    queue: JobQueue | None
    transports: Mapping[str, Transport]


@dataclass(slots=True)
class IntakeResult:
    # stored | quarantined | refused | duplicate | unknown_sender | paired | pairing_invalid | text | ignored
    outcome: str
    firm_id: uuid.UUID | None = None
    document_ids: list[uuid.UUID] = field(default_factory=list)
    replies: list[str] = field(default_factory=list)


def new_pairing_code() -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    raw = "".join(secrets.choice(alphabet) for _ in range(8))
    return f"{raw[:4]}-{raw[4:]}"


async def _log(
    session: AsyncSession,
    *,
    firm_id: uuid.UUID | None,
    channel: str,
    direction: str,
    sender: str,
    kind: str,
    body: str = "",
    external_id: str | None = None,
    document_id: uuid.UUID | None = None,
) -> None:
    session.add(
        ChannelMessage(
            firm_id=firm_id,
            channel=channel,
            direction=direction,
            sender_identity=sender[:320],
            external_id=external_id,
            document_id=document_id,
            kind=kind,
            body=body[:2000],
        )
    )
    await session.flush()


async def _reply(
    session: AsyncSession,
    deps: IntakeDeps,
    result: IntakeResult,
    *,
    firm_id: uuid.UUID | None,
    msg: InboundMessage,
    kind: str,
    body: str,
    subject: str | None = None,
    document_id: uuid.UUID | None = None,
    firm_locale: str | None = None,
) -> None:
    if kind in ("help", "pairing", "unknown") and msg.channel != DocumentChannel.email:
        body = f"{body} {bot_text(firm_locale, 'disclosure')}"  # EU AI Act art. 50
    result.replies.append(body)
    transport = deps.transports.get(msg.channel.value)
    sent = False
    if transport is not None:
        try:
            sent = await transport.send_text(msg.reply_to or msg.sender, body, subject=subject)
        except Exception as e:  # noqa: BLE001 - a failed reply never fails the intake
            log.warning("reply on %s failed: %s", msg.channel.value, type(e).__name__)
    await _log(
        session,
        firm_id=firm_id,
        channel=msg.channel.value,
        direction="out",
        sender=msg.sender,
        kind=kind if sent else f"{kind}_unsent",
        body=body,
        document_id=document_id,
    )


async def _seen_before(session: AsyncSession, msg: InboundMessage) -> bool:
    if not msg.external_id:
        return False
    row = (
        await session.execute(
            select(ChannelMessage.id).where(
                ChannelMessage.channel == msg.channel.value,
                ChannelMessage.direction == "in",
                ChannelMessage.external_id == msg.external_id,
            )
        )
    ).first()
    return row is not None


async def _mapping(session: AsyncSession, channel: str, identity: str) -> SenderMapping | None:
    return (
        await session.execute(
            select(SenderMapping).where(
                SenderMapping.channel == channel,
                SenderMapping.identity == identity,
            )
        )
    ).scalar_one_or_none()


async def _try_pairing(session: AsyncSession, msg: InboundMessage, code: str) -> SenderMapping | None:
    row = (
        await session.execute(
            select(SenderMapping).where(
                SenderMapping.pairing_code == code,
                SenderMapping.channel == msg.channel.value,
                SenderMapping.identity.is_(None),
            )
        )
    ).scalar_one_or_none()
    if row is None or (row.pairing_expires_at and row.pairing_expires_at < datetime.now(UTC)):
        return None
    if await _mapping(session, msg.channel.value, msg.sender) is not None:
        return None  # already bound elsewhere; the accountant removes the old mapping first
    row.identity, row.paired_at, row.pairing_code = msg.sender, datetime.now(UTC), None
    row.display_name = row.display_name or msg.sender_display[:200]
    await session.flush()
    return row


async def _documents_this_month(session: AsyncSession) -> int:
    start = datetime.now(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    n = (
        await session.execute(
            select(func.count())
            .select_from(Document)
            .where(Document.received_at >= start, Document.deleted_at.is_(None))
        )
    ).scalar_one()
    return int(n)


async def _status_text(session: AsyncSession, firm: Firm, client_id: uuid.UUID) -> str:
    rows = (
        await session.execute(
            select(Document.status, func.count())
            .where(Document.client_company_id == client_id, Document.deleted_at.is_(None))
            .group_by(Document.status)
        )
    ).all()
    counts = {str(status.value if hasattr(status, "value") else status): int(n) for status, n in rows}
    total = sum(counts.values())
    return bot_text(
        firm.locale,
        "status",
        total=total,
        received=counts.get("received", 0),
        processing=counts.get("processing", 0),
        needs_info=counts.get("needs_info", 0),
        needs_review=counts.get("needs_review", 0),
        approved=counts.get("approved", 0),
    )


async def _open_question_document(
    session: AsyncSession, firm: Firm, client_id: uuid.UUID, sender: str, channel: str
) -> Document | None:
    """The document the sender was last asked about, else the client's latest ``needs_info`` document."""
    last_q = (
        await session.execute(
            select(ChannelMessage.document_id)
            .where(
                ChannelMessage.firm_id == firm.id,
                ChannelMessage.channel == channel,
                ChannelMessage.direction == "out",
                ChannelMessage.kind == "question",
                ChannelMessage.sender_identity == sender,
            )
            .order_by(ChannelMessage.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if last_q is not None:
        d = await session.get(Document, last_q)
        if d is not None and d.status == DocumentStatus.needs_info:
            return d
    return (
        await session.execute(
            select(Document)
            .where(Document.client_company_id == client_id, Document.status == DocumentStatus.needs_info)
            .order_by(Document.received_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def _resolve_email(
    session: AsyncSession, deps: IntakeDeps, msg: InboundMessage
) -> tuple[Firm, ClientCompany, bool] | None:
    """(firm, client, known sender) by the dedicated address; None when the address is unknown."""
    if not msg.to_address:
        return None
    local = msg.to_address.split("@", 1)[0].strip().lower()
    client = (
        await session.execute(select(ClientCompany).where(ClientCompany.inbound_email_local_part == local))
    ).scalar_one_or_none()
    if client is None or not client.is_active:
        return None
    firm = await session.get(Firm, client.firm_id)
    if firm is None or not firm.is_active:
        return None
    mapping = await _mapping(session, "email", msg.sender)
    known = (mapping is not None and mapping.firm_id == firm.id) or bool(
        (client.settings or {}).get("email_accept_any_sender")
    )
    if mapping is not None and mapping.firm_id == firm.id:
        mapping.last_seen_at = datetime.now(UTC)
    return firm, client, known


async def handle_inbound(session: AsyncSession, deps: IntakeDeps, msg: InboundMessage) -> IntakeResult:
    """One inbound message end to end. The caller owns the transaction (commits on return)."""
    result = IntakeResult("ignored")
    await set_platform_bypass(session)
    if await _seen_before(session, msg):
        result.outcome = "duplicate"
        return result
    channel = msg.channel.value

    # ---- routing -------------------------------------------------------------------------------
    firm: Firm | None = None
    client: ClientCompany | None = None
    known = False
    if msg.channel == DocumentChannel.email:
        routed = await _resolve_email(session, deps, msg)
        if routed is None:
            await _log(
                session,
                firm_id=None,
                channel=channel,
                direction="in",
                sender=msg.sender,
                kind="unknown",
                body=msg.subject or "",
                external_id=msg.external_id,
            )
            await record_audit(
                session,
                firm_id=None,
                actor=None,
                action="channel.unknown_address",
                target_type="channel",
                target_id=(msg.to_address or "")[:120],
                result="ignored",
            )
            result.outcome = "unknown_sender"
            return result
        firm, client, known = routed
    else:
        mapping = await _mapping(session, channel, msg.sender)
        if mapping is None:
            code = pairing_code_in(msg.text)
            if code:
                bound = await _try_pairing(session, msg, code)
                if bound is not None:
                    firm = await session.get(Firm, bound.firm_id)
                    client = await session.get(ClientCompany, bound.client_company_id)
                    assert firm is not None and client is not None
                    await set_tenant(session, firm.id)
                    await _log(
                        session,
                        firm_id=firm.id,
                        channel=channel,
                        direction="in",
                        sender=msg.sender,
                        kind="pairing",
                        body=code,
                        external_id=msg.external_id,
                    )
                    await record_audit(
                        session,
                        firm_id=firm.id,
                        actor=None,
                        action="channel.paired",
                        target_type="sender_mapping",
                        target_id=str(bound.id),
                        after={"channel": channel, "client_company_id": str(client.id)},
                    )
                    await _reply(
                        session,
                        deps,
                        result,
                        firm_id=firm.id,
                        msg=msg,
                        kind="pairing",
                        body=bot_text(firm.locale, "paired", client=client.name),
                    )
                    result.outcome, result.firm_id = "paired", firm.id
                    return result
                await _log(
                    session,
                    firm_id=None,
                    channel=channel,
                    direction="in",
                    sender=msg.sender,
                    kind="pairing",
                    body="invalid",
                    external_id=msg.external_id,
                )
                await _reply(
                    session,
                    deps,
                    result,
                    firm_id=None,
                    msg=msg,
                    kind="pairing",
                    body=bot_text("en", "pairing_invalid"),
                )
                result.outcome = "pairing_invalid"
                return result
            await _log(
                session,
                firm_id=None,
                channel=channel,
                direction="in",
                sender=msg.sender,
                kind="unknown",
                body="",
                external_id=msg.external_id,
            )
            await record_audit(
                session,
                firm_id=None,
                actor=None,
                action="channel.unknown_sender",
                target_type="channel",
                target_id=channel,
                result="ignored",
            )
            await _reply(
                session, deps, result, firm_id=None, msg=msg, kind="unknown", body=bot_text("en", "unknown")
            )
            result.outcome = "unknown_sender"
            return result
        firm = await session.get(Firm, mapping.firm_id)
        client = await session.get(ClientCompany, mapping.client_company_id)
        if firm is None or client is None:
            result.outcome = "ignored"
            return result
        mapping.last_seen_at = datetime.now(UTC)
        known = True

    assert firm is not None and client is not None
    await set_tenant(session, firm.id)
    result.firm_id = firm.id
    await _log(
        session,
        firm_id=firm.id,
        channel=channel,
        direction="in",
        sender=msg.sender,
        kind="document" if msg.files else "text",
        body=(msg.subject or msg.text or "")[:500],
        external_id=msg.external_id,
    )

    # ---- license gate before anything is stored -------------------------------------
    snap = deps.service.snapshot(firm)
    if not snap.core_on or not snap.feature_on("channels"):
        reason = "license_required" if not snap.core_on else "channels_not_entitled"
        await record_audit(
            session,
            firm_id=firm.id,
            actor=None,
            action="channel.intake_refused",
            target_type="channel",
            target_id=channel,
            after={"reason": reason, "files": len(msg.files), "sender": msg.sender[:120]},
            result="refused",
        )
        if msg.channel != DocumentChannel.email:  # no auto-replies by mail (backscatter)
            await _reply(
                session,
                deps,
                result,
                firm_id=firm.id,
                msg=msg,
                kind="paused",
                body=bot_text(firm.locale, "paused"),
            )
        result.outcome = "refused"
        return result

    # ---- text-only messages: pairing noise, status, answers, help ------------------------------
    files = list(msg.files)
    if not files and msg.channel == DocumentChannel.email and (msg.text or "").strip():
        from app.channels.email import body_to_pdf

        files = [body_to_pdf(msg.subject or "", msg.sender_display or msg.sender, msg.text or "")]
    if not files:
        if msg.channel == DocumentChannel.email:
            result.outcome = "ignored"
            return result
        if is_status_command(msg.text):
            await _reply(
                session,
                deps,
                result,
                firm_id=firm.id,
                msg=msg,
                kind="status",
                body=await _status_text(session, firm, client.id),
            )
            result.outcome = "text"
            return result
        if pairing_code_in(msg.text):
            await _reply(
                session,
                deps,
                result,
                firm_id=firm.id,
                msg=msg,
                kind="help",
                body=bot_text(firm.locale, "paired", client=client.name),
            )
            result.outcome = "text"
            return result
        answer_text = (msg.text or "").strip()
        doc = (
            await _open_question_document(session, firm, client.id, msg.sender, channel)
            if answer_text
            else None
        )
        if doc is None:
            await _reply(
                session,
                deps,
                result,
                firm_id=firm.id,
                msg=msg,
                kind="help",
                body=bot_text(firm.locale, "no_question" if answer_text else "help"),
            )
            result.outcome = "text"
            return result
        session.add(
            DocumentComment(
                firm_id=firm.id,
                document_id=doc.id,
                author_user_id=None,
                kind="answer",
                body=answer_text[:4000],
                visible_to_client=True,
            )
        )
        for q in (
            await session.execute(
                select(DocumentComment).where(
                    DocumentComment.document_id == doc.id,
                    DocumentComment.kind == "question",
                    DocumentComment.resolved_at.is_(None),
                )
            )
        ).scalars():
            q.resolved_at = datetime.now(UTC)
        doc.status, doc.review_status = DocumentStatus.needs_review, "needs_review"
        await record_audit(
            session,
            firm_id=firm.id,
            actor=None,
            action="document.comment.answer",
            target_type="document",
            target_id=str(doc.id),
            after={"channel": channel},
        )
        await _log(
            session,
            firm_id=firm.id,
            channel=channel,
            direction="in",
            sender=msg.sender,
            kind="answer",
            body=answer_text,
            document_id=doc.id,
        )
        await _reply(
            session,
            deps,
            result,
            firm_id=firm.id,
            msg=msg,
            kind="answer",
            body=bot_text(firm.locale, "answer_ack"),
            document_id=doc.id,
        )
        result.outcome = "text"
        result.document_ids.append(doc.id)
        return result

    # ---- files: type and scan, then the monthly limit, then storage ----------------------------
    accepted: list[tuple[str, bytes, str]] = []
    unsupported = 0
    for f in files[:MAX_FILES_PER_MESSAGE]:
        try:
            security.check_size(f.data)
            mime, _ext = security.check_type(f.data)
        except security.IntakeRejectedError:
            unsupported += 1
            continue
        if mime == "application/zip":
            from app.pipeline.detect import unpack_zip

            try:
                for entry in unpack_zip(f.data):
                    emime, _ = security.check_type(entry.data)
                    if emime != "application/zip":
                        accepted.append((entry.name, entry.data, emime))
            except security.IntakeRejectedError:
                unsupported += 1
            continue
        accepted.append((f.name, f.data, mime))
    clean: list[tuple[str, bytes, str]] = []
    for name, blob, mime in accepted:
        scan = await deps.scanner.scan(blob)
        if not scan.clean:
            await record_audit(
                session,
                firm_id=firm.id,
                actor=None,
                action="document.receive",
                target_type="document",
                target_id=name[:120],
                after={"channel": channel},
                result="malware",
            )
            unsupported += 1
            continue
        clean.append((name, blob, mime))
    if not clean:
        if msg.channel != DocumentChannel.email:
            await _reply(
                session,
                deps,
                result,
                firm_id=firm.id,
                msg=msg,
                kind="unsupported",
                body=bot_text(firm.locale, "unsupported"),
            )
        result.outcome = "ignored"
        return result
    try:
        check_limit(snap, "max_documents_per_month", await _documents_this_month(session), adding=len(clean))
    except LicenseLimitReached:
        await record_audit(
            session,
            firm_id=firm.id,
            actor=None,
            action="channel.intake_refused",
            target_type="channel",
            target_id=channel,
            after={"reason": "max_documents_per_month", "files": len(clean)},
            result="refused",
        )
        if msg.channel != DocumentChannel.email:
            await _reply(
                session,
                deps,
                result,
                firm_id=firm.id,
                msg=msg,
                kind="paused",
                body=bot_text(firm.locale, "limit"),
            )
        result.outcome = "refused"
        return result
    docs: list[Document] = []
    for name, blob, mime in clean:
        doc = await store_document(
            session,
            deps.storage,
            firm,
            data=blob,
            filename=name,
            mime=mime,
            client_company_id=client.id,
            channel=msg.channel,
            sender_identity=msg.sender,
            status=DocumentStatus.received if known else DocumentStatus.quarantined,
            error=None if known else "unknown_sender",
        )
        docs.append(doc)
        await _log(
            session,
            firm_id=firm.id,
            channel=channel,
            direction="in",
            sender=msg.sender,
            kind="file",
            body=name[:200],
            document_id=doc.id,
        )
        if known:
            await emit_event(session, firm.id, snap, "document.received", document_payload(doc))
    result.document_ids = [d.id for d in docs]
    if not known:
        await record_audit(
            session,
            firm_id=firm.id,
            actor=None,
            action="channel.quarantined",
            target_type="channel",
            target_id=channel,
            after={"sender": msg.sender[:120], "documents": len(docs)},
        )
        result.outcome = "quarantined"
        return result
    await session.commit()  # the worker must see the rows
    await session.begin()
    await set_tenant(session, firm.id)
    if deps.queue is not None:
        for d in docs:
            await deps.queue.enqueue_process_document(d.id)
        await deps.queue.kick_webhooks()
    if msg.channel != DocumentChannel.email:
        body = (
            bot_text(firm.locale, "receipt_one")
            if len(docs) == 1
            else bot_text(firm.locale, "receipt", n=len(docs))
        )
        if unsupported:
            body += " " + bot_text(firm.locale, "unsupported")
        await _reply(
            session, deps, result, firm_id=firm.id, msg=msg, kind="receipt", body=body, document_id=docs[0].id
        )
    result.outcome = "stored"
    return result


async def send_question(
    session: AsyncSession,
    transports: Mapping[str, Transport],
    firm: Firm,
    doc: Document,
    question: str,
) -> bool:
    """Request-info: the accountant's question goes back through the document's channel."""
    channel = doc.channel.value
    transport = transports.get(channel)
    if transport is None or channel not in ("email", "telegram", "whatsapp") or not doc.sender_identity:
        return False
    name = doc.invoice_number or doc.original_filename
    body = bot_text(firm.locale, "question", name=name, question=question)
    sent = False
    try:
        sent = await transport.send_text(
            doc.sender_identity, body, subject=bot_text(firm.locale, "mail_subject_question", name=name)
        )
    except Exception as e:  # noqa: BLE001
        log.warning("question on %s failed: %s", channel, type(e).__name__)
    await _log(
        session,
        firm_id=firm.id,
        channel=channel,
        direction="out",
        sender=doc.sender_identity,
        kind="question" if sent else "question_unsent",
        body=body,
        document_id=doc.id,
    )
    return sent


def channel_status(settings: Settings, transports: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "email": {
            "configured": bool(settings.inbound_email_webhook_token or settings.imap_host),
            "domain": settings.inbound_email_domain,
            "webhook_path": "/api/v1/channels/email/inbound",
            "imap": bool(settings.imap_host),
            "replies": "email" in transports,
        },
        "telegram": {
            "configured": bool(settings.telegram_bot_token),
            "webhook_path": "/api/v1/channels/telegram/webhook",
        },
        "whatsapp": {
            "configured": bool(settings.whatsapp_access_token and settings.whatsapp_phone_number_id),
            "webhook_path": "/api/v1/channels/whatsapp/webhook",
        },
    }


__all__ = [
    "IntakeDeps",
    "IntakeResult",
    "PAIRING_TTL",
    "channel_status",
    "handle_inbound",
    "new_pairing_code",
    "send_question",
]
