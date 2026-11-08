"""Per-tenant encryption at rest.

* The platform ``MASTER_KEY`` (32 bytes, environment) wraps one random 32-byte data key per firm.
* The firm's data key encrypts its secrets (license key, license token, TOTP secrets) with
  AES-256-GCM; each ciphertext is ``nonce(12) || ciphertext || tag`` with the firm id and the
  field name as associated data, so a value cannot be moved between rows or columns.
"""

from __future__ import annotations

import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_NONCE_LEN = 12


class DecryptError(ValueError):
    pass


def _seal(key: bytes, plaintext: bytes, aad: bytes) -> bytes:
    nonce = os.urandom(_NONCE_LEN)
    return nonce + AESGCM(key).encrypt(nonce, plaintext, aad)


def _open(key: bytes, blob: bytes, aad: bytes) -> bytes:
    if len(blob) < _NONCE_LEN + 16:
        raise DecryptError("ciphertext too short")
    try:
        return AESGCM(key).decrypt(blob[:_NONCE_LEN], blob[_NONCE_LEN:], aad)
    except InvalidTag as e:
        raise DecryptError("authentication failed") from e


class MasterKey:
    def __init__(self, raw: bytes) -> None:
        if len(raw) != 32:
            raise ValueError("master key must be 32 bytes")
        self._raw = raw

    def new_data_key(self) -> bytes:
        return os.urandom(32)

    def wrap(self, data_key: bytes, firm_id: str) -> bytes:
        return _seal(self._raw, data_key, f"firm-data-key:{firm_id}".encode())

    def unwrap(self, wrapped: bytes, firm_id: str) -> bytes:
        return _open(self._raw, wrapped, f"firm-data-key:{firm_id}".encode())


class TenantCipher:
    def __init__(self, data_key: bytes, firm_id: str) -> None:
        if len(data_key) != 32:
            raise ValueError("data key must be 32 bytes")
        self._key = data_key
        self._firm = firm_id

    def encrypt(self, field: str, plaintext: str) -> bytes:
        return _seal(self._key, plaintext.encode("utf-8"), f"{self._firm}:{field}".encode())

    def decrypt(self, field: str, blob: bytes) -> str:
        return _open(self._key, blob, f"{self._firm}:{field}".encode()).decode("utf-8")
