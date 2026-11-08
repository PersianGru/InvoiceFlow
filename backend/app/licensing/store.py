"""Encrypted at-rest storage of a firm's license key and token.

Both live on the ``firms`` row, encrypted with the firm's data key (AES-256-GCM).
Nothing here logs; callers log decisions only.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Any

from app.models.firm import Firm
from app.security.crypto import DecryptError, MasterKey, TenantCipher


class FirmSecrets:
    """Read/write the firm's secrets. Construct once per firm per request."""

    def __init__(self, master: MasterKey, firm: Firm) -> None:
        self.firm = firm
        self._cipher = TenantCipher(master.unwrap(firm.data_key_wrapped, str(firm.id)), str(firm.id))

    # other per-firm secrets (webhook endpoint secrets) ----------------------------
    def seal(self, field: str, value: str) -> bytes:
        return self._cipher.encrypt(field, value)

    def open(self, field: str, blob: bytes) -> str | None:
        try:
            return self._cipher.decrypt(field, blob)
        except DecryptError:
            return None

    # license key -----------------------------------------------------------------------
    def key(self) -> str | None:
        if self.firm.license_key_enc is None:
            return None
        try:
            return self._cipher.decrypt("license_key", self.firm.license_key_enc)
        except DecryptError:
            return None

    def set_key(self, canonical_key: str, key_hint: str) -> None:
        self.firm.license_key_enc = self._cipher.encrypt("license_key", canonical_key)
        self.firm.key_hint = key_hint

    def clear_key(self) -> None:
        self.firm.license_key_enc = None
        self.firm.key_hint = None

    # token ------------------------------------------------------------------------------
    def token(self) -> str | None:
        if self.firm.license_token_enc is None:
            return None
        try:
            return self._cipher.decrypt("license_token", self.firm.license_token_enc)
        except DecryptError:
            return None

    def token_digest(self) -> str | None:
        if self.firm.license_token_enc is None:
            return None
        return hashlib.sha256(self.firm.license_token_enc).hexdigest()

    def set_token(self, jws: str, use: str, claims: Mapping[str, Any]) -> None:
        self.firm.license_token_enc = self._cipher.encrypt("license_token", jws)
        self.firm.license_use = use
        sub, act, kh = claims.get("sub"), claims.get("act"), claims.get("kh")
        self.firm.license_id = sub if isinstance(sub, str) else None
        self.firm.activation_id = act if isinstance(act, str) else None
        if isinstance(kh, str) and kh:
            self.firm.key_hint = kh[-5:]

    def clear_token(self) -> None:
        self.firm.license_token_enc = None
        self.firm.license_use = None
        self.firm.activation_id = None

    # generic (TOTP secrets etc.) ---------------------------------------------------------
    def encrypt(self, field: str, value: str) -> bytes:
        return self._cipher.encrypt(field, value)

    def decrypt(self, field: str, blob: bytes) -> str:
        return self._cipher.decrypt(field, blob)


def new_firm_data_key(master: MasterKey, firm_id: str) -> bytes:
    return master.wrap(master.new_data_key(), firm_id)
