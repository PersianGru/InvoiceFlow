"""License flows per firm: activate, validate (refresh), deactivate, offline files, revocations.

Error handling follows spec §6.4 exactly: only ``license_suspended``, ``license_revoked``,
``license_expired``, ``instance_blocked`` and ``not_activated`` followed by one failed re-activation
may remove a cached license. Network errors, 5xx, 429, ``project_paused`` and unknown codes keep the
cached token (grace rules apply) and back off (1 min, 5 min, 30 min, then 6 h, with jitter).
"""

from __future__ import annotations

import logging
import random
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.service import record_audit
from app.config import Settings
from app.licensing.client import HubClient, HubResponse, HubUnreachableError, InstanceInfo, new_nonce
from app.licensing.clock import Clock, system_clock
from app.licensing.constants import (
    BACKOFF_SCHEDULE,
    BLOCKED_RETRY_INTERVAL,
    DAY,
    MIN_VALIDATE_INTERVAL,
)
from app.licensing.instance import current_fingerprint
from app.licensing.keyformat import key_check_ok, key_hint, normalize_key
from app.licensing.keyring import TrustedKeyRing
from app.licensing.offline import LicenseFileError, make_request_code, parse_license_file
from app.licensing.state import LicenseSnapshot, LicenseStateCache, compute_snapshot
from app.licensing.store import FirmSecrets
from app.licensing.verifier import VerifyContext, VerifyResult, verify_license_token, verify_revocation_list
from app.models.firm import Firm
from app.models.license import LicenseEvent
from app.security.crypto import MasterKey
from app.tenancy.context import Actor

log = logging.getLogger("invoiceflow.licensing.service")

DEFINITIVE_ERRORS = frozenset({"license_suspended", "license_revoked", "license_expired", "instance_blocked"})
TRANSIENT_ERRORS = frozenset({"project_paused", "rate_limited"})


class LicenseError(Exception):
    """A flow could not complete. ``code`` is a hub error code or one of: invalid_key, invalid_token,
    network, no_key, no_token, bad_file, too_soon."""

    def __init__(self, code: str, message: str, *, hint: str | None = None, status: int = 400) -> None:
        super().__init__(code)
        self.code = code
        self.message = message
        self.hint = hint
        self.status = status


HUMAN_MESSAGES: dict[str, str] = {
    "invalid_key": "The license key looks mistyped. Check it and try again.",
    "invalid_token": "The hub's answer could not be verified. Try again later or contact support.",
    "network": (
        "The license hub could not be reached. Your current license keeps working during the grace period."
    ),
    "no_key": "Enter a license key first.",
    "no_token": "This firm has no active license.",
    "seat_limit_reached": "All seats of this license are in use. Deactivate another install first.",
    "license_suspended": "This license is suspended. Contact support.",
    "license_revoked": "This license was revoked. Contact support.",
    "license_expired": "This license has expired. Renew it in the store.",
    "license_not_started": "This license starts later.",
    "instance_blocked": "Support blocked this installation. Contact support.",
    "online_not_allowed": "This license only allows offline activation. Use the offline activation request.",
    "offline_not_allowed": "This license does not allow offline activation.",
    "offline_deactivation_requires_support": (
        "Offline activations are released by 21CEL support. Contact support to move this license."
    ),
    "activation_kind_mismatch": "This installation already holds a different kind of activation.",
    "invalid_instance": "The installation id was not accepted by the hub. Contact support.",
    "project_paused": "Licensing is paused on the hub side. Your current license keeps working.",
    "rate_limited": "Too many requests to the license hub. Try again in a few minutes.",
    "not_activated": "This installation is no longer activated.",
    "too_soon": "The license was checked less than 15 minutes ago.",
    "backoff": "The last check did not go through; the next attempt is scheduled.",
    "blocked": "Support blocked this installation. The next check is scheduled.",
    "offline": "An offline license is verified on this server; the hub is not contacted.",
    "ok": "The license was checked with the hub.",
    "bad_file": "This is not a valid InvoiceFlow license file.",
    "platform_license_on_firm": (
        "This is a platform license (number of firms). Activate it under Platform → License, not on a firm."
    ),
    "not_a_platform_license": (
        "This is a firm license. The platform needs a platform license issued by 21CEL on request."
    ),
}

