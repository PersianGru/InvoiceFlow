"""Public-API credentials: ``ivfk_<key id>_<secret>`` keys and short-lived OAuth2
client-credentials access tokens ``ivfat_<payload>_<hmac>``.

Only digests are stored; the plain key is returned once at creation. Nothing here logs.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass

KEY_PREFIX = "ivfk_"
TOKEN_PREFIX = "ivfat_"  # noqa: S105 - a prefix, not a secret
KEY_ID_LEN = 8
SECRET_LEN = 32
_ALPHABET = "abcdefghijkmnpqrstuvwxyz23456789"  # lowercase crockford-ish, no ambiguous glyphs


def _rand(n: int) -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(n))


@dataclass(frozen=True, slots=True)
class NewKey:
    key_id: str
    secret_hash: str
    plain: str  # shown once


def generate_key() -> NewKey:
    key_id, secret = _rand(KEY_ID_LEN), _rand(SECRET_LEN)
    return NewKey(key_id, hash_secret(secret), f"{KEY_PREFIX}{key_id}_{secret}")


def hash_secret(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def split_key(plain: str) -> tuple[str, str] | None:
    """``ivfk_<id>_<secret>`` → (id, secret) or None when the shape is wrong."""
    if not plain.startswith(KEY_PREFIX):
        return None
    rest = plain[len(KEY_PREFIX) :]
    if "_" not in rest:
        return None
    key_id, secret = rest.split("_", 1)
    if len(key_id) != KEY_ID_LEN or not (16 <= len(secret) <= 64):
        return None
    return key_id, secret


def secret_matches(secret: str, secret_hash: str) -> bool:
    return hmac.compare_digest(hash_secret(secret), secret_hash)


def mask(plain_or_id: str) -> str:
    parts = split_key(plain_or_id)
    key_id = parts[0] if parts else plain_or_id
    return f"{KEY_PREFIX}{key_id}_…"


# ---- OAuth2 client-credentials access tokens (stateless, HMAC-signed with SECRET_KEY) ------------
def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def issue_access_token(
    secret_key: str, key_id: str, ttl_seconds: int, now: int | None = None
) -> tuple[str, int]:
    now = int(time.time()) if now is None else now
    payload = json.dumps({"k": key_id, "iat": now, "exp": now + ttl_seconds}, separators=(",", ":")).encode()
    sig = hmac.new(secret_key.encode(), payload, hashlib.sha256).digest()
    return f"{TOKEN_PREFIX}{_b64(payload)}_{_b64(sig)}", now + ttl_seconds


def verify_access_token(secret_key: str, token: str, now: int | None = None) -> str | None:
    """Returns the key id when the token is intact and not expired, else None."""
    if not token.startswith(TOKEN_PREFIX):
        return None
    try:
        payload_b64, sig_b64 = token[len(TOKEN_PREFIX) :].split("_", 1)
        payload = _unb64(payload_b64)
        sig = _unb64(sig_b64)
    except (ValueError, TypeError):
        return None
    want = hmac.new(secret_key.encode(), payload, hashlib.sha256).digest()
    if not hmac.compare_digest(want, sig):
        return None
    try:
        claims = json.loads(payload)
    except ValueError:
        return None
    now = int(time.time()) if now is None else now
    exp, key_id = claims.get("exp"), claims.get("k")
    if not isinstance(exp, int) or not isinstance(key_id, str) or now >= exp:
        return None
    return key_id
