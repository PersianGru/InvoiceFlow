"""The firm's license state, computed from the stored token with the injected clock and cached in
memory: re-verified whenever the token changes and at least every 5 minutes.
Entitlements come only from the verified claims (spec §8.4), never from the database alone."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import Any

from app.licensing.constants import CORE_TOOL, DAY, STATE_CACHE_SECONDS
from app.licensing.keys import TrustedKeys
from app.licensing.verifier import (
    Use,
    VerifyContext,
    effective_now,
    tool_active,
    tool_expiry,
    tool_included,
    verify_license_token,
)
from app.models.firm import Firm

log = logging.getLogger("invoiceflow.licensing.state")

DISPLAY_STATES = ("valid", "grace", "expired", "unlicensed", "invalid")

# Entitlement keys and their defaults when the token carries no value
LIMIT_FEATURES = ("max_client_companies", "max_documents_per_month", "max_staff_users")
BOOL_FEATURES = ("channels", "connectors", "cloud_ai", "api", "sso")


@dataclass(frozen=True, slots=True)
class LicenseSnapshot:
    firm_id: str
    state: str  # valid | grace | expired | unlicensed | invalid
    reason: str | None
    core_on: bool
    trial: bool
    expires_at: int | None
    grace_days: int
    kh: str | None
    plan: str | None
    plan_name: str | None
    licensed_to: str | None
    items: dict[str, Any]
    features: dict[str, Any]
    messages: list[dict[str, Any]]
    verified_at: int
    now: int
    use: str | None
    license_id: str | None
    activation_id: str | None
    next_refresh_at: int | None
    hub_error: str | None
    hub_message: str | None
    claims: Mapping[str, Any] = field(default_factory=dict)
    core_tool: str = CORE_TOOL

    @property
    def licensed(self) -> bool:
        return self.core_on

    def feature_on(self, feature: str) -> bool:
        if feature == CORE_TOOL or feature == self.core_tool:
            return self.core_on
        if not self.core_on:
            return False
        return (
            tool_active(self.claims, feature, self.now)
            if feature in self.items
            else self.features.get(feature) is True
        )

    def limit(self, name: str) -> int:
        """0 = unlimited. Missing = 0 (unlimited) by default."""
        v = self.features.get(name, 0)
        if isinstance(v, bool) or not isinstance(v, int | float):
            return 0
        return max(0, int(v))

    @property
    def trial_days_left(self) -> int | None:
        if not self.trial or self.expires_at is None:
            return None
        return max(0, (self.expires_at - self.now + DAY - 1) // DAY)

    def public_dict(self) -> dict[str, Any]:
        """What the License page and the health page show: never the key or token."""
        return {
            "state": self.state,
            "reason": self.reason,
            "core_on": self.core_on,
            "trial": self.trial,
            "trial_days_left": self.trial_days_left,
            "expires_at": self.expires_at,
            "grace_days": self.grace_days,
            "kh": self.kh,
            "plan": self.plan,
            "plan_name": self.plan_name,
            "licensed_to": self.licensed_to,
            "items": self.items,
            "features": self.features,
            "use": self.use,
            "next_refresh_at": self.next_refresh_at,
            "verified_at": self.verified_at,
            "hub_error": self.hub_error,
            "hub_message": self.hub_message,
        }


def unlicensed_snapshot(firm: Firm, now: int, reason: str = "no_token") -> LicenseSnapshot:
    return LicenseSnapshot(
        firm_id=str(firm.id),
        state="unlicensed",
        reason=reason,
        core_on=False,
        trial=False,
        expires_at=None,
        grace_days=0,
        kh=firm.key_hint,
        plan=None,
        plan_name=None,
        licensed_to=None,
        items={},
        features={},
        messages=[],
        verified_at=now,
        now=now,
        use=None,
        license_id=firm.license_id,
        activation_id=None,
        next_refresh_at=firm.next_refresh_at,
        hub_error=firm.hub_error,
        hub_message=firm.hub_message,
    )


def compute_snapshot(
    firm: Firm,
    token: str | None,
    *,
    product: str,
    issuer: str,
    keys: TrustedKeys,
    now: int,
    core_tool: str = CORE_TOOL,
) -> LicenseSnapshot:
    if token is None:
        return unlicensed_snapshot(firm, now)
    use: Use = "offline" if firm.license_use == "offline" else "lease"
    ctx = VerifyContext(
        product=product,
        issuer=issuer,
        keys=keys,
        use=use,
        instance_id=firm.instance_id,
        now=now,
        last_seen=firm.last_seen,
    )
    r = verify_license_token(token, ctx)
    eff_now = effective_now(now, firm.last_seen)
    if r.state == "invalid":
        return replace(unlicensed_snapshot(firm, now, reason=r.reason or "invalid"), state="invalid")
    c = r.claims
    lic_raw, to_raw = c.get("lic"), c.get("to")
    lic: Mapping[str, Any] = lic_raw if isinstance(lic_raw, Mapping) else {}
    to: Mapping[str, Any] = to_raw if isinstance(to_raw, Mapping) else {}
    features_raw, items_raw = lic.get("features"), lic.get("items")
    features: dict[str, Any] = dict(features_raw) if isinstance(features_raw, Mapping) else {}
    items: dict[str, Any] = dict(items_raw) if isinstance(items_raw, Mapping) else {}
    core_on = r.licensed and tool_active(c, core_tool, now, firm.last_seen)
    state, reason = r.state, r.reason
    if r.licensed and not core_on:
        # The token is within exp but the core tool is not on (spec §8.4): either the license never
        # carried it (a license issued from a plan without the tool) or its item has ended.
        state = "expired"
        reason = "core_ended" if tool_included(c, core_tool) else "core_not_included"
    expires = tool_expiry(c, core_tool)
    grace_raw = lic.get("grace_days")
    grace_days = grace_raw if isinstance(grace_raw, int) and not isinstance(grace_raw, bool) else 0
    messages = [
        {"id": str(m.get("id")), "level": str(m.get("level", "info")), "text": str(m.get("text", ""))}
        for m in (c.get("msg") or [])
        if isinstance(m, Mapping) and m.get("id")
    ][:3]
    return LicenseSnapshot(
        firm_id=str(firm.id),
        state=state,
        reason=reason,
        core_on=core_on,
        trial=lic.get("trial") is True,
        expires_at=expires,
        grace_days=grace_days,
        kh=str(c.get("kh")) if c.get("kh") else firm.key_hint,
        plan=str(lic.get("plan")) if lic.get("plan") else None,
        plan_name=str(lic.get("plan_name")) if lic.get("plan_name") else None,
        licensed_to=(
            str(to.get("company") or to.get("name")) if (to.get("company") or to.get("name")) else None
        ),
        items=items,
        features=features,
        messages=messages,
        verified_at=now,
        now=eff_now,
        use=use,
        license_id=str(c.get("sub")) if c.get("sub") else firm.license_id,
        activation_id=str(c.get("act")) if c.get("act") else firm.activation_id,
        next_refresh_at=firm.next_refresh_at,
        hub_error=firm.hub_error,
        hub_message=firm.hub_message,
        claims=c,
        core_tool=core_tool,
    )


@dataclass(slots=True)
class _Entry:
    snapshot: LicenseSnapshot
    computed_at: int
    token_digest: str | None


class LicenseStateCache:
    """Per-process cache keyed by firm id; recomputed after ``ttl`` seconds or when the token changed."""

    def __init__(self, ttl: int = STATE_CACHE_SECONDS) -> None:
        self._ttl = ttl
        self._entries: dict[str, _Entry] = {}

    def get(self, firm_id: str, token_digest: str | None, now: int) -> LicenseSnapshot | None:
        e = self._entries.get(firm_id)
        if (
            e is None
            or e.token_digest != token_digest
            or now - e.computed_at >= self._ttl
            or now < e.computed_at
        ):
            return None
        return e.snapshot

    def put(self, firm_id: str, snapshot: LicenseSnapshot, token_digest: str | None, now: int) -> None:
        self._entries[firm_id] = _Entry(snapshot, now, token_digest)

    def invalidate(self, firm_id: str) -> None:
        self._entries.pop(firm_id, None)

    def clear(self) -> None:
        self._entries.clear()
