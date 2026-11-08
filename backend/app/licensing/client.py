"""HTTPS client for the hub's product API (spec §6, §12).

* HTTPS with certificate validation only; a plain ``http://`` origin is accepted solely through the
  debug override outside production (``Settings.validate``), for the mock hub.
* 10 s timeout, a fresh random nonce per request, ``instance.id`` + ``instance.fp`` on every call.
* Never logs the key, the token or the body; logs decisions only.
"""

from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.config import ConfigurationError, Settings
from app.licensing.constants import CLIENT_TIMEOUT_SECONDS, INSTANCE_PLATFORM

log = logging.getLogger("invoiceflow.licensing.client")


class HubUnreachableError(Exception):
    """Network-level failure (timeout, connection, TLS). Never removes a cached license."""

    def __init__(self, kind: str, detail: str = "") -> None:
        super().__init__(kind)
        self.kind = kind
        self.detail = detail


@dataclass(frozen=True, slots=True)
class HubResponse:
    status: int
    ok: bool
    error: str | None = None
    message: str | None = None
    data: dict[str, Any] = field(default_factory=dict)

    @property
    def token(self) -> str | None:
        t = self.data.get("token")
        return t if isinstance(t, str) else None

    @property
    def is_transient(self) -> bool:
        """429, 5xx and project_paused: keep the cached token, use grace, back off (spec §6.4)."""
        return self.status == 429 or self.status >= 500 or self.error == "project_paused"


@dataclass(frozen=True, slots=True)
class InstanceInfo:
    id: str
    fp: str
    name: str | None = None

    def payload(self, app_version: str) -> dict[str, Any]:
        out: dict[str, Any] = {
            "id": self.id,
            "fp": self.fp,
            "platform": INSTANCE_PLATFORM,
            "app_version": app_version,
        }
        if self.name:
            out["name"] = self.name[:120]
        return out


def new_nonce() -> str:
    return secrets.token_urlsafe(12)


class HubClient:
    def __init__(self, settings: Settings, http: httpx.AsyncClient | None = None) -> None:
        origin = settings.hub_origin
        if not origin.startswith("https://"):
            if settings.is_production or not settings.license_hub_url_debug:
                raise ConfigurationError("the license hub must be reached over https://")
            log.warning("license hub debug override in use (plain HTTP, non-production only)")
        self.origin = origin
        self.project = settings.product_id
        self.app_version = settings.app_version
        self._own_http = http is None
        self._http = http or httpx.AsyncClient(
            base_url=origin,
            timeout=httpx.Timeout(CLIENT_TIMEOUT_SECONDS),
            verify=True,
            headers={"user-agent": f"InvoiceFlow/{settings.app_version}", "accept": "application/json"},
            follow_redirects=False,
        )

    async def aclose(self) -> None:
        if self._own_http:
            await self._http.aclose()

    def _body(self, key: str, instance: InstanceInfo, nonce: str) -> dict[str, Any]:
        return {
            "project": self.project,
            "key": key,
            "instance": instance.payload(self.app_version),
            "nonce": nonce,
        }

    async def _post(self, path: str, body: dict[str, Any]) -> HubResponse:
        try:
            r = await self._http.post(path, json=body)
        except httpx.TimeoutException as e:
            raise HubUnreachableError("timeout", type(e).__name__) from e
        except httpx.TransportError as e:  # includes TLS and connection errors
            raise HubUnreachableError("connect", type(e).__name__) from e
        return _parse(r)

    async def _get(self, path: str, params: dict[str, str] | None = None) -> HubResponse:
        try:
            r = await self._http.get(path, params=params)
        except httpx.TimeoutException as e:
            raise HubUnreachableError("timeout", type(e).__name__) from e
        except httpx.TransportError as e:
            raise HubUnreachableError("connect", type(e).__name__) from e
        return _parse(r)

    async def activate(self, key: str, instance: InstanceInfo, nonce: str) -> HubResponse:
        return await self._post("/v1/client/activate", self._body(key, instance, nonce))

    async def validate(self, key: str, instance: InstanceInfo, nonce: str) -> HubResponse:
        return await self._post("/v1/client/validate", self._body(key, instance, nonce))

    async def deactivate(self, key: str, instance: InstanceInfo) -> HubResponse:
        return await self._post("/v1/client/deactivate", self._body(key, instance, new_nonce()))

    async def offline_activate(self, request_code: str) -> HubResponse:
        return await self._post("/v1/client/offline/activate", {"request": request_code})

    async def get_keys(self) -> dict[str, Any]:
        r = await self._get(f"/v1/client/keys/{self.project}")
        if not r.ok:
            raise HubUnreachableError("protocol", f"keys endpoint answered {r.status}")
        return r.data

    async def get_revocations(self) -> str:
        r = await self._get(f"/v1/client/revocations/{self.project}")
        tok = r.token
        if not r.ok or tok is None:
            raise HubUnreachableError("protocol", f"revocations endpoint answered {r.status}")
        return tok


def _parse(r: httpx.Response) -> HubResponse:
    data: Any
    try:
        data = r.json()
    except ValueError:
        data = {}
    if not isinstance(data, dict):
        data = {}
    ok = bool(data.get("ok", r.status_code < 300)) and r.status_code < 300
    error = data.get("error")
    message = data.get("message")
    resp = HubResponse(
        status=r.status_code,
        ok=ok,
        error=str(error) if isinstance(error, str) else (None if ok else "http_error"),
        message=str(message)[:500] if isinstance(message, str) else None,
        data=data,
    )
    log.info("hub answered status=%s ok=%s error=%s", resp.status, resp.ok, resp.error)
    return resp
