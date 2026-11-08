"""Poland — KSeF 2.0 (Krajowy System e-Faktur).

Per client company: the NIP and a KSeF token (issued in the KSeF web application for the taxpayer).

* Authentication: ``POST /api/v2/auth/challenge`` → ``{challenge, timestamp}``;
  ``POST /api/v2/auth/ksef-token`` with ``"<token>|<timestamp ms>"`` RSA-OAEP(SHA-256) encrypted with
  the published token-encryption certificate (``GET /api/v2/security/public-key-certificates``);
  ``GET /api/v2/auth/{reference}`` until status 200; ``POST /api/v2/auth/token/redeem`` → access token.
* Receive (buyer side): ``POST /api/v2/invoices/query/metadata`` with ``subjectType = Subject2`` and
  the cursor as the ``from`` date, then ``GET /api/v2/invoices/ksef/{ksefNumber}`` for each FA(3) XML.
* Send: ``POST /api/v2/sessions/online`` with an AES-256-CBC session key encrypted for KSeF, then
  ``POST /api/v2/sessions/online/{ref}/invoices`` with the encrypted FA(3); only outgoing documents
  whose original *is* FA(3) XML are sent (InvoiceFlow does not author FA(3)).

Built from the public KSeF 2.0 API description (2026); verify against the test environment
(``ksef-test.mf.gov.pl``) before production use — see ``docs/integrations/ksef.md``.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import os
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx
from cryptography import x509
from cryptography.hazmat.primitives import hashes, padding
from cryptography.hazmat.primitives.asymmetric import padding as apadding
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from app.connectors.base import (
    ConfigField,
    PushItem,
    PushOutcome,
    PushResult,
    ReceivedItem,
    ReceiveResult,
)

log = logging.getLogger("invoiceflow.connectors.ksef")

ENVIRONMENTS = {
    "test": "https://api-test.ksef.mf.gov.pl",
    "demo": "https://api-demo.ksef.mf.gov.pl",
    "prod": "https://api.ksef.mf.gov.pl",
}
FA3_FORM = {"systemCode": "FA (3)", "schemaVersion": "1-0E", "value": "FA"}
MAX_PER_PULL = 100


class KsefError(Exception):
    pass


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def encrypt_for_ksef(public_key: RSAPublicKey, plaintext: bytes) -> bytes:
    return public_key.encrypt(
        plaintext,
        apadding.OAEP(mgf=apadding.MGF1(algorithm=hashes.SHA256()), algorithm=hashes.SHA256(), label=None),
    )


def aes_cbc_encrypt(key: bytes, iv: bytes, data: bytes) -> bytes:
    padder = padding.PKCS7(128).padder()
    padded = padder.update(data) + padder.finalize()
    enc = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
    return enc.update(padded) + enc.finalize()


def _timestamp_ms(value: str) -> int:
    ts = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return int(ts.timestamp() * 1000)


class KsefConnector:
    key = "ksef"
    name = "Poland — KSeF"
    description = (
        "Receive your clients' purchase invoices from the national e-invoicing system (KSeF) and "
        "send their FA(3) sales invoices. One KSeF token per client (NIP)."
    )
    docs_url = "https://ksef.podatki.gov.pl/"
    receive_scope = "client"
    fields: tuple[ConfigField, ...] = (
        ConfigField("environment", "Environment: test | demo | prod", required=False, default="test"),
    )
    client_fields: tuple[ConfigField, ...] = (
        ConfigField("nip", "NIP (10 digits)", required=True),
        ConfigField("ksef_token", "KSeF token", secret=True, required=True),
    )

    def __init__(self, http: httpx.AsyncClient | None = None) -> None:
        self.http = http or httpx.AsyncClient(timeout=30, follow_redirects=False)

    # -- plumbing ------------------------------------------------------------------------------
    @staticmethod
    def base_url(config: dict[str, Any]) -> str:
        env = str(config.get("environment") or "test").lower()
        if env not in ENVIRONMENTS:
            raise KsefError("unknown environment")
        return ENVIRONMENTS[env]

    async def _json(self, method: str, url: str, *, token: str | None = None, **kw: Any) -> Any:
        headers = dict(kw.pop("headers", {}) or {})
        headers["accept"] = "application/json"
        if token:
            headers["authorization"] = f"Bearer {token}"
        r = await self.http.request(method, url, headers=headers, **kw)
        if r.status_code >= 400:
            raise KsefError(f"KSeF answered {r.status_code} on {url.rsplit('/', 1)[-1]}")
        if not r.content:
            return {}
        try:
            return r.json()
        except ValueError as e:
            raise KsefError("KSeF answered with a non-JSON body") from e

    async def _encryption_key(self, base: str) -> RSAPublicKey:
        certs = await self._json("GET", f"{base}/api/v2/security/public-key-certificates")
        for entry in certs if isinstance(certs, list) else []:
            if not isinstance(entry, dict):
                continue
            usages = [str(u) for u in (entry.get("usage") or [])]
            if usages and "KsefTokenEncryption" not in usages and "SymmetricKeyEncryption" not in usages:
                continue
            raw = entry.get("certificate")
            if isinstance(raw, str):
                cert = x509.load_der_x509_certificate(base64.b64decode(raw))
                key = cert.public_key()
                if isinstance(key, RSAPublicKey):
                    return key
        raise KsefError("no KSeF encryption certificate published")

    async def authenticate(self, config: dict[str, Any]) -> str:
        """Challenge → encrypted KSeF token → access token."""
        base = self.base_url(config)
        nip, token = str(config.get("nip") or "").strip(), str(config.get("ksef_token") or "")
        if not (nip.isdigit() and len(nip) == 10):
            raise KsefError("NIP must be 10 digits")
        if not token:
            raise KsefError("KSeF token missing")
        context = {"type": "Nip", "value": nip}
        ch = await self._json("POST", f"{base}/api/v2/auth/challenge", json={"contextIdentifier": context})
        challenge, ts = str(ch.get("challenge") or ""), str(ch.get("timestamp") or "")
        if not challenge or not ts:
            raise KsefError("challenge incomplete")
        key = await self._encryption_key(base)
        encrypted = encrypt_for_ksef(key, f"{token}|{_timestamp_ms(ts)}".encode())
        init = await self._json(
            "POST",
            f"{base}/api/v2/auth/ksef-token",
            json={"challenge": challenge, "contextIdentifier": context, "encryptedToken": _b64(encrypted)},
        )
        ref = str(init.get("referenceNumber") or "")
        temp = str(
            ((init.get("authenticationToken") or {}) if isinstance(init, dict) else {}).get("token") or ""
        )
        if not ref or not temp:
            raise KsefError("authentication not started")
        status = await self._json("GET", f"{base}/api/v2/auth/{ref}", token=temp)
        code = int(((status.get("status") or {}) if isinstance(status, dict) else {}).get("code") or 0)
        if code != 200:
            raise KsefError(f"authentication status {code or 'pending'}")
        redeemed = await self._json("POST", f"{base}/api/v2/auth/token/redeem", token=temp)
        access = str(
            ((redeemed.get("accessToken") or {}) if isinstance(redeemed, dict) else {}).get("token") or ""
        )
        if not access:
            raise KsefError("no access token")
        return access

    # -- protocol ------------------------------------------------------------------------------
    async def test(self, config: dict[str, Any]) -> tuple[bool, str]:
        try:
            self.base_url(config)
        except KsefError as e:
            return False, str(e)
        if "nip" not in config:
            return True, "Environment set; add a NIP and a KSeF token per client."
        try:
            await self.authenticate(config)
        except (KsefError, httpx.HTTPError) as e:
            return False, f"Authentication failed: {e if isinstance(e, KsefError) else type(e).__name__}."
        return True, "Authenticated with KSeF."

    async def receive(self, config: dict[str, Any], cursor: str | None) -> ReceiveResult:
        base = self.base_url(config)
        access = await self.authenticate(config)
        start = cursor or (date.today() - timedelta(days=30)).isoformat()
        now = datetime.now(UTC)
        query = {
            "subjectType": "Subject2",
            "dateRange": {"dateType": "Invoicing", "from": f"{start}T00:00:00+00:00", "to": now.isoformat()},
        }
        meta = await self._json(
            "POST",
            f"{base}/api/v2/invoices/query/metadata",
            token=access,
            params={"pageOffset": 0, "pageSize": MAX_PER_PULL},
            json=query,
        )
        out = ReceiveResult(cursor=now.date().isoformat())
        for inv in (meta.get("invoices") or []) if isinstance(meta, dict) else []:
            if not isinstance(inv, dict) or not inv.get("ksefNumber"):
                continue
            ksef_number = str(inv["ksefNumber"])
            r = await self.http.get(
                f"{base}/api/v2/invoices/ksef/{ksef_number}",
                headers={"authorization": f"Bearer {access}", "accept": "application/xml"},
                timeout=60,
            )
            if r.status_code != 200 or not r.content:
                log.warning("ksef: invoice %s not fetched (%s)", ksef_number, r.status_code)
                continue
            seller_raw = inv.get("seller")
            seller: dict[str, Any] = seller_raw if isinstance(seller_raw, dict) else {}
            out.items.append(
                ReceivedItem(
                    external_id=ksef_number[:200],
                    filename=f"{ksef_number}.xml",
                    mime="application/xml",
                    data=r.content,
                    receiver_id=str(config.get("nip") or ""),
                    sender_identity=f"nip:{seller.get('nip', '')}"[:320],
                )
            )
        return out

    async def acknowledge(self, config: dict[str, Any], external_ids: list[str]) -> None:
        return None  # cursor-based: nothing to acknowledge at KSeF

    async def push(self, items: list[PushItem], config: dict[str, Any]) -> PushResult:
        result = PushResult()
        sendable = [
            i
            for i in items
            if i.original is not None
            and i.document.structured_format == "fa3"
            and i.invoice.direction is not None
            and i.invoice.direction.value == "sales"
        ]
        for i in items:
            if i not in sendable:
                result.outcomes.append(
                    PushOutcome(
                        str(i.document.id), False, None, "only sales FA(3) originals are sent to KSeF"
                    )
                )
        if not sendable:
            return result
        try:
            base = self.base_url(config)
            access = await self.authenticate(config)
            key_pub = await self._encryption_key(base)
            aes_key, iv = os.urandom(32), os.urandom(16)
            session = await self._json(
                "POST",
                f"{base}/api/v2/sessions/online",
                token=access,
                json={
                    "formCode": FA3_FORM,
                    "encryption": {
                        "encryptedSymmetricKey": _b64(encrypt_for_ksef(key_pub, aes_key)),
                        "initializationVector": _b64(iv),
                    },
                },
            )
            ref = str(session.get("referenceNumber") or "")
            if not ref:
                raise KsefError("no session reference")
            for i in sendable:
                assert i.original is not None
                encrypted = aes_cbc_encrypt(aes_key, iv, i.original)
                sent = await self._json(
                    "POST",
                    f"{base}/api/v2/sessions/online/{ref}/invoices",
                    token=access,
                    json={
                        "invoiceHash": _b64(hashlib.sha256(i.original).digest()),
                        "invoiceSize": len(i.original),
                        "encryptedInvoiceHash": _b64(hashlib.sha256(encrypted).digest()),
                        "encryptedInvoiceSize": len(encrypted),
                        "encryptedInvoiceContent": _b64(encrypted),
                    },
                )
                result.outcomes.append(
                    PushOutcome(
                        str(i.document.id),
                        True,
                        str(sent.get("referenceNumber") or ref)[:200],
                        "sent to KSeF",
                    )
                )
            await self._json("POST", f"{base}/api/v2/sessions/online/{ref}/close", token=access)
        except (KsefError, httpx.HTTPError) as e:
            msg = f"KSeF: {e if isinstance(e, KsefError) else type(e).__name__}"
            done = {o.document_id for o in result.outcomes}
            for i in sendable:
                if str(i.document.id) not in done:
                    result.outcomes.append(PushOutcome(str(i.document.id), False, None, msg))
        return result
