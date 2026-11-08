"""21CEL license key format (spec §5).
Keys are secrets: nothing here logs, stores or transmits them.
"""

from __future__ import annotations

import hashlib
import re

CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
VALID_BODY_LENGTHS = (25, 30, 35, 40)  # 5 to 8 groups of 5 (spec §5)
_NOT_ALNUM = re.compile(r"[^0-9A-Z]")
_PREFIX_RE = re.compile(r"^[A-Z0-9]{2,6}$")


def normalize_key(raw: str, prefix: str) -> str:
    """Canonical form: uppercase, no separators, body with I/L→1 and O→0.

    The prefix part is only uppercased (spec §5).
    """
    flat = _NOT_ALNUM.sub("", str(raw).upper())
    head, body = flat[: len(prefix)], flat[len(prefix) :]
    body = body.replace("I", "1").replace("L", "1").replace("O", "0")
    return head + body


def check_character(prefix: str, body_without_check: str) -> str:
    digest = hashlib.sha256(f"{prefix}:{body_without_check}".encode()).digest()
    return CROCKFORD[digest[0] & 31]


def key_check_ok(raw: str, prefix: str) -> bool:
    """True when the key has a valid prefix, an accepted length and a matching check character.

    Only catches typos before calling the hub; it is not a security feature (spec §5).
    """
    if not _PREFIX_RE.match(prefix):
        return False
    flat = normalize_key(raw, prefix)
    if not flat.startswith(prefix):
        return False
    body = flat[len(prefix) :]
    if len(body) not in VALID_BODY_LENGTHS:
        return False
    if any(ch not in CROCKFORD for ch in body):
        return False
    return check_character(prefix, body[:-1]) == body[-1]


def key_hint(raw: str, prefix: str) -> str:
    """The last 5 characters (``kh``): the only part of a key that may be shown or stored in clear."""
    return normalize_key(raw, prefix)[-5:]


def format_key(raw: str, prefix: str) -> str:
    """``PREFIX-XXXXX-...`` for display in the product's own UI (never in logs or URLs)."""
    flat = normalize_key(raw, prefix)
    body = flat[len(prefix) :]
    groups = [body[i : i + 5] for i in range(0, len(body), 5)]
    return "-".join([prefix, *groups])


def mask_key(raw: str, prefix: str) -> str:
    """``IVF-*****-...-ABCDE``: everything hidden but the hint."""
    flat = normalize_key(raw, prefix)
    body = flat[len(prefix) :]
    if len(body) < 5:
        return prefix + "-*****"
    groups = ["*****"] * (len(body) // 5 - 1) + [body[-5:]]
    return "-".join([prefix, *groups])


__all__ = [
    "CROCKFORD",
    "VALID_BODY_LENGTHS",
    "check_character",
    "format_key",
    "key_check_ok",
    "key_hint",
    "mask_key",
    "normalize_key",
]
