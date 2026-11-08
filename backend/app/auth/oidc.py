"""OpenID Connect (authorization code + PKCE) for firm SSO: discovery, JWKS, and an
ID-token verifier for RS256 and ES256 built on ``cryptography`` only (no JOSE library that could be
talked into ``alg: none``). Every claim the checklist names is checked: signature, ``iss``, ``aud``,
``exp``, ``iat``/``nbf`` skew, ``nonce``, ``azp`` when several audiences."""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
from dataclasses import dataclass
from typing import Any

import httpx
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature

SKEW = 300


class OidcError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def b64url_decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def pkce_pair() -> tuple[str, str]:
    verifier = b64url(secrets.token_bytes(48))
    challenge = b64url(hashlib.sha256(verifier.encode()).digest())
    return verifier, challenge


@dataclass(frozen=True, slots=True)
class Discovery:
    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    jwks_uri: str


async def discover(http: httpx.AsyncClient, issuer: str) -> Discovery:
    if not issuer.startswith("https://"):
        raise OidcError("issuer_not_https", "The issuer must be an https:// URL.")
    url = issuer.rstrip("/") + "/.well-known/openid-configuration"
    r = await http.get(url, timeout=10)
    if r.status_code != 200:
        raise OidcError("discovery_failed", "The identity provider's discovery document is not reachable.")
    d = r.json()
    if str(d.get("issuer", "")).rstrip("/") != issuer.rstrip("/"):
        raise OidcError("issuer_mismatch", "The discovery document names a different issuer.")
    for key in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
        if not str(d.get(key, "")).startswith("https://"):
            raise OidcError("discovery_invalid", f"The discovery document has no https {key}.")
    return Discovery(issuer.rstrip("/"), d["authorization_endpoint"], d["token_endpoint"], d["jwks_uri"])


async def fetch_jwks(http: httpx.AsyncClient, jwks_uri: str) -> list[dict[str, Any]]:
    r = await http.get(jwks_uri, timeout=10)
    if r.status_code != 200:
        raise OidcError("jwks_failed", "The identity provider's keys are not reachable.")
    keys = r.json().get("keys")
    if not isinstance(keys, list):
        raise OidcError("jwks_invalid", "The JWKS document is malformed.")
    return [k for k in keys if isinstance(k, dict)]


def _public_key(jwk: dict[str, Any]) -> rsa.RSAPublicKey | ec.EllipticCurvePublicKey:
    kty = jwk.get("kty")
    if kty == "RSA":
        n = int.from_bytes(b64url_decode(jwk["n"]), "big")
        e = int.from_bytes(b64url_decode(jwk["e"]), "big")
        return rsa.RSAPublicNumbers(e, n).public_key()
    if kty == "EC" and jwk.get("crv") == "P-256":
        x = int.from_bytes(b64url_decode(jwk["x"]), "big")
        y = int.from_bytes(b64url_decode(jwk["y"]), "big")
        return ec.EllipticCurvePublicNumbers(x, y, ec.SECP256R1()).public_key()
    raise OidcError("unsupported_key", "Only RSA and P-256 keys are supported.")


def verify_id_token(
    token: str,
    jwks: list[dict[str, Any]],
    *,
    issuer: str,
    client_id: str,
    nonce: str,
    now: int | None = None,
) -> dict[str, Any]:
    now = int(time.time()) if now is None else now
    parts = token.split(".")
    if len(parts) != 3 or len(token) > 16_384:
        raise OidcError("token_format", "The ID token is malformed.")
    try:
        header = json.loads(b64url_decode(parts[0]))
        claims = json.loads(b64url_decode(parts[1]))
        signature = b64url_decode(parts[2])
    except (ValueError, UnicodeDecodeError) as e:
        raise OidcError("token_format", "The ID token is malformed.") from e
    alg = header.get("alg")
    if alg not in ("RS256", "ES256"):
        raise OidcError("alg", "Unsupported or unsafe ID token algorithm.")
    kid = header.get("kid")
    candidates = [k for k in jwks if (kid is None or k.get("kid") == kid) and k.get("use", "sig") == "sig"]
    if not candidates:
        raise OidcError("unknown_kid", "The ID token was signed with an unknown key.")
    signed = f"{parts[0]}.{parts[1]}".encode()
    ok = False
    for jwk in candidates:
        try:
            key = _public_key(jwk)
            if alg == "RS256" and isinstance(key, rsa.RSAPublicKey):
                key.verify(signature, signed, padding.PKCS1v15(), hashes.SHA256())
                ok = True
            elif alg == "ES256" and isinstance(key, ec.EllipticCurvePublicKey) and len(signature) == 64:
                der = encode_dss_signature(
                    int.from_bytes(signature[:32], "big"), int.from_bytes(signature[32:], "big")
                )
                key.verify(der, signed, ec.ECDSA(hashes.SHA256()))
                ok = True
            if ok:
                break
        except (InvalidSignature, ValueError, KeyError, OidcError):
            continue
    if not ok:
        raise OidcError("bad_signature", "The ID token signature is invalid.")
    if str(claims.get("iss", "")).rstrip("/") != issuer.rstrip("/"):
        raise OidcError("iss", "The ID token was issued by someone else.")
    aud = claims.get("aud")
    auds = aud if isinstance(aud, list) else [aud]
    if client_id not in auds:
        raise OidcError("aud", "The ID token is not for this application.")
    if len(auds) > 1 and claims.get("azp") != client_id:
        raise OidcError("azp", "The ID token's authorized party is wrong.")
    exp = claims.get("exp")
    if not isinstance(exp, int | float) or now >= exp + SKEW:
        raise OidcError("exp", "The ID token has expired.")
    iat = claims.get("iat")
    if isinstance(iat, int | float) and iat > now + SKEW:
        raise OidcError("iat", "The ID token is from the future.")
    nbf = claims.get("nbf")
    if isinstance(nbf, int | float) and now < nbf - SKEW:
        raise OidcError("nbf", "The ID token is not yet valid.")
    if not nonce or claims.get("nonce") != nonce:
        raise OidcError("nonce", "The ID token nonce does not match this sign-in.")
    if not isinstance(claims.get("sub"), str) or not claims["sub"]:
        raise OidcError("sub", "The ID token has no subject.")
    return dict(claims)
