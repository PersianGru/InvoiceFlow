"""Settings in the dashboard: platform defaults (super admin) and each firm's
own mail settings, backup target and inbound domains (firm owner). Secrets are write-only."""

from __future__ import annotations

import asyncio
import imaplib
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import FirmDep, SessionDep, StoreDep, require
from app.audit.service import record_audit
from app.channels.imap import ImapMailbox
from app.db.rls import set_platform_bypass, set_tenant
from app.domains import (
    DnsChecker,
    DnsPythonChecker,
    DomainError,
    check_domain,
    domain_view,
    firm_domains,
    remove_custom,
    rename_subdomain,
    root_domain,
    set_custom,
    set_root,
)
from app.mail import SmtpMailer
from app.models.config import ConfigEntry
from app.models.firm import Firm
from app.platform import get_platform
from app.settings_store import SECTIONS, Section, SettingsError, SettingsStore
from app.tenancy.context import Actor

router = APIRouter(tags=["settings"])

FIRM_SECTIONS = ("smtp", "imap", "backup")
PLATFORM_SECTIONS = ("smtp", "imap", "inbound_webhook", "ai", "backup")


class SectionIn(BaseModel):
    values: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True
    clear_secrets: list[str] = Field(default_factory=list)


class TestMailIn(BaseModel):
    to: EmailStr


class DomainIn(BaseModel):
    domain: str = Field(min_length=3, max_length=253)
    grace_days: int = Field(default=0, ge=0, le=14)


class LabelIn(BaseModel):
    label: str = Field(min_length=1, max_length=63)
    grace_days: int = Field(default=0, ge=0, le=14)


def _dns(request: Request) -> DnsChecker:
    return getattr(request.app.state, "dns", None) or DnsPythonChecker()


async def _section_view(session: AsyncSession, store: SettingsStore, firm: Firm, key: str) -> dict[str, Any]:
    own = await store.own_in(session, firm, key)
    if own is not None and own.enabled:
        effective = own
    elif firm.is_platform:
        effective = Section(key, {f.name: f.default for f in SECTIONS[key].fields if f.kind != "secret"})
    else:
        effective = await store.effective(key, None)
    return {
        "key": key,
        "own": own.public() if own is not None else None,
        "own_enabled": own.enabled if own is not None else False,
        "effective_source": effective.source,
        "effective": effective.public(),
        "fields": [{"name": f.name, "kind": f.kind} for f in SECTIONS[key].fields],
    }


async def _entry_source(session: AsyncSession, firm_id: Any, key: str) -> str | None:
    from sqlalchemy import select

    row = (
        await session.execute(
            select(ConfigEntry.source).where(ConfigEntry.firm_id == firm_id, ConfigEntry.key == key)
        )
    ).first()
    return row[0] if row else None


async def _platform(session: AsyncSession) -> Firm:
    await set_platform_bypass(session)
    platform = await get_platform(session)
    if platform is None:
        raise HTTPException(409, {"error": "no_platform", "message": "The platform is not set up yet."})
    return platform


def _bad(e: Exception) -> HTTPException:
    return HTTPException(422, {"error": "invalid_settings", "message": str(e)})


async def _test_imap(section: Section) -> dict[str, Any]:
    if not section.get("host"):
        return {"ok": False, "message": "No IMAP server is set."}
    try:
        box = await asyncio.to_thread(ImapMailbox.from_section, section)
        await asyncio.to_thread(box.close)
        return {"ok": True, "message": "Signed in to the mailbox."}
    except (OSError, imaplib.IMAP4.error) as e:
        return {"ok": False, "message": f"The mailbox could not be opened ({type(e).__name__})."}


async def _test_smtp(store: SettingsStore, section: Section, to: str) -> dict[str, Any]:
    if not section.get("host") or not section.get("from_address"):
        return {"ok": False, "message": "No SMTP server or sender address is set."}
    ok = await SmtpMailer(store).test(section, to)
    return {"ok": ok, "message": "Test email sent." if ok else "The SMTP server refused the message."}


