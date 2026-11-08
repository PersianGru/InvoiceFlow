"""Webhook signatures: ``X-InvoiceFlow-Signature: t=<unix>,v1=<hex HMAC-SHA256 over "<t>.<body>">``,
the 21CEL hub's scheme (spec §14), so receivers can reuse one verifier. Secrets are ``ivfwh_…``."""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import time

SECRET_PREFIX = "ivfwh_"  # noqa: S105 - a prefix, not a secret
_SIG_RE = re.compile(r"^t=(\d+),v1=([0-9a-f]{64})$")
MAX_AGE = 300


def new_secret() -> str:
    return SECRET_PREFIX + secrets.token_urlsafe(24).replace("-", "a").replace("_", "b")


def sign(secret: str, body: bytes, t: int | None = None) -> str:
    t = int(time.time()) if t is None else t
    mac = hmac.new(secret.encode(), f"{t}.".encode() + body, hashlib.sha256).hexdigest()
    return f"t={t},v1={mac}"


def verify(
    secret: str, header: str | None, body: bytes, now: int | None = None, max_age: int = MAX_AGE
) -> bool:
    if not secret or not header:
        return False
    m = _SIG_RE.match(header.strip())
    if not m:
        return False
    t = int(m.group(1))
    now = int(time.time()) if now is None else now
    if abs(now - t) > max_age:
        return False
    want = hmac.new(secret.encode(), f"{t}.".encode() + body, hashlib.sha256).digest()
    try:
        got = bytes.fromhex(m.group(2))
    except ValueError:
        return False
    return hmac.compare_digest(want, got)