PLATFORM_TOOL = "platform"


def is_platform_token(claims: Mapping[str, Any]) -> bool:
    """A platform license carries ``lic.features.platform === true``."""
    lic = claims.get("lic")
    features = lic.get("features") if isinstance(lic, Mapping) else None
    return isinstance(features, Mapping) and features.get(PLATFORM_TOOL) is True


def kind_mismatch(firm: Firm, claims: Mapping[str, Any]) -> str | None:
    """Platform tokens only on the platform row, firm tokens only on firms."""
    platform_token = is_platform_token(claims)
    if firm.kind == "platform" and not platform_token:
        return "not_a_platform_license"
    if firm.kind != "platform" and platform_token:
        return "platform_license_on_firm"
    return None


def _claim_int(claims: Mapping[str, Any], name: str, default: int) -> int:
    v = claims.get(name)
    return v if isinstance(v, int) and not isinstance(v, bool) else default


def human(code: str, fallback: str | None = None) -> str:
    return HUMAN_MESSAGES.get(code) or fallback or "The license hub refused the request."


@dataclass(slots=True)
class FlowOutcome:
    ok: bool
    code: str  # ok | <hub error> | network | ...
    snapshot: LicenseSnapshot
    message: str | None = None
    hint: str | None = None


class LicenseService:
    def __init__(
        self,
        settings: Settings,
        master: MasterKey,
        hub: HubClient,
        keyring: TrustedKeyRing,
        cache: LicenseStateCache,
        *,
        clock: Clock = system_clock,
        rng: random.Random | None = None,
    ) -> None:
        self.settings = settings
        self.master = master
        self.hub = hub
        self.keyring = keyring
        self.cache = cache
        self.clock = clock
        self._rng = rng or random.SystemRandom()

    # ---- helpers -------------------------------------------------------------------------
    def secrets(self, firm: Firm) -> FirmSecrets:
        return FirmSecrets(self.master, firm)

    def _instance(self, firm: Firm) -> InstanceInfo:
        name = firm.name if self.settings.license_send_instance_name else None
        return InstanceInfo(id=firm.instance_id, fp=current_fingerprint(self.settings, firm), name=name)

    def tool_for(self, firm: Firm) -> str:
        """The licensed tool: ``platform`` for the platform row, the core tool for firms."""
        return PLATFORM_TOOL if firm.kind == "platform" else self.settings.core_feature

    def _touch_clock(self, firm: Firm) -> int:
        now = self.clock()
        if now > firm.last_seen:
            firm.last_seen = now
        return now

    def snapshot(self, firm: Firm, secrets: FirmSecrets | None = None) -> LicenseSnapshot:
        now = self.clock()
        sec = secrets or self.secrets(firm)
        digest = sec.token_digest()
        cached = self.cache.get(str(firm.id), digest, now)
        if cached is not None:
            return cached
        snap = compute_snapshot(
            firm,
            sec.token(),
            product=self.settings.product_id,
            issuer=self.settings.hub_origin,
            keys=self.keyring.keys,
            now=now,
            core_tool=self.tool_for(firm),
        )
        if snap.claims and kind_mismatch(firm, snap.claims) is not None:
            snap = replace(snap, core_on=False, state="invalid", reason="wrong_license_kind")
        self.cache.put(str(firm.id), snap, digest, now)
        return snap

    def _persist_state(self, firm: Firm, snap: LicenseSnapshot) -> None:
        firm.license_state = snap.state
        firm.license_state_reason = snap.reason

    def _recompute(self, firm: Firm, sec: FirmSecrets) -> LicenseSnapshot:
        self.cache.invalidate(str(firm.id))
        snap = self.snapshot(firm, sec)
        self._persist_state(firm, snap)
        return snap

    def _jitter(self, seconds: int) -> int:
        return int(seconds * (1 + self._rng.uniform(-0.1, 0.1)))

    def _schedule_refresh(self, firm: Firm, claims: Mapping[str, Any], now: int) -> None:
        iat = _claim_int(claims, "iat", now)
        rfs = _claim_int(claims, "rfs", DAY) or DAY
        exp = _claim_int(claims, "exp", iat + rfs)
        target = min(iat + self._jitter(rfs), exp)
        firm.next_refresh_at = max(target, now + MIN_VALIDATE_INTERVAL)
        firm.last_refresh_at = now
        firm.refresh_failures = 0
        firm.backoff_until = None
        firm.blocked_until = None
        firm.hub_error = None
        firm.hub_message = None

    def _backoff(self, firm: Firm, now: int, error: str | None, message: str | None) -> None:
        firm.refresh_failures += 1
        delay = BACKOFF_SCHEDULE[min(firm.refresh_failures, len(BACKOFF_SCHEDULE)) - 1]
        firm.backoff_until = now + self._jitter(delay)
        firm.next_refresh_at = firm.backoff_until
        firm.hub_error = error
        firm.hub_message = message

    async def _event(
        self,
        session: AsyncSession,
        firm: Firm,
        kind: str,
        outcome: str,
        before: str,
        after: str,
        detail: dict[str, Any] | None = None,
    ) -> None:
        session.add(
            LicenseEvent(
                firm_id=firm.id,
                kind=kind,
                outcome=outcome,
                state_before=before,
                state_after=after,
                detail=detail,
            )
        )
        log.info("license %s firm=%s outcome=%s state %s -> %s", kind, firm.id, outcome, before, after)

    async def _verify_fresh(
        self, jws: str | None, firm: Firm, nonce: str | None, use: str, now: int
    ) -> VerifyResult:
        if jws is None:
            return VerifyResult("invalid", "format")
        ctx = VerifyContext(
            product=self.settings.product_id,
            issuer=self.settings.hub_origin,
            keys=self.keyring.keys,
            use="offline" if use == "offline" else "lease",
            instance_id=firm.instance_id,
            now=now,
            nonce=nonce,
            last_seen=firm.last_seen,
        )
        r = verify_license_token(jws, ctx)
        if r.state == "invalid" and r.reason == "unknown_kid":
            kid = r.header.get("kid") if isinstance(r.header, Mapping) else None
            if await self.keyring.ensure(kid if isinstance(kid, str) else None):
                r = verify_license_token(jws, replace(ctx, keys=self.keyring.keys))
        return r

    def _end_license(self, firm: Firm, sec: FirmSecrets, now: int, code: str, message: str | None) -> None:
        """Definitive answers (spec §6.4): remove the cached token; the user's data is untouched."""
        sec.clear_token()
        firm.hub_error = code
        firm.hub_message = message
        firm.refresh_failures = 0
        firm.backoff_until = None
        if code == "instance_blocked":
            firm.blocked_until = now + BLOCKED_RETRY_INTERVAL
            firm.next_refresh_at = firm.blocked_until
        else:
            firm.next_refresh_at = now + self._jitter(BACKOFF_SCHEDULE[-1])

    # ---- activate ------------------------------------------------------------------------
    async def activate(
        self, session: AsyncSession, firm: Firm, raw_key: str, actor: Actor | None
    ) -> FlowOutcome:
        prefix = self.settings.license_key_prefix
        canonical = normalize_key(raw_key, prefix)
        if not key_check_ok(canonical, prefix):
            await record_audit(
                session,
                firm_id=firm.id,
                actor=actor,
                action="license.activate",
                target_type="firm",
                target_id=str(firm.id),
                result="invalid_key",
            )
            raise LicenseError("invalid_key", human("invalid_key"))
        sec = self.secrets(firm)
        now = self._touch_clock(firm)
        before = firm.license_state
        nonce = new_nonce()
        try:
            resp = await self.hub.activate(canonical, self._instance(firm), nonce)
        except HubUnreachableError as e:
            await self._event(session, firm, "activate", f"network_{e.kind}", before, before)
            await record_audit(
                session,
                firm_id=firm.id,
                actor=actor,
                action="license.activate",
                target_type="firm",
                target_id=str(firm.id),
                result="network_error",
            )
            raise LicenseError("network", human("network"), status=503) from e
        if resp.ok:
            r = await self._verify_fresh(resp.token, firm, nonce, "lease", now)
            if r.state == "invalid":
                await self._event(session, firm, "activate", f"invalid_token_{r.reason}", before, before)
                await record_audit(
                    session,
                    firm_id=firm.id,
                    actor=actor,
                    action="license.activate",
                    target_type="firm",
                    target_id=str(firm.id),
                    result=f"invalid_token:{r.reason}",
                )
                raise LicenseError("invalid_token", human("invalid_token"), status=502)
            mismatch = kind_mismatch(firm, r.claims)
            if mismatch is not None:
                # release the seat again so the wrong kind of license holds nothing
                try:
                    await self.hub.deactivate(canonical, self._instance(firm))
                except HubUnreachableError:
                    pass
                await self._event(session, firm, "activate", mismatch, before, before)
                await record_audit(
                    session,
                    firm_id=firm.id,
                    actor=actor,
                    action="license.activate",
                    target_type="firm",
                    target_id=str(firm.id),
                    result=mismatch,
                )
                raise LicenseError(mismatch, human(mismatch), status=409)
            sec.set_key(canonical, key_hint(canonical, prefix))
            sec.set_token(resp.token or "", "lease", r.claims)
            self._schedule_refresh(firm, r.claims, now)
            firm.last_validate_call_at = now  # an activation is a check-in (spec §12: 15-minute rule)
            firm.revoked_by_list_at = None
            snap = self._recompute(firm, sec)
            await self._event(session, firm, "activate", "ok", before, snap.state, {"kh": snap.kh})
            await record_audit(
                session,
                firm_id=firm.id,
                actor=actor,
                action="license.activate",
                target_type="firm",
                target_id=str(firm.id),
                after={"state": snap.state, "kh": snap.kh, "license_id": snap.license_id},
            )
            return FlowOutcome(True, "ok", snap)
        return await self._handle_error(session, firm, sec, resp, now, "activate", actor, raise_error=True)

    # ---- validate / refresh --------------------------------------------------------------
    async def refresh(self, session: AsyncSession, firm: Firm, *, reason: str = "scheduled") -> FlowOutcome:
        """One refresh attempt (spec §8.3). Respects the 15-minute rule, back-off and the daily
        retry after ``instance_blocked``. Safe to call often: it does nothing when not due."""
        sec = self.secrets(firm)
        now = self._touch_clock(firm)
        before = firm.license_state
        key = sec.key()
        if firm.license_use == "offline":
            return FlowOutcome(True, "offline", self._recompute(firm, sec))
        if key is None:
            return FlowOutcome(False, "no_key", self._recompute(firm, sec))
        if (
            firm.last_validate_call_at is not None
            and now - firm.last_validate_call_at < MIN_VALIDATE_INTERVAL
        ):
            return FlowOutcome(False, "too_soon", self.snapshot(firm, sec))
        if firm.backoff_until is not None and now < firm.backoff_until:
            return FlowOutcome(False, "backoff", self.snapshot(firm, sec))
        if firm.blocked_until is not None and now < firm.blocked_until:
            return FlowOutcome(False, "blocked", self.snapshot(firm, sec))
        has_token = sec.token() is not None
        nonce = new_nonce()
        firm.last_validate_call_at = now
        try:
            if has_token or firm.blocked_until is None:
                resp = await self.hub.validate(key, self._instance(firm), nonce)
            else:
                resp = await self.hub.activate(key, self._instance(firm), nonce)
        except HubUnreachableError as e:
            self._backoff(firm, now, f"network_{e.kind}", None)
            snap = self._recompute(firm, sec)
            await self._event(
                session, firm, "validate", f"network_{e.kind}", before, snap.state, {"reason": reason}
            )
            return FlowOutcome(False, "network", snap, human("network"))
        if resp.ok:
            r = await self._verify_fresh(resp.token, firm, nonce, "lease", now)
            if r.state == "invalid":
                # a bad answer from a "successful" call: keep the cached token, treat as transient
                self._backoff(firm, now, f"invalid_token_{r.reason}", None)
                snap = self._recompute(firm, sec)
                await self._event(session, firm, "validate", f"invalid_token_{r.reason}", before, snap.state)
                return FlowOutcome(False, "invalid_token", snap, human("invalid_token"))
            sec.set_token(resp.token or "", "lease", r.claims)
            self._schedule_refresh(firm, r.claims, now)
            firm.revoked_by_list_at = None
            snap = self._recompute(firm, sec)
            await self._event(session, firm, "validate", "ok", before, snap.state, {"reason": reason})
            return FlowOutcome(True, "ok", snap)
        if resp.error == "not_activated" and resp.status == 404:
            return await self._reactivate_once(session, firm, sec, key, now, before)
        return await self._handle_error(session, firm, sec, resp, now, "validate", None, raise_error=False)

    async def _reactivate_once(
        self, session: AsyncSession, firm: Firm, sec: FirmSecrets, key: str, now: int, before: str
    ) -> FlowOutcome:
        """Spec §6.4 ``not_activated``: ``/activate`` once; any 4xx makes the install unlicensed at once."""
        firm.reactivation_attempted_at = now
        nonce = new_nonce()
        try:
            resp = await self.hub.activate(key, self._instance(firm), nonce)
        except HubUnreachableError as e:
            self._backoff(firm, now, f"network_{e.kind}", None)
            snap = self._recompute(firm, sec)
            await self._event(session, firm, "reactivate", f"network_{e.kind}", before, snap.state)
            return FlowOutcome(False, "network", snap, human("network"))
        if resp.ok:
            r = await self._verify_fresh(resp.token, firm, nonce, "lease", now)
            if r.state != "invalid":
                sec.set_token(resp.token or "", "lease", r.claims)
                self._schedule_refresh(firm, r.claims, now)
                snap = self._recompute(firm, sec)
                await self._event(session, firm, "reactivate", "ok", before, snap.state)
                return FlowOutcome(True, "ok", snap)
            self._backoff(firm, now, f"invalid_token_{r.reason}", None)
            snap = self._recompute(firm, sec)
            return FlowOutcome(False, "invalid_token", snap, human("invalid_token"))
        if 400 <= resp.status < 500:
            self._end_license(firm, sec, now, "not_activated", resp.message)
            snap = self._recompute(firm, sec)
            await self._event(session, firm, "reactivate", resp.error or "http_error", before, snap.state)
            return FlowOutcome(
                False, resp.error or "not_activated", snap, resp.message or human("not_activated")
            )
        self._backoff(firm, now, resp.error, resp.message)
        snap = self._recompute(firm, sec)
        await self._event(session, firm, "reactivate", resp.error or "http_error", before, snap.state)
        return FlowOutcome(False, resp.error or "http_error", snap, resp.message)

    async def _handle_error(
        self,
        session: AsyncSession,
        firm: Firm,
        sec: FirmSecrets,
        resp: HubResponse,
        now: int,
        kind: str,
        actor: Actor | None,
        *,
        raise_error: bool,
    ) -> FlowOutcome:
        before = firm.license_state
        code = resp.error or "http_error"
        if code in DEFINITIVE_ERRORS:
            self._end_license(firm, sec, now, code, resp.message)
        else:
            # transient (429, 5xx, project_paused) and unknown codes: keep the cached token, use grace
            self._backoff(firm, now, code, resp.message)
        snap = self._recompute(firm, sec)
        await self._event(session, firm, kind, code, before, snap.state, {"status": resp.status})
        if raise_error:
            await record_audit(
                session,
                firm_id=firm.id,
                actor=actor,
                action=f"license.{kind}",
                target_type="firm",
                target_id=str(firm.id),
                result=code,
            )
            hint = "deactivate_other_install" if code == "seat_limit_reached" else None
            status = 503 if resp.is_transient else 409 if code == "seat_limit_reached" else 403
            raise LicenseError(code, resp.message or human(code), hint=hint, status=status)
        hint = "deactivate_other_install" if code == "seat_limit_reached" else None
        return FlowOutcome(False, code, snap, resp.message or human(code), hint)

    # ---- deactivate ("move license") ------------------------------------------------------
    async def deactivate(self, session: AsyncSession, firm: Firm, actor: Actor | None) -> FlowOutcome:
        sec = self.secrets(firm)
        self._touch_clock(firm)
        before = firm.license_state
        key = sec.key()
        if firm.license_use == "offline":
            await record_audit(
                session,
                firm_id=firm.id,
                actor=actor,
                action="license.deactivate",
                target_type="firm",
                target_id=str(firm.id),
                result="offline_deactivation_requires_support",
            )
            raise LicenseError(
                "offline_deactivation_requires_support",
                human("offline_deactivation_requires_support"),
                status=403,
            )
        if key is None:
            raise LicenseError("no_key", human("no_key"), status=404)
        try:
            resp = await self.hub.deactivate(key, self._instance(firm))
        except HubUnreachableError as e:
            await record_audit(
                session,
                firm_id=firm.id,
                actor=actor,
                action="license.deactivate",
                target_type="firm",
                target_id=str(firm.id),
                result="network_error",
            )
            raise LicenseError("network", human("network"), status=503) from e
        if resp.ok or resp.error in ("not_activated", "invalid_key"):
            sec.clear_token()
            sec.clear_key()
            firm.license_id = None
            firm.next_refresh_at = None
            firm.backoff_until = None
            firm.refresh_failures = 0
            firm.hub_error = None
            firm.hub_message = None
            snap = self._recompute(firm, sec)
            await self._event(session, firm, "deactivate", "ok", before, snap.state)
            await record_audit(
                session,
                firm_id=firm.id,
                actor=actor,
                action="license.deactivate",
                target_type="firm",
                target_id=str(firm.id),
                after={"state": snap.state},
            )
            return FlowOutcome(True, "ok", snap)
        await self._event(session, firm, "deactivate", resp.error or "http_error", before, before)
        await record_audit(
            session,
            firm_id=firm.id,
            actor=actor,
            action="license.deactivate",
            target_type="firm",
            target_id=str(firm.id),
            result=resp.error or "http_error",
        )
        raise LicenseError(
            resp.error or "http_error",
            resp.message or human(resp.error or ""),
            status=503 if resp.is_transient else 403,
        )

    # ---- offline (spec §9) -----------------------------------------------------------------
    def offline_request_code(self, firm: Firm, raw_key: str | None) -> str:
        sec = self.secrets(firm)
        prefix = self.settings.license_key_prefix
        key = normalize_key(raw_key, prefix) if raw_key else sec.key()
        if not key:
            raise LicenseError("no_key", human("no_key"))
        if not key_check_ok(key, prefix):
            raise LicenseError("invalid_key", human("invalid_key"))
        if sec.key() != key:
            sec.set_key(key, key_hint(key, prefix))
        now = self._touch_clock(firm)
        return make_request_code(
            product=self.settings.product_id,
            key=key,
            instance_id=firm.instance_id,
            fp=current_fingerprint(self.settings, firm),
            app_version=self.settings.app_version,
            now=now,
            name=firm.name if self.settings.license_send_instance_name else "",
        )

    async def import_license_file(
        self, session: AsyncSession, firm: Firm, text: str, actor: Actor | None
    ) -> FlowOutcome:
        sec = self.secrets(firm)
        now = self._touch_clock(firm)
        before = firm.license_state
        try:
            jws = parse_license_file(text)
        except LicenseFileError as e:
            await record_audit(
                session,
                firm_id=firm.id,
                actor=actor,
                action="license.import_file",
                target_type="firm",
                target_id=str(firm.id),
                result="bad_file",
            )
            raise LicenseError("bad_file", human("bad_file")) from e
        r = await self._verify_fresh(jws, firm, None, "offline", now)
        if r.state == "invalid":
            await self._event(session, firm, "offline_import", f"invalid_{r.reason}", before, before)
            await record_audit(
                session,
                firm_id=firm.id,
                actor=actor,
                action="license.import_file",
                target_type="firm",
                target_id=str(firm.id),
                result=f"invalid:{r.reason}",
            )
            raise LicenseError("invalid_token", f"The license file was not accepted ({r.reason}).")
        if r.state in ("expired", "unlicensed"):
            await record_audit(
                session,
                firm_id=firm.id,
                actor=actor,
                action="license.import_file",
                target_type="firm",
                target_id=str(firm.id),
                result=r.state,
            )
            raise LicenseError("license_expired", "This license file has already expired.")
        mismatch = kind_mismatch(firm, r.claims)
        if mismatch is not None:
            await record_audit(
                session,
                firm_id=firm.id,
                actor=actor,
                action="license.import_file",
                target_type="firm",
                target_id=str(firm.id),
                result=mismatch,
            )
            raise LicenseError(mismatch, human(mismatch), status=409)
        sec.set_token(jws, "offline", r.claims)
        firm.next_refresh_at = None
        firm.backoff_until = None
        firm.refresh_failures = 0
        firm.hub_error = None
        firm.hub_message = None
        firm.revoked_by_list_at = None
        snap = self._recompute(firm, sec)
        await self._event(session, firm, "offline_import", "ok", before, snap.state, {"kh": snap.kh})
        await record_audit(
            session,
            firm_id=firm.id,
            actor=actor,
            action="license.import_file",
            target_type="firm",
            target_id=str(firm.id),
            after={"state": snap.state, "kh": snap.kh, "license_id": snap.license_id},
        )
        return FlowOutcome(True, "ok", snap)

    # ---- revocation list (spec §10) ---------------------------------------------------------
    async def apply_revocation_list(self, session: AsyncSession, firm: Firm, list_jws: str) -> bool:
        """Stops the firm's license when its ``sub`` or ``act`` is listed. Returns True when it did."""
        sec = self.secrets(firm)
        now = self._touch_clock(firm)
        if sec.token() is None:
            return False
        r = verify_revocation_list(
            list_jws,
            product=self.settings.product_id,
            keys=self.keyring.keys,
            now=now,
            sub=firm.license_id,
            act=firm.activation_id,
        )
        if not r.ok or not r.revoked:
            return False
        before = firm.license_state
        sec.clear_token()
        firm.revoked_by_list_at = now
        firm.hub_error = "license_revoked"
        firm.hub_message = "This license or activation was revoked by 21CEL."
        firm.next_refresh_at = now + self._jitter(BACKOFF_SCHEDULE[-1])
        snap = self._recompute(firm, sec)
        await self._event(session, firm, "revocation", "revoked", before, snap.state)
        await record_audit(
            session,
            firm_id=firm.id,
            actor=None,
            action="license.revoked_by_list",
            target_type="firm",
            target_id=str(firm.id),
            after={"state": snap.state},
        )
        return True