# ---- platform ------------------------------------------------------------------------------------
@router.get("/platform/settings")
async def platform_settings(
    session: SessionDep, store: StoreDep, actor: Actor = Depends(require("platform.admin"))
) -> dict[str, Any]:
    platform = await _platform(session)
    sections = {}
    for key in PLATFORM_SECTIONS:
        view = await _section_view(session, store, platform, key)
        view["source"] = await _entry_source(session, platform.id, key)
        sections[key] = view
    root = await root_domain(session)
    return {"sections": sections, "root_domain": domain_view(root) if root else None}


@router.put("/platform/settings/{key}")
async def put_platform_section(
    key: str,
    body: SectionIn,
    session: SessionDep,
    store: StoreDep,
    actor: Actor = Depends(require("platform.admin")),
) -> dict[str, Any]:
    if key not in PLATFORM_SECTIONS:
        raise HTTPException(404, {"error": "not_found", "message": "Unknown settings section."})
    platform = await _platform(session)
    try:
        await store.write(
            session,
            platform,
            key,
            body.values,
            enabled=body.enabled,
            clear_secrets=tuple(body.clear_secrets),
            user_id=actor.user_id,
        )
    except SettingsError as e:
        raise _bad(e) from e
    await record_audit(
        session,
        firm_id=None,
        actor=actor,
        action="platform.settings",
        target_type="settings",
        target_id=key,
        after={"fields": sorted(body.values), "enabled": body.enabled, "cleared": body.clear_secrets},
    )
    return await _section_view(session, store, platform, key)


@router.post("/platform/settings/smtp/test")
async def test_platform_smtp(
    body: TestMailIn, session: SessionDep, store: StoreDep, actor: Actor = Depends(require("platform.admin"))
) -> dict[str, Any]:
    await _platform(session)
    return await _test_smtp(store, await store.effective("smtp", None), body.to)


@router.post("/platform/settings/imap/test")
async def test_platform_imap(
    session: SessionDep, store: StoreDep, actor: Actor = Depends(require("platform.admin"))
) -> dict[str, Any]:
    await _platform(session)
    return await _test_imap(await store.effective("imap", None))


@router.put("/platform/domain")
async def put_root_domain(
    body: DomainIn, request: Request, session: SessionDep, actor: Actor = Depends(require("platform.admin"))
) -> dict[str, Any]:
    platform = await _platform(session)
    try:
        root = await set_root(session, platform, body.domain, body.grace_days)
    except DomainError as e:
        raise _bad(e) from e
    await check_domain(session, root, _dns(request))
    await record_audit(
        session,
        firm_id=None,
        actor=actor,
        action="platform.domain",
        target_type="domain",
        target_id=root.domain,
        after={"status": root.status},
    )
    return domain_view(root)


@router.post("/platform/domain/check")
async def check_root_domain(
    request: Request, session: SessionDep, actor: Actor = Depends(require("platform.admin"))
) -> dict[str, Any]:
    await _platform(session)
    root = await root_domain(session)
    if root is None:
        raise HTTPException(404, {"error": "not_found", "message": "Set the platform domain first."})
    await check_domain(session, root, _dns(request))
    return domain_view(root)


# ---- firm -----------------------------------------------------------------------------------------
@router.get("/firm-settings")
async def firm_settings(
    request: Request,
    session: SessionDep,
    firm: FirmDep,
    store: StoreDep,
    actor: Actor = Depends(require("settings.manage")),
) -> dict[str, Any]:
    sections = {key: await _section_view(session, store, firm, key) for key in FIRM_SECTIONS}
    domains = [domain_view(d) for d in await firm_domains(session, firm.id, include_retired=True)]
    await set_platform_bypass(session)
    root = await root_domain(session)
    await set_tenant(session, firm.id)
    return {
        "sections": sections,
        "domains": domains,
        "root_domain": root.domain if root is not None else None,
        "legacy_domain": request.app.state.settings.inbound_email_domain if root is None else None,
    }


@router.put("/firm-settings/{key}")
async def put_firm_section(
    key: str,
    body: SectionIn,
    session: SessionDep,
    firm: FirmDep,
    store: StoreDep,
    actor: Actor = Depends(require("settings.manage")),
) -> dict[str, Any]:
    if key not in FIRM_SECTIONS:
        raise HTTPException(404, {"error": "not_found", "message": "Unknown settings section."})
    try:
        await store.write(
            session,
            firm,
            key,
            body.values,
            enabled=body.enabled,
            clear_secrets=tuple(body.clear_secrets),
            user_id=actor.user_id,
        )
    except SettingsError as e:
        raise _bad(e) from e
    await record_audit(
        session,
        firm_id=firm.id,
        actor=actor,
        action="firm.settings_section",
        target_type="settings",
        target_id=key,
        after={"fields": sorted(body.values), "enabled": body.enabled, "cleared": body.clear_secrets},
    )
    return await _section_view(session, store, firm, key)


