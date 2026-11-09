"""age encryption for backups.

Each backup is encrypted to two X25519 recipients:

* one derived from the backup's random password (scrypt → 32-byte X25519 secret), so the password
  alone opens it in the app (``python -m app.backup.cli``) and the derived key works with ``age -i``;
* the owner's recovery key, whose private half only the owner keeps (``age -d -i recovery.txt``).
"""

from __future__ import annotations

import hashlib
import secrets
from pathlib import Path

import pyrage

_CHARSET = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"
_GEN = (0x3B6A57B2, 0x26508E6D, 0x1EA119FA, 0x3D4233DD, 0x2A1462B3)
SECRET_HRP = "age-secret-key-"  # noqa: S105 - the bech32 prefix of age keys
# the password is 32 random characters (~187 bits), so one fixed salt is enough and an archive
# opens with the password alone, also on another installation
PASSWORD_SALT = "v1"  # noqa: S105 - not a secret
PASSWORD_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"  # noqa: S105


def _polymod(values: list[int]) -> int:
    chk = 1
    for v in values:
        b = chk >> 25
        chk = (chk & 0x1FFFFFF) << 5 ^ v
        for i in range(5):
            chk ^= _GEN[i] if (b >> i) & 1 else 0
    return chk


def _convertbits(data: bytes, frombits: int, tobits: int) -> list[int]:
    acc, bits, ret, maxv = 0, 0, [], (1 << tobits) - 1
    for value in data:
        acc = (acc << frombits) | value
        bits += frombits
        while bits >= tobits:
            bits -= tobits
            ret.append((acc >> bits) & maxv)
    if bits:
        ret.append((acc << (tobits - bits)) & maxv)
    return ret


def bech32_encode(hrp: str, data: bytes) -> str:
    five = _convertbits(data, 8, 5)
    values = [ord(x) >> 5 for x in hrp] + [0] + [ord(x) & 31 for x in hrp] + five
    polymod = _polymod(values + [0] * 6) ^ 1
    checksum = [(polymod >> 5 * (5 - i)) & 31 for i in range(6)]
    return hrp + "1" + "".join(_CHARSET[d] for d in five + checksum)


def new_password() -> str:
    return "".join(secrets.choice(PASSWORD_ALPHABET) for _ in range(32))


def password_identity(password: str, salt: str) -> pyrage.x25519.Identity:
    secret = hashlib.scrypt(
        password.encode(),
        salt=f"invoiceflow-backup:{salt}".encode(),
        n=2**15,
        r=8,
        p=1,
        maxmem=2**26,
        dklen=32,
    )
    return pyrage.x25519.Identity.from_str(bech32_encode(SECRET_HRP, secret).upper())


def new_recovery_key() -> tuple[str, str]:
    """(private ``AGE-SECRET-KEY-1…`` shown once, public ``age1…`` kept)."""
    ident = pyrage.x25519.Identity.generate()
    return str(ident), str(ident.to_public())


def encrypt_file(src: Path, dst: Path, recipients: list[pyrage.x25519.Recipient]) -> None:
    with src.open("rb") as fin, dst.open("wb") as fout:
        pyrage.encrypt_io(fin, fout, recipients)


def decrypt_file(src: Path, dst: Path, identity: pyrage.x25519.Identity) -> None:
    with src.open("rb") as fin, dst.open("wb") as fout:
        pyrage.decrypt_io(fin, fout, [identity])


def recipient(public_key: str) -> pyrage.x25519.Recipient:
    return pyrage.x25519.Recipient.from_str(public_key)


def identity(private_key: str) -> pyrage.x25519.Identity:
    return pyrage.x25519.Identity.from_str(private_key.strip())
