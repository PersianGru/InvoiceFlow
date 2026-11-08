"""License token verification (spec §8.2, §8.4, §10). Pure, clock-injected, never throws.

Every step of §8.2 is implemented in ``verify_license_token``. The result's ``reason`` uses the
spec's vocabulary (``format alg_or_typ unknown_kid bad_signature version aud iss use bind nonce
not_yet_valid``). Fuzzed or hostile input gives ``state = "invalid"``; nothing propagates to callers.
Ed25519 is verified with ``cryptography`` directly: no JOSE library, so ``alg: none`` cannot exist.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from app.licensing.constants import (
    DAY,
    MAX_TOKEN_BYTES,
    REVOCATION_TYP,
    SIGNING_ALG,
    TOKEN_CLAIM_V,
    TOKEN_TYP,
)
from app.licensing.keys import TrustedKeys

LicenseState = Literal["valid", "grace", "expired", "unlicensed", "invalid"]
Use = Literal["lease", "offline"]
_B64URL_ALPHABET = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_")


@dataclass(frozen=True, slots=True)
class VerifyContext:
    """Everything the verifier needs; the clock is injected (``now``, ``last_seen``)."""

    product: str
    issuer: str
    keys: TrustedKeys
    use: Use
    instance_id: str
    now: int
    domain: str | None = None
    nonce: str | None = None  # None: no nonce was sent
    last_seen: int = 0


@dataclass(frozen=True, slots=True)
class VerifyResult:
    state: LicenseState
    reason: str | None = None
    claims: Mapping[str, Any] = field(default_factory=dict)
    header: Mapping[str, Any] = field(default_factory=dict)

    @property
    def licensed(self) -> bool:
        """valid or grace: the product runs (callers still check tools with ``tool_active``)."""
        return self.state in ("valid", "grace")


def b64url_decode(s: str) -> bytes:
    if not isinstance(s, str) or any(c not in _B64URL_ALPHABET for c in s):
        raise ValueError("not base64url")
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def b64url_encode(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def instance_hash(instance_id: str) -> str:
    return b64url_encode(hashlib.sha256(instance_id.encode()).digest())


def norm_domain(domain: str | None) -> str:
    d = (domain or "").strip().lower()
    return d[4:] if d.startswith("www.") else d


def _decode_part(part: str) -> Any:
    raw = b64url_decode(part)
    return json.loads(raw.decode("utf-8"))


@dataclass(frozen=True, slots=True)
class _Split:
    header: Mapping[str, Any]
    claims: Mapping[str, Any]
    signing_input: bytes
    signature: bytes


def _split(jws: object) -> _Split | None:
    """Step 1: split and decode. None for anything that is not a 3-part JWS carrying JSON objects."""
    if not isinstance(jws, str) or len(jws) > MAX_TOKEN_BYTES:
        return None
    parts = jws.split(".")
    if len(parts) != 3:
        return None
    try:
        header = _decode_part(parts[0])
        claims = _decode_part(parts[1])
        signature = b64url_decode(parts[2])
    except (ValueError, binascii.Error, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(header, dict) or not isinstance(claims, dict):
        return None
    return _Split(header, claims, f"{parts[0]}.{parts[1]}".encode("ascii"), signature)


def _check_signature(split: _Split, keys: TrustedKeys, typ: str) -> str | None:
    """Steps 2-3. Returns a reason, or None when the signature verifies with a trusted key."""
    h = split.header
    if h.get("alg") != SIGNING_ALG or h.get("typ") != typ:
        return "alg_or_typ"
    kid = h.get("kid")
    if not isinstance(kid, str) or kid not in keys:
        return "unknown_kid"
    try:
        Ed25519PublicKey.from_public_bytes(keys[kid]).verify(split.signature, split.signing_input)
    except (InvalidSignature, ValueError, TypeError):
        return "bad_signature"
    return None


def _as_int(v: object) -> int | None:
    if isinstance(v, bool) or not isinstance(v, int | float):
        return None
    try:
        return int(v)
    except (OverflowError, ValueError):
        return None


def grace_seconds(claims: Mapping[str, Any]) -> int:
    lic = claims.get("lic")
    if not isinstance(lic, Mapping):
        return 0
    g = _as_int(lic.get("grace_days"))
    return max(0, g or 0) * DAY


def effective_now(now: int, last_seen: int | None) -> int:
    """Clock-rollback protection (spec §8.2 step 8): ``now = max(system, last_seen)``."""
    return max(int(now), int(last_seen or 0))


def verify_license_token(jws: object, ctx: VerifyContext) -> VerifyResult:
    """Spec §8.2 steps 1-10. Pure. Never raises."""
    try:
        return _verify(jws, ctx)
    except Exception:  # noqa: BLE001 - a verifier must never throw into callers
        return VerifyResult("invalid", "format")


def _verify(jws: object, ctx: VerifyContext) -> VerifyResult:
    split = _split(jws)
    if split is None:
        return VerifyResult("invalid", "format")
    reason = _check_signature(split, ctx.keys, TOKEN_TYP)
    if reason:
        return VerifyResult("invalid", reason, header=split.header)
    c = split.claims
    v = _as_int(c.get("v"))
    if v is None or v > TOKEN_CLAIM_V:
        return VerifyResult("invalid", "version", c, split.header)
    if c.get("aud") != ctx.product:
        return VerifyResult("invalid", "aud", c, split.header)
    if c.get("iss") != ctx.issuer:
        return VerifyResult("invalid", "iss", c, split.header)
    if c.get("use") != ctx.use:
        return VerifyResult("invalid", "use", c, split.header)
    bind = c.get("bind")
    if isinstance(bind, Mapping):
        iid = bind.get("iid")
        if iid and iid != instance_hash(ctx.instance_id):
            return VerifyResult("invalid", "bind", c, split.header)
        dom = bind.get("dom")
        if dom and dom != norm_domain(ctx.domain):
            return VerifyResult("invalid", "bind", c, split.header)
    elif bind is not None:
        return VerifyResult("invalid", "bind", c, split.header)
    if ctx.nonce is not None and c.get("nonce") != ctx.nonce:
        return VerifyResult("invalid", "nonce", c, split.header)
    now = effective_now(ctx.now, ctx.last_seen)
    nbf, exp = _as_int(c.get("nbf")), _as_int(c.get("exp"))
    if nbf is None or exp is None:
        return VerifyResult("invalid", "format", c, split.header)
    if now < nbf - 300:
        return VerifyResult("invalid", "not_yet_valid", c, split.header)
    grace = grace_seconds(c)
    lic_raw = c.get("lic")
    lic: Mapping[str, Any] = lic_raw if isinstance(lic_raw, Mapping) else {}
    expires = _as_int(lic.get("expires")) if lic.get("expires") is not None else None
    if expires is not None and now > expires + grace:
        return VerifyResult("expired", "license_period_over", c, split.header)
    if now <= exp:
        return VerifyResult("valid", None, c, split.header)
    if now <= exp + grace:
        return VerifyResult("grace", None, c, split.header)
    return VerifyResult("unlicensed", "token_expired", c, split.header)


def tool_included(claims: Mapping[str, Any], tool: str) -> bool:
    """``lic.features[tool] === true``: the license carries the tool at all (spec §8.4), whether or
    not its item has ended."""
    lic = claims.get("lic")
    if not isinstance(lic, Mapping):
        return False
    features = lic.get("features")
    return isinstance(features, Mapping) and features.get(tool) is True


def tool_active(claims: Mapping[str, Any], tool: str, now: int, last_seen: int | None = None) -> bool:
    """Spec §8.4: ``lic.features[tool] === true`` and, when ``lic.items[tool].expires`` is set,
    ``now <= expires + grace``. A missing feature means not entitled."""
    lic = claims.get("lic")
    if not isinstance(lic, Mapping):
        return False
    features = lic.get("features")
    if not isinstance(features, Mapping) or features.get(tool) is not True:
        return False
    items = lic.get("items")
    if isinstance(items, Mapping):
        item = items.get(tool)
        if isinstance(item, Mapping) and item.get("expires") is not None:
            expires = _as_int(item.get("expires"))
            if expires is None:
                return False
            if effective_now(now, last_seen) > expires + grace_seconds(claims):
                return False
    return True


def tool_expiry(claims: Mapping[str, Any], tool: str) -> int | None:
    lic = claims.get("lic")
    if not isinstance(lic, Mapping):
        return None
    items = lic.get("items")
    if isinstance(items, Mapping):
        item = items.get(tool)
        if isinstance(item, Mapping):
            return _as_int(item.get("expires"))
    return _as_int(lic.get("expires")) if lic.get("expires") is not None else None


@dataclass(frozen=True, slots=True)
class RevocationResult:
    ok: bool
    revoked: bool = False
    reason: str | None = None
    claims: Mapping[str, Any] = field(default_factory=dict)


def verify_revocation_list(
    jws: object, *, product: str, keys: TrustedKeys, now: int, sub: str | None, act: str | None
) -> RevocationResult:
    """Spec §10: verify like a token (``typ``, ``aud``, ``use``, ``exp``, signature); check ``sub``/``act``.

    A revocation list is never accepted as a license (different ``typ``/``use``), and vice versa.
    """
    try:
        split = _split(jws)
        if split is None:
            return RevocationResult(False, reason="format")
        reason = _check_signature(split, keys, REVOCATION_TYP)
        if reason:
            return RevocationResult(False, reason=reason)
        c = split.claims
        if c.get("aud") != product or c.get("use") != "revocations":
            return RevocationResult(False, reason="aud_or_use")
        exp = _as_int(c.get("exp"))
        if exp is None or now > exp:
            return RevocationResult(False, reason="expired")
        revoked_subs = {r.get("sub") for r in c.get("revoked", []) if isinstance(r, Mapping)}
        revoked_acts = {r.get("act") for r in c.get("revoked_activations", []) if isinstance(r, Mapping)}
        hit = (sub is not None and sub in revoked_subs) or (act is not None and act in revoked_acts)
        return RevocationResult(True, revoked=hit, claims=c)
    except Exception:  # noqa: BLE001
        return RevocationResult(False, reason="format")