@router.delete("/firm-settings/{key}", status_code=204)
async def delete_firm_section(
    key: str,
    session: SessionDep,
    firm: FirmDep,
    store: StoreDep,
    actor: Actor = Depends(require("settings.manage")),
) -> None:
    """Back to the platform default."""
    if key not in FIRM_SECTIONS:
        raise HTTPException(404, {"error": "not_found", "message": "Unknown settings section."})
    if await store.delete(session, firm, key):
        await record_audit(
            session,
            firm_id=firm.id,
            actor=actor,
            action="firm.settings_section_removed",
            target_type="settings",
            target_id=key,
        )


@router.post("/firm-settings/smtp/test")
async def test_firm_smtp(
    body: TestMailIn, firm: FirmDep, store: StoreDep, actor: Actor = Depends(require("settings.manage"))
) -> dict[str, Any]:
    return await _test_smtp(store, await store.effective("smtp", firm.id), body.to)


@router.post("/firm-settings/imap/test")
async def test_firm_imap(
    firm: FirmDep, store: StoreDep, actor: Actor = Depends(require("settings.manage"))
) -> dict[str, Any]:
    return await _test_imap(await store.effective("imap", firm.id))


async def _domain_change(
    session: AsyncSession, firm: Firm, actor: Actor, action: str, after: dict[str, Any]
) -> None:
    await set_tenant(session, firm.id)
    await record_audit(
        session,
        firm_id=firm.id,
        actor=actor,
        action=action,
        target_type="domain",
        target_id=str(after.get("domain")),
        after=after,
    )


@router.patch("/firm-settings/domains/subdomain")
async def rename_firm_subdomain(
    body: LabelIn, session: SessionDep, firm: FirmDep, actor: Actor = Depends(require("settings.manage"))
) -> dict[str, Any]:
    await set_platform_bypass(session)
    try:
        d = await rename_subdomain(session, firm, body.label, body.grace_days)
    except DomainError as e:
        raise _bad(e) from e
    view = domain_view(d)
    await _domain_change(
        session, firm, actor, "firm.domain_rename", {"domain": d.domain, "grace_days": body.grace_days}
    )
    return view


@router.put("/firm-settings/domains/custom")
async def put_custom_domain(
    body: DomainIn,
    request: Request,
    session: SessionDep,
    firm: FirmDep,
    actor: Actor = Depends(require("settings.manage")),
) -> dict[str, Any]:
    await set_platform_bypass(session)
    try:
        d = await set_custom(session, firm, body.domain, body.grace_days)
    except DomainError as e:
        raise _bad(e) from e
    await check_domain(session, d, _dns(request))
    view = domain_view(d)
    await _domain_change(session, firm, actor, "firm.domain_custom", {"domain": d.domain, "status": d.status})
    return view


@router.post("/firm-settings/domains/custom/check")
async def check_custom_domain(
    request: Request, session: SessionDep, firm: FirmDep, actor: Actor = Depends(require("settings.manage"))
) -> dict[str, Any]:
    d = next((x for x in await firm_domains(session, firm.id) if x.kind == "custom"), None)
    if d is None:
        raise HTTPException(404, {"error": "not_found", "message": "Add your domain first."})
    await check_domain(session, d, _dns(request))
    if d.status == "verified":
        await _domain_change(session, firm, actor, "firm.domain_verified", {"domain": d.domain})
    return domain_view(d)


@router.delete("/firm-settings/domains/custom", status_code=204)
async def delete_custom_domain(
    session: SessionDep,
    firm: FirmDep,
    grace_days: int = 0,
    actor: Actor = Depends(require("settings.manage")),
) -> None:
    if await remove_custom(session, firm, max(0, min(grace_days, 14))):
        await _domain_change(
            session, firm, actor, "firm.domain_removed", {"domain": "custom", "grace_days": grace_days}
        )
