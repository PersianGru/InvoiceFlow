"""Channel intake: route an inbound message to a firm and client, pass the license gate
and the monthly limit before storing anything, store through the one intake function, answer on
the channel, and handle the task-specific bot texts (pairing, status, answers)."""

from __future__ import annotations

import logging
import re
import secrets
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.service import record_audit
from app.channels.base import InboundFile, InboundMessage, Transport, is_status_command, pairing_code_in
from app.channels.texts import text as bot_text
from app.config import Settings
from app.db.rls import set_platform_bypass, set_tenant
from app.intake import store_document
from app.jobs.queue import JobQueue
from app.licensing.gate import LicenseLimitReached, check_limit
from app.licensing.service import LicenseService
from app.models.bots import ChannelBot, HeldMessage, SenderSelection
from app.models.channel import ChannelMessage, SenderMapping
from app.models.client_company import ClientCompany
from app.models.comment import DocumentComment
from app.models.document import Document, DocumentChannel, DocumentStatus
from app.models.firm import Firm
from app.pipeline import security
from app.settings_store import SettingsStore
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
    store: SettingsStore | None = None
    mailer: Any = None  # app.mail.Mailer: email replies through the dashboard SMTP


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
    transport = (
        msg.transport or _email_transport(deps, msg, firm_id) or deps.transports.get(msg.channel.value)
    )
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


def _email_transport(deps: IntakeDeps, msg: InboundMessage, firm_id: uuid.UUID | None) -> Transport | None:
    if msg.channel != DocumentChannel.email or deps.mailer is None:
        return None
    from app.mail import MailerTransport

    return MailerTransport(deps.mailer, firm_id)


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


async def _mappings(
    session: AsyncSession,
    channel: str,
    identity: str,
    *,
    scope: str = "person",
    firm_id: uuid.UUID | None = None,
) -> list[SenderMapping]:
    """Active pairings of a sender (or of a chat). A person may be paired to several client companies
    ; ``firm_id`` limits the search to one firm (a firm's own bot)."""
    stmt = select(SenderMapping).where(
        SenderMapping.channel == channel,
        SenderMapping.identity == identity,
        SenderMapping.scope == scope,
        SenderMapping.active.is_(True),
    )
    if firm_id is not None:
        stmt = stmt.where(SenderMapping.firm_id == firm_id)
    return list((await session.execute(stmt.order_by(SenderMapping.created_at))).scalars().all())


async def _mapping(session: AsyncSession, channel: str, identity: str) -> SenderMapping | None:
    rows = await _mappings(session, channel, identity)
    return rows[0] if rows else None


