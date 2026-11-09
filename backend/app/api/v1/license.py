"""License page API. Never returns the key or the token."""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.deps import ActorDep, FirmPathDep, LicenseServiceDep, SessionDep, SettingsDep, require
from app.audit.service import record_audit
from app.auth.rbac import allowed
from app.licensing.constants import MIN_VALIDATE_INTERVAL
from app.licensing.installid import format_grouped
from app.licensing.messages import mark_seen, messages_with_seen
from app.licensing.offline import request_code_qr_svg
from app.licensing.service import human
from app.licensing.store import FirmSecrets
from app.models.firm import Firm
from app.tenancy.context import Actor

router = APIRouter(prefix="/firms/{firm_id}/license", tags=["license"])


class LicenseView(BaseModel):
    state: str
    reason: str | None
    core_on: bool
    trial: bool
    trial_days_left: int | None
    expires_at: int | None
    grace_days: int
    kh: str | None
    plan: str | None
    plan_name: str | None
    licensed_to: str | None
    items: dict[str, Any]
    features: dict[str, Any]
    use: str | None
    next_refresh_at: int | None
    verified_at: int
    hub_error: str | None
    hub_message: str | None
    installation_id: str
    installation_id_grouped: str
    has_key: bool
    messages: list[dict[str, Any]]
    store_links: dict[str, str]
    banner: dict[str, str] | None
    kind: str = "firm"


def _store_links(store_url: str, installation_id: str) -> dict[str, str]:
    base = store_url.rstrip("/")
    q = f"?installation={quote(installation_id)}"
    return {
        "buy": f"{base}/{q}",
        "trial": f"{base}/{q}&trial=1",
        "renew": f"{base}/{q}",
        "offline": f"{base}/offline",
        "addons": f"{base}/{q}#add-ons",
    }


def _banner(
    state: str, trial_days_left: int | None, hub_error: str | None, reason: str | None = None
) -> dict[str, str] | None:
    if state == "grace":
        return {
            "level": "warning",
            "code": "grace",
            "text": "The license could not be refreshed. InvoiceFlow keeps working during the grace period.",
        }
    if state in ("expired", "unlicensed", "invalid"):
        paused = "Intake and processing are paused; your data stays available."
        if state == "expired" and reason == "core_not_included":
            # spec §8.4: the token is valid but carries no `core` tool, which happens
            # when a license is issued from a plan without it. Not an ended license.
            return {
                "level": "error",
                "code": "core_not_included",
                "text": (
                    "This license does not include InvoiceFlow (its core tool). Ask whoever issued it "
                    f"to add the core tool, or buy InvoiceFlow in the store. {paused}"
                ),
            }
        text = {
            "expired": f"Your InvoiceFlow license has ended. {paused}",
            "unlicensed": f"InvoiceFlow is not licensed. {paused}",
            "invalid": f"The InvoiceFlow license could not be verified. {paused}",
        }[state]
        if hub_error in ("license_suspended", "instance_blocked", "license_revoked"):
            text += " Contact support."
        return {"level": "error", "code": state, "text": text}
    if trial_days_left is not None:
        return {"level": "info", "code": "trial", "text": f"Trial: {trial_days_left} day(s) left."}
    return None


async def _view(firm: Any, svc: Any, settings: Any) -> LicenseView:
    snap = svc.snapshot(firm)
    sec = FirmSecrets(svc.master, firm)
    d = snap.public_dict()
    if firm.kind == "platform":
        # the platform license only raises the number of firms; without one the platform runs 1 firm
        banner = None
        if snap.state in ("expired", "invalid") or (snap.state == "grace"):
            banner = _banner(snap.state, None, firm.hub_error, snap.reason)
        return LicenseView(
            **d,
            installation_id=firm.instance_id,
            installation_id_grouped=format_grouped(firm.instance_id),
            has_key=sec.key() is not None,
            messages=messages_with_seen(snap, firm),
            store_links={},
            banner=banner,
            kind="platform",
        )
    return LicenseView(
        **d,
        installation_id=firm.instance_id,
        installation_id_grouped=format_grouped(firm.instance_id),
        has_key=sec.key() is not None,
        messages=messages_with_seen(snap, firm),
        store_links=_store_links(settings.store_url, firm.instance_id),
        banner=_banner(snap.state, snap.trial_days_left, firm.hub_error, snap.reason),
    )


