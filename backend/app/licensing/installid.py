"""Sealed installation ids (spec §7.1, DEVICES.md).

The installation key ships inside the product (it is not a hub secret) but is read from the
environment (``LICENSE_INSTALLATION_KEY``) and never logged.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass

from app.licensing.constants import DAY

INSTALL_EPOCH = 1577836800  # 2020-01-01T00:00:00Z
_KEY_RE = re.compile(r"^ik1\.([1-9])\.([A-Za-z0-9_-]{43})$")
_ID_RE = re.compile(r"^[1-9]\d{37}$")
_HALF = 10**18

PLATFORM_CODES = {
    "other": "00",
    "windows": "01",
    "macos": "02",
    "linux": "03",
    "android": "04",
    "ios": "05",
    "wordpress": "06",
    "browser": "07",
    "server": "08",
    "web": "09",
}


class InstallationKeyError(ValueError):
    """The installation key is not ``ik1.<g>.<43 base64url chars>``."""


def _hmac(key: bytes, data: str | bytes) -> bytes:
    return hmac.new(key, data.encode() if isinstance(data, str) else data, hashlib.sha256).digest()


def digits_of(h: bytes, n: int) -> str:
    """n decimal digits from an HMAC: 4 digits per 32-bit big-endian word (word mod 10000)."""
    out = ""
    i = 0
    while len(out) < n:
        word = int.from_bytes(h[i : i + 4], "big")
        out += f"{word % 10000:04d}"
        i += 4
    return out[:n]


def luhn_digit(digits: str) -> str:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2 == 0:
            d = d * 2 - 9 if d * 2 > 9 else d * 2
        total += d
    return str((10 - (total % 10)) % 10)


@dataclass(frozen=True, slots=True)
class InstallKeyParts:
    gen: int
    enc: bytes
    fp: bytes
    chk: bytes


def install_key_parts(install_key: str) -> InstallKeyParts:
    m = _KEY_RE.match(install_key or "")
    if not m:
        raise InstallationKeyError("installation key is not valid")
    secret = base64.urlsafe_b64decode(m.group(2) + "=")
    return InstallKeyParts(
        gen=int(m.group(1)),
        enc=_hmac(secret, "21cel/iid/enc/v1"),
        fp=_hmac(secret, "21cel/iid/fp/v1"),
        chk=_hmac(secret, "21cel/iid/chk/v1"),
    )


def fingerprint(install_key: str, trait: str) -> str:
    """8 digits from a stable machine trait. The trait never leaves the machine."""
    return digits_of(_hmac(install_key_parts(install_key).fp, trait), 8)


def random_fingerprint() -> str:
    """When no machine trait exists: 8 CSPRNG digits, stored with the id (spec §7.1)."""
    return "".join(str(secrets.randbelow(10)) for _ in range(8))


def _round_f(k: InstallKeyParts, i: int, half: str) -> int:
    return int(digits_of(_hmac(k.enc, f"{k.gen}:{i}:{half}"), 18))


def make_install_id(
    install_key: str, platform: str, created: int, fp: str, random_digits: str | None = None
) -> str:
    """Make the 38-digit id once at install (spec §7.1 steps 1-4)."""
    if not re.fullmatch(r"\d{2}", platform):
        raise ValueError("platform must be two digits")
    if not re.fullmatch(r"\d{8}", fp):
        raise ValueError("fingerprint must be eight digits")
    if random_digits is None:
        random_digits = "".join(str(secrets.randbelow(10)) for _ in range(14))
    if not re.fullmatch(r"\d{14}", random_digits):
        raise ValueError("random part must be fourteen digits")
    k = install_key_parts(install_key)
    days = (created - INSTALL_EPOCH) // DAY
    head = f"1{platform}{days:05d}{fp}{random_digits}"
    plain = head + digits_of(_hmac(k.chk, head), 6)
    left, right = int(plain[:18]), int(plain[18:36])
    for i in range(10):
        f = _round_f(k, i, f"{right:018d}")
        left, right = right, (left + f) % _HALF
    body = f"{k.gen}{left:018d}{right:018d}"
    return body + luhn_digit(body)


@dataclass(frozen=True, slots=True)
class OpenedInstallId:
    ok: bool
    reason: str | None = None
    platform: str | None = None
    created: int | None = None
    fp: str | None = None


def open_install_id(raw_id: str, install_keys: list[str], now: int) -> OpenedInstallId:
    """Open an id with the known installation keys.

    reason: ``format`` | ``check_digit`` | ``unknown_key`` | ``not_genuine`` | ``date``.
    """
    s = re.sub(r"[\s.\-]", "", str(raw_id))
    if not _ID_RE.match(s):
        return OpenedInstallId(False, "format")
    if luhn_digit(s[:37]) != s[37]:
        return OpenedInstallId(False, "check_digit")
    key = next((x for x in install_keys if x.startswith(f"ik1.{s[0]}.")), None)
    if key is None:
        return OpenedInstallId(False, "unknown_key")
    k = install_key_parts(key)
    left, right = s[1:19], s[19:37]
    for i in range(9, -1, -1):
        f = _round_f(k, i, left)
        left, right = f"{((int(right) - f) % _HALF + _HALF) % _HALF:018d}", left
    plain = left + right
    if plain[0] != "1" or plain[30:] != digits_of(_hmac(k.chk, plain[:30]), 6):
        return OpenedInstallId(False, "not_genuine")
    created = INSTALL_EPOCH + int(plain[3:8]) * DAY
    if created > now + 2 * DAY:
        return OpenedInstallId(False, "date")
    return OpenedInstallId(True, None, plain[1:3], created, plain[8:16])


def format_grouped(install_id: str) -> str:
    """``25480 66987 …`` for humans (spec §7.1). The id itself is not a secret."""
    return " ".join(install_id[i : i + 5] for i in range(0, len(install_id), 5))


def is_sealed_id_format(raw_id: str) -> bool:
    return bool(_ID_RE.match(re.sub(r"[\s.\-]", "", str(raw_id))))
