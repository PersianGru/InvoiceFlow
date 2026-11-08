"""Trusted public keys (spec §2 rule 1, §11): shipped keys plus keys fetched from the hub's JWKS.

A key is identified by ``kid`` and holds the raw 32-byte Ed25519 public key.
Accepted configuration formats for ``LICENSE_PUBLIC_KEYS``:

* a JWKS ``{"keys": [...]}``, a JSON list of JWKs, or a single JWK (``kty`` OKP, ``crv`` Ed25519);
* PEM blocks (SPKI), each preceded by a ``# kid: <kid>`` line as ``GET /keys/{project}?format=pem`` prints.
"""

from __future__ import annotations

import base64
import json
import re
from collections.abc import Iterable, Mapping
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

TrustedKeys = Mapping[str, bytes]

_PEM_RE = re.compile(r"-----BEGIN PUBLIC KEY-----.*?-----END PUBLIC KEY-----", re.S)
_KID_RE = re.compile(r"#\s*kid:\s*([A-Za-z0-9._-]+)")


class PublicKeyConfigError(ValueError):
    pass


def _b64url_decode(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def key_from_jwk(jwk: Mapping[str, Any]) -> tuple[str, bytes]:
    if jwk.get("kty") != "OKP" or jwk.get("crv") != "Ed25519":
        raise PublicKeyConfigError("only OKP/Ed25519 keys are accepted")
    kid = jwk.get("kid")
    x = jwk.get("x")
    if not isinstance(kid, str) or not kid or not isinstance(x, str):
        raise PublicKeyConfigError("JWK needs kid and x")
    raw = _b64url_decode(x)
    if len(raw) != 32:
        raise PublicKeyConfigError("Ed25519 public key must be 32 bytes")
    Ed25519PublicKey.from_public_bytes(raw)  # validates
    return kid, raw


def keys_from_jwks(jwks: Mapping[str, Any] | Iterable[Mapping[str, Any]]) -> dict[str, bytes]:
    entries: Iterable[Any]
    if isinstance(jwks, Mapping):
        entries = jwks["keys"] if "keys" in jwks else [jwks]
    else:
        entries = jwks
    out: dict[str, bytes] = {}
    for jwk in entries:
        if not isinstance(jwk, Mapping):
            continue
        if jwk.get("kty") == "OKP" and jwk.get("crv") == "Ed25519":
            kid, raw = key_from_jwk(jwk)
            out[kid] = raw
    return out


def keys_from_pem_text(text: str) -> dict[str, bytes]:
    out: dict[str, bytes] = {}
    pos = 0
    for m in _PEM_RE.finditer(text):
        kids = _KID_RE.findall(text[pos : m.start()])
        if not kids:
            raise PublicKeyConfigError("PEM public key needs a preceding '# kid: <kid>' line")
        pub = serialization.load_pem_public_key(m.group(0).encode())
        if not isinstance(pub, Ed25519PublicKey):
            raise PublicKeyConfigError("only Ed25519 public keys are accepted")
        out[kids[-1]] = pub.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        pos = m.end()
    return out


def keys_from_config(text: str) -> dict[str, bytes]:
    """Parse ``LICENSE_PUBLIC_KEYS``. Raises ``PublicKeyConfigError`` on anything unusable."""
    stripped = (text or "").strip()
    if not stripped:
        raise PublicKeyConfigError("LICENSE_PUBLIC_KEYS is empty")
    if stripped[0] in "{[":
        try:
            data = json.loads(stripped)
        except json.JSONDecodeError as e:
            raise PublicKeyConfigError(f"LICENSE_PUBLIC_KEYS is not valid JSON: {e}") from e
        keys = keys_from_jwks(data)
    else:
        keys = keys_from_pem_text(stripped)
    if not keys:
        raise PublicKeyConfigError("LICENSE_PUBLIC_KEYS holds no Ed25519 key")
    return keys


def jwk_from_raw(kid: str, raw: bytes) -> dict[str, str]:
    return {
        "kty": "OKP",
        "crv": "Ed25519",
        "kid": kid,
        "x": base64.urlsafe_b64encode(raw).rstrip(b"=").decode(),
        "use": "sig",
        "alg": "EdDSA",
    }