async def _try_pairing(
    session: AsyncSession,
    msg: InboundMessage,
    code: str,
    *,
    scope: str = "person",
    identity: str | None = None,
) -> SenderMapping | None:
    """Bind a pairing code to the sender (``person``) or to a Telegram group/channel (``chat``)."""
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
    ident = identity or msg.sender
    existing = (
        await session.execute(
            select(SenderMapping).where(
                SenderMapping.channel == msg.channel.value,
                SenderMapping.scope == scope,
                SenderMapping.identity == ident,
                SenderMapping.client_company_id == row.client_company_id,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:  # already paired to this client company: the code is used up
        existing.active = True
        await session.delete(row)
        await session.flush()
        return existing
    row.identity, row.scope, row.paired_at, row.pairing_code = ident, scope, datetime.now(UTC), None
    row.bot_id = msg.bot_id
    row.display_name = row.display_name or (msg.chat_title if scope == "chat" else msg.sender_display)[:200]
    await session.flush()
    return row


GROUP_TYPES = ("group", "supergroup", "channel")
COMPANY_RE = re.compile(r"^/company(?:@\w+)?(?:\s+(.+))?$", re.IGNORECASE)
TAG_RE = re.compile(r"(?:^|\s)#([\w.-]{2,40})")
HOLD_HOURS = 24


def _pick(options: list[ClientCompany], arg: str) -> ClientCompany | None:
    from app.addresses import name_slug

    arg = arg.strip()
    if arg.isdigit():
        i = int(arg)
        return options[i - 1] if 1 <= i <= len(options) else None
    want = name_slug(arg)
    hits = [c for c in options if name_slug(c.name).startswith(want) or arg.lower() in c.name.lower()]
    return hits[0] if len(hits) == 1 else None


def _options_text(options: list[ClientCompany]) -> str:
    return ", ".join(f"{i}) {c.name}" for i, c in enumerate(options, start=1))


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


@dataclass(slots=True)
class EmailRoute:
    firm: Firm | None
    client: ClientCompany | None
    known: bool
    problem: str | None = None  # unknown_recipient | ambiguous_recipient | retired_address
    flag: str | None = None  # retired_address_grace
    address: str = ""


async def _route_email(session: AsyncSession, deps: IntakeDeps, msg: InboundMessage) -> EmailRoute:
    """Package B: every recipient candidate is looked at; only addresses on an inbound domain count
    (the firm's domains, or the legacy ``INBOUND_EMAIL_DOMAIN``). The domain names the firm, the local
    part the client company. Two client companies in one email → ``ambiguous_recipient`` (never
    guessed). A retired address routes only during its grace period and only for known
    senders."""
    from app.addresses import retired_address
    from app.domains import any_domain_configured, find_domain
    from app.models.client_company import InboundAddress

    candidates = list(msg.recipients) or ([msg.to_address] if msg.to_address else [])
    has_domains = await any_domain_configured(session)
    legacy = (deps.settings.inbound_email_domain or "").strip().lower()
    hits: dict[uuid.UUID, tuple[ClientCompany, InboundAddress | None, str]] = {}
    for addr in candidates:
        local, _, domain = addr.strip().lower().partition("@")
        if not local or not domain:
            continue
        scope: uuid.UUID | None = None
        dom = await find_domain(session, domain) if has_domains else None
        if dom is not None:
            scope = dom.firm_id
        elif domain != legacy:
            continue
        stmt = select(ClientCompany).where(ClientCompany.inbound_email_local_part == local)
        if scope is not None:
            stmt = stmt.where(ClientCompany.firm_id == scope)
        client = (await session.execute(stmt)).scalar_one_or_none()
        if client is not None:
            hits[client.id] = (client, None, addr)
            continue
        old = await retired_address(session, local)
        if old is not None and (scope is None or old.firm_id == scope):
            c = await session.get(ClientCompany, old.client_company_id)
            if c is not None and c.id not in hits:
                hits[c.id] = (c, old, addr)
    if not hits:
        return EmailRoute(None, None, False, "unknown_recipient", address=(candidates or [""])[0])
    if len(hits) > 1:
        firms = {c.firm_id for c, _o, _a in hits.values()}
        firm = await session.get(Firm, next(iter(firms))) if len(firms) == 1 else None
        return EmailRoute(
            firm, None, False, "ambiguous_recipient", address=", ".join(a for *_x, a in hits.values())
        )
    client, old, addr = next(iter(hits.values()))
    firm = await session.get(Firm, client.firm_id)
    if firm is None or not firm.is_active or firm.deleted_at is not None or not client.is_active:
        return EmailRoute(None, None, False, "unknown_recipient", address=addr)
    senders = await _mappings(session, "email", msg.sender, firm_id=firm.id)
    mapped = bool(senders)
    for m in senders:
        m.last_seen_at = datetime.now(UTC)
    if old is not None:
        in_grace = old.grace_until is not None and old.grace_until > datetime.now(UTC)
        if not (in_grace and mapped):
            return EmailRoute(firm, client, False, "retired_address", address=addr)
        return EmailRoute(firm, client, True, None, "retired_address_grace", address=addr)
    known = mapped or bool((client.settings or {}).get("email_accept_any_sender"))
    return EmailRoute(firm, client, known, address=addr)


async def _unknown_person(
    session: AsyncSession, deps: IntakeDeps, msg: InboundMessage, result: IntakeResult
) -> None:
    channel = msg.channel.value
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
    await _reply(session, deps, result, firm_id=None, msg=msg, kind="unknown", body=bot_text("en", "unknown"))
    result.outcome = "unknown_sender"


async def _pair(
    session: AsyncSession,
    deps: IntakeDeps,
    msg: InboundMessage,
    result: IntakeResult,
    code: str,
    *,
    scope: str,
    identity: str,
) -> None:
    channel = msg.channel.value
    bound = await _try_pairing(session, msg, code, scope=scope, identity=identity)
    if bound is None:
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
        return
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
        after={
            "channel": channel,
            "client_company_id": str(client.id),
            "scope": scope,
            "chat_title": (msg.chat_title or "")[:120] if scope == "chat" else None,
        },
    )
    key = "chat_paired" if scope == "chat" else "paired"
    await _reply(
        session,
        deps,
        result,
        firm_id=firm.id,
        msg=msg,
        kind="pairing",
        body=bot_text(firm.locale, key, client=client.name),
    )
    result.outcome, result.firm_id = "paired", firm.id


async def _bot_scope(
    session: AsyncSession, msg: InboundMessage
) -> tuple[ChannelBot | None, uuid.UUID | None]:
    """(bot, firm the bot limits routing to). The platform bot (or the environment bot) routes across
    firms; a firm's own bot only to that firm."""
    if msg.bot_id is None:
        return None, None
    bot = await session.get(ChannelBot, msg.bot_id)
    if bot is None:
        return None, None
    owner = await session.get(Firm, bot.firm_id)
    return bot, (None if owner is not None and owner.kind == "platform" else bot.firm_id)


async def _route_chat(
    session: AsyncSession, deps: IntakeDeps, msg: InboundMessage, result: IntakeResult
) -> tuple[Firm, ClientCompany, bool] | None:
    """Package D routing for Telegram and WhatsApp. ``None`` means the result is final already."""
    channel = msg.channel.value
    now = datetime.now(UTC)
    bot, bot_firm = await _bot_scope(session, msg)
    if msg.forced_client_id is not None:  # a held message, released after the company was chosen
        client = await session.get(ClientCompany, msg.forced_client_id)
        firm = await session.get(Firm, client.firm_id) if client is not None else None
        if client is None or firm is None:
            result.outcome = "ignored"
            return None
        return firm, client, True
    # 1. a dedicated bot or number: everything it receives belongs to its client company
    if bot is not None and bot.client_company_id is not None:
        client = await session.get(ClientCompany, bot.client_company_id)
        firm = await session.get(Firm, bot.firm_id)
        if client is None or firm is None or not client.is_active:
            result.outcome = "ignored"
            return None
        people = await _mappings(session, channel, msg.sender, firm_id=firm.id)
        known = bot.accept_from == "anyone" or any(m.client_company_id == client.id for m in people)
        return firm, client, known
    # 2. a Telegram group or channel: the chat decides the client company
    if msg.chat_type in GROUP_TYPES and msg.chat_id:
        if msg.migrate_to_chat_id:
            for m in await _mappings(session, channel, msg.chat_id, scope="chat"):
                m.identity = msg.migrate_to_chat_id
            result.outcome = "ignored"
            return None
        chats = await _mappings(session, channel, msg.chat_id, scope="chat", firm_id=bot_firm)
        code = pairing_code_in(msg.text)
        if code and not chats:
            await _pair(session, deps, msg, result, code, scope="chat", identity=msg.chat_id)
            return None
        if not chats:
            result.outcome = "ignored"  # never answer in groups the bot does not serve
            return None
        m = chats[0]
        m.last_seen_at = now
        client = await session.get(ClientCompany, m.client_company_id)
        firm = await session.get(Firm, m.firm_id)
        if client is None or firm is None:
            result.outcome = "ignored"
            return None
        if m.accept_from == "paired" and msg.files and msg.chat_type != "channel":
            people = await _mappings(session, channel, msg.sender, firm_id=firm.id)
            if not any(p.client_company_id == client.id for p in people):
                await set_tenant(session, firm.id)
                await _reply(
                    session,
                    deps,
                    result,
                    firm_id=firm.id,
                    msg=msg,
                    kind="unknown",
                    body=bot_text(firm.locale, "chat_members_only", client=client.name),
                )
                result.outcome, result.firm_id = "refused", firm.id
                return None
        return firm, client, True
    # 3. a person: pairing, one company, or a choice among several
    code = pairing_code_in(msg.text)
    if code:
        await _pair(session, deps, msg, result, code, scope="person", identity=msg.sender)
        return None
    people = await _mappings(session, channel, msg.sender, firm_id=bot_firm)
    if not people:
        await _unknown_person(session, deps, msg, result)
        return None
    for m in people:
        m.last_seen_at = now
    if len(people) == 1:
        client = await session.get(ClientCompany, people[0].client_company_id)
        firm = await session.get(Firm, people[0].firm_id)
        if client is None or firm is None:
            result.outcome = "ignored"
            return None
        return firm, client, True
    return await _choose_company(session, deps, msg, result, people)


async def _choose_company(
    session: AsyncSession,
    deps: IntakeDeps,
    msg: InboundMessage,
    result: IntakeResult,
    people: list[SenderMapping],
) -> tuple[Firm, ClientCompany, bool] | None:
    channel = msg.channel.value
    options: list[ClientCompany] = []
    for m in people:
        c = await session.get(ClientCompany, m.client_company_id)
        if c is not None and c.is_active and c not in options:
            options.append(c)
    options.sort(key=lambda c: c.name.lower())
    locale = None
    first_firm = await session.get(Firm, options[0].firm_id) if options else None
    if first_firm is not None:
        locale = first_firm.locale
    text = (msg.text or "").strip()

    async def say(key: str, **kw: object) -> None:
        await _reply(
            session, deps, result, firm_id=None, msg=msg, kind="help", body=bot_text(locale, key, **kw)
        )

    command = COMPANY_RE.match(text)
    held = (
        (
            await session.execute(
                select(HeldMessage).where(HeldMessage.channel == channel, HeldMessage.identity == msg.sender)
            )
        )
        .scalars()
        .all()
    )
    answer = command.group(1) if command else (text if text.isdigit() and held and not msg.files else None)
    if command and not command.group(1):
        await say("company_list", options=_options_text(options))
        result.outcome = "text"
        return None
    if answer is not None:
        pick = _pick(options, answer)
        if pick is None:
            await say("company_unknown", options=_options_text(options))
            result.outcome = "text"
            return None
        await _select(session, channel, msg.sender, pick)
        await say("company_set", client=pick.name)
        result.outcome = "text"
        for h in held:
            await _release(session, deps, h, pick, result)
        return None
    tag = TAG_RE.search(text)
    if tag:
        pick = _pick(options, tag.group(1))
        if pick is not None:
            return await _with_firm(session, pick)
    selected = (
        await session.execute(
            select(SenderSelection)
            .where(SenderSelection.channel == channel, SenderSelection.identity == msg.sender)
            .order_by(SenderSelection.set_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if selected is not None:
        pick = next((c for c in options if c.id == selected.client_company_id), None)
        if pick is not None:
            return await _with_firm(session, pick)
    if msg.files:
        await _hold(session, deps, msg, options)
        await say("choose_company", options=_options_text(options))
        result.outcome = "held"
        return None
    await say("company_list", options=_options_text(options))
    result.outcome = "text"
    return None


async def _with_firm(session: AsyncSession, client: ClientCompany) -> tuple[Firm, ClientCompany, bool] | None:
    firm = await session.get(Firm, client.firm_id)
    return (firm, client, True) if firm is not None else None


async def _select(session: AsyncSession, channel: str, identity: str, client: ClientCompany) -> None:
    for old in (
        await session.execute(
            select(SenderSelection).where(
                SenderSelection.channel == channel, SenderSelection.identity == identity
            )
        )
    ).scalars():
        await session.delete(old)
    session.add(
        SenderSelection(
            firm_id=client.firm_id, channel=channel, identity=identity, client_company_id=client.id
        )
    )
    await session.flush()
    await record_audit(
        session,
        firm_id=client.firm_id,
        actor=None,
        action="channel.company_select",
        target_type="client_company",
        target_id=str(client.id),
        after={"channel": channel},
    )


async def _hold(
    session: AsyncSession, deps: IntakeDeps, msg: InboundMessage, options: list[ClientCompany]
) -> None:
    held = HeldMessage(
        channel=msg.channel.value,
        identity=msg.sender,
        bot_id=msg.bot_id,
        reply_to=msg.reply_to or msg.sender,
        options=[str(c.id) for c in options],
        text=(msg.text or "")[:2000],
        expires_at=datetime.now(UTC) + timedelta(hours=HOLD_HOURS),
    )
    session.add(held)
    await session.flush()
    files = []
    for i, f in enumerate(msg.files[:MAX_FILES_PER_MESSAGE]):
        key = f"held/{held.id}/{i}"
        await deps.storage.put(key, f.data)
        files.append({"name": f.name[:200], "key": key, "mime": f.mime or ""})
    held.files = files


async def _held_files(deps: IntakeDeps, held: HeldMessage) -> list[InboundFile]:
    out = []
    for f in held.files:
        try:
            out.append(InboundFile(f["name"], await deps.storage.get(f["key"]), f.get("mime") or None))
        except Exception as e:  # noqa: BLE001 - a missing blob drops one file, not the rest
            log.warning("held file missing: %s", type(e).__name__)
    return out


async def _drop_held(deps: IntakeDeps, session: AsyncSession, held: HeldMessage) -> None:
    for f in held.files:
        try:
            await deps.storage.delete(f["key"])
        except Exception as e:  # noqa: BLE001
            log.warning("held file not deleted: %s", type(e).__name__)
    await session.delete(held)


async def _release(
    session: AsyncSession, deps: IntakeDeps, held: HeldMessage, client: ClientCompany, parent: IntakeResult
) -> None:
    """Run held files through the normal intake for the chosen client company."""

    msg = InboundMessage(
        channel=DocumentChannel(held.channel),
        sender=held.identity,
        text=None,
        files=await _held_files(deps, held),
        reply_to=held.reply_to,
        bot_id=held.bot_id,
        forced_client_id=client.id,
    )
    await _drop_held(deps, session, held)
    await set_platform_bypass(session)
    sub = await handle_inbound(session, deps, msg)
    parent.document_ids.extend(sub.document_ids)
    await set_platform_bypass(session)


async def expire_held(session: AsyncSession, deps: IntakeDeps) -> dict[str, int]:
    """After 24 h without an answer: quarantine in the firm when every option is in one firm, else drop.
    Caller has the platform bypass."""
    counts = {"quarantined": 0, "dropped": 0}
    rows = (
        (await session.execute(select(HeldMessage).where(HeldMessage.expires_at <= datetime.now(UTC))))
        .scalars()
        .all()
    )
    for held in rows:
        clients = [await session.get(ClientCompany, uuid.UUID(c)) for c in held.options]
        firms = {c.firm_id for c in clients if c is not None}
        if len(firms) == 1:
            firm = await session.get(Firm, next(iter(firms)))
            assert firm is not None
            await set_tenant(session, firm.id)
            for name, blob in [(f.name, f.data) for f in await _held_files(deps, held)]:
                try:
                    mime, _ = security.check_type(blob)
                except security.IntakeRejectedError:
                    continue
                await store_document(
                    session,
                    deps.storage,
                    firm,
                    data=blob,
                    filename=name,
                    mime=mime,
                    client_company_id=None,
                    channel=DocumentChannel(held.channel),
                    sender_identity=held.identity,
                    status=DocumentStatus.quarantined,
                    error="company_not_chosen",
                )
                counts["quarantined"] += 1
            await set_platform_bypass(session)
        else:
            counts["dropped"] += 1
        await record_audit(
            session,
            firm_id=next(iter(firms)) if len(firms) == 1 else None,
            actor=None,
            action="channel.held_expired",
            target_type="channel",
            target_id=held.channel,
            after={"files": len(held.files), "outcome": "quarantined" if len(firms) == 1 else "dropped"},
        )
        await _drop_held(deps, session, held)
    return counts


async def _paused(
    session: AsyncSession,
    deps: IntakeDeps,
    result: IntakeResult,
    firm: Firm,
    client: ClientCompany,
    msg: InboundMessage,
) -> bool:
    """Package C: intake paused for this client company on this channel → log, answer, stop."""
    from app.addresses import intake_paused

    if not intake_paused(client, msg.channel.value):
        return False
    await _log(
        session,
        firm_id=firm.id,
        channel=msg.channel.value,
        direction="in",
        sender=msg.sender,
        kind="paused",
        body=(msg.subject or msg.text or "")[:200],
        external_id=msg.external_id,
    )
    await record_audit(
        session,
        firm_id=firm.id,
        actor=None,
        action="channel.intake_paused",
        target_type="client_company",
        target_id=str(client.id),
        after={"channel": msg.channel.value, "sender": msg.sender[:120], "files": len(msg.files)},
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
            body=bot_text(firm.locale, "intake_paused", client=client.name),
        )
    result.outcome = "paused"
    return True


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
    quarantine_reason: str | None = None
    if msg.channel == DocumentChannel.email:
        route = await _route_email(session, deps, msg)
        if route.problem == "unknown_recipient" or (route.problem is not None and route.firm is None):
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
                action=(
                    "channel.unknown_address" if route.problem == "unknown_recipient" else "channel.ambiguous"
                ),
                target_type="channel",
                target_id=route.address[:120],
                result="ignored",
            )
            result.outcome = "unknown_sender"
            return result
        if route.problem == "retired_address":
            assert route.firm is not None
            await set_tenant(session, route.firm.id)
            await _log(
                session,
                firm_id=route.firm.id,
                channel=channel,
                direction="in",
                sender=msg.sender,
                kind="retired_address",
                body=msg.subject or "",
                external_id=msg.external_id,
            )
            await record_audit(
                session,
                firm_id=route.firm.id,
                actor=None,
                action="channel.retired_address",
                target_type="client_company",
                target_id=str(route.client.id) if route.client else None,
                after={"address": route.address[:120], "sender": msg.sender[:120], "files": len(msg.files)},
                result="dropped",
            )
            result.outcome, result.firm_id = "dropped", route.firm.id
            return result
        firm, client, known = route.firm, route.client, route.known
        if route.problem == "ambiguous_recipient":
            quarantine_reason, known = "ambiguous_recipient", False
        elif route.flag == "retired_address_grace":
            assert firm is not None and client is not None
            await record_audit(
                session,
                firm_id=firm.id,
                actor=None,
                action="channel.retired_address_grace",
                target_type="client_company",
                target_id=str(client.id),
                after={"address": route.address[:120], "sender": msg.sender[:120]},
            )
    else:
        routed = await _route_chat(session, deps, msg, result)
        if routed is None:
            return result
        firm, client, known = routed

    assert firm is not None and (client is not None or quarantine_reason is not None)
    await set_tenant(session, firm.id)
    result.firm_id = firm.id
    if client is not None and await _paused(session, deps, result, firm, client, msg):
        return result
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
        assert client is not None  # chat channels always route to a client company
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
            client_company_id=client.id if client is not None else None,
            channel=msg.channel,
            sender_identity=msg.sender,
            status=DocumentStatus.received if known else DocumentStatus.quarantined,
            error=None if known else (quarantine_reason or "unknown_sender"),
        )
        doc.channel_bot_id = msg.bot_id
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
            after={
                "sender": msg.sender[:120],
                "documents": len(docs),
                "reason": quarantine_reason or "unknown_sender",
            },
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
            bot_text(firm.locale, "receipt_one_client", client=client.name)
            if len(docs) == 1 and client is not None
            else bot_text(firm.locale, "receipt_client", n=len(docs), client=client.name if client else "")
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
    """Request-info: the accountant's question goes back through the document's channel (and through
    the bot or number it came in on)."""
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


async def question_transports(
    request: Any, session: AsyncSession, firm: Firm, doc: Document
) -> dict[str, Transport]:
    """Transports for a question about ``doc``: its own bot or number, the dashboard SMTP for email,
    else the environment's."""
    from app.mail import MailerTransport

    out: dict[str, Transport] = dict(request.app.state.channels.transports())
    out["email"] = MailerTransport(request.app.state.mailer, firm.id)
    if doc.channel_bot_id is not None:
        from app.channels.bots import adapter_for, load

        await set_platform_bypass(session)
        found = await load(session, doc.channel_bot_id)
        await set_tenant(session, firm.id)
        if found is not None:
            out[found[0].channel] = adapter_for(
                request.app.state.master_key, found[1], found[0], request.app.state.channels.http
            )
    return out


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