@router.get("", response_model=LicenseView)
async def get_license(
    firm: FirmPathDep, actor: ActorDep, svc: LicenseServiceDep, settings: SettingsDep
) -> LicenseView:
    if not (actor.is_platform_admin or allowed(actor.role, "firm.license.view")):
        raise HTTPException(403, {"error": "forbidden", "message": "You are not allowed to see the license."})
    return await _view(firm, svc, settings)


class ActivateIn(BaseModel):
    key: str = Field(min_length=1, max_length=200)


@router.post("/activate", response_model=LicenseView)
async def activate(
    body: ActivateIn,
    firm: FirmPathDep,
    session: SessionDep,
    svc: LicenseServiceDep,
    settings: SettingsDep,
    actor: Actor = Depends(require("firm.license.manage")),
) -> LicenseView:
    await svc.activate(session, firm, body.key, actor)
    return await _view(firm, svc, settings)


@router.post("/deactivate", response_model=LicenseView)
async def deactivate(
    firm: FirmPathDep,
    session: SessionDep,
    svc: LicenseServiceDep,
    settings: SettingsDep,
    actor: Actor = Depends(require("firm.license.manage")),
) -> LicenseView:
    await svc.deactivate(session, firm, actor)
    return await _view(firm, svc, settings)


class RefreshOutcome(BaseModel):
    """What "Check now" did. ``retry_at`` says when the next check is possible while the 15-minute
    rule (spec §12), a back-off or a block holds; the page shows it instead of staying silent."""

    ok: bool
    code: str
    message: str
    retry_at: int | None


class RefreshView(LicenseView):
    outcome: RefreshOutcome


def _retry_at(firm: Firm, code: str) -> int | None:
    if code == "too_soon" and firm.last_validate_call_at is not None:
        return int(firm.last_validate_call_at) + MIN_VALIDATE_INTERVAL
    if code == "backoff":
        return firm.backoff_until
    if code == "blocked":
        return firm.blocked_until
    if code == "network":
        return firm.next_refresh_at
    return None


@router.post("/refresh", response_model=RefreshView)
async def refresh_now(
    firm: FirmPathDep,
    session: SessionDep,
    svc: LicenseServiceDep,
    settings: SettingsDep,
    actor: Actor = Depends(require("firm.license.manage")),
) -> RefreshView:
    outcome = await svc.refresh(session, firm, reason="manual")
    await record_audit(
        session,
        firm_id=firm.id,
        actor=actor,
        action="license.refresh",
        target_type="firm",
        target_id=str(firm.id),
        result=outcome.code,
    )
    view = await _view(firm, svc, settings)
    return RefreshView(
        **view.model_dump(),
        outcome=RefreshOutcome(
            ok=outcome.ok,
            code=outcome.code,
            message=outcome.message or human(outcome.code),
            retry_at=None if outcome.ok else _retry_at(firm, outcome.code),
        ),
    )


class ImportIn(BaseModel):
    license_file: str = Field(min_length=10, max_length=65536)


@router.post("/import-file", response_model=LicenseView)
async def import_file(
    body: ImportIn,
    firm: FirmPathDep,
    session: SessionDep,
    svc: LicenseServiceDep,
    settings: SettingsDep,
    actor: Actor = Depends(require("firm.license.manage")),
) -> LicenseView:
    await svc.import_license_file(session, firm, body.license_file, actor)
    return await _view(firm, svc, settings)


class OfflineRequestIn(BaseModel):
    key: str | None = Field(default=None, max_length=200)


class OfflineRequestOut(BaseModel):
    request_code: str
    qr_svg: str
    instructions: str


@router.post("/offline-request", response_model=OfflineRequestOut)
async def offline_request(
    body: OfflineRequestIn,
    firm: FirmPathDep,
    session: SessionDep,
    svc: LicenseServiceDep,
    settings: SettingsDep,
    actor: Actor = Depends(require("firm.license.manage")),
) -> OfflineRequestOut:
    code = svc.offline_request_code(firm, body.key)
    await record_audit(
        session,
        firm_id=firm.id,
        actor=actor,
        action="license.offline_request",
        target_type="firm",
        target_id=str(firm.id),
    )
    return OfflineRequestOut(
        request_code=code,
        qr_svg=request_code_qr_svg(code),
        instructions=(
            f"Paste this code at {settings.store_url.rstrip('/')}/offline on any internet-connected device, "
            "then import the license file it returns here."
        ),
    )


@router.post("/messages/{message_id}/seen", status_code=204)
async def message_seen(
    message_id: str,
    firm: FirmPathDep,
    session: SessionDep,
    actor: Actor = Depends(require("firm.license.view")),
) -> None:
    mark_seen(firm, message_id[:120])
