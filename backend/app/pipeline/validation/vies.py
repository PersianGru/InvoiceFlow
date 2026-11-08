"""VIES (EU VAT number validation): REST client with a database cache and downtime tolerance."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.pipeline import VatCheck

log = logging.getLogger("invoiceflow.pipeline.vies")
VIES_URL = "https://ec.europa.eu/taxation_customs/vies/rest-api/check-vat-number"
CACHE_TTL = timedelta(days=30)


@dataclass(frozen=True, slots=True)
class ViesResult:
    valid: bool | None  # None = could not verify (downtime)
    name: str | None = None
    address: str | None = None
    source: str = "vies"


class ViesClient(Protocol):
    async def check(self, vat_id: str) -> ViesResult: ...


class HttpViesClient:
    def __init__(self, http: httpx.AsyncClient | None = None, url: str = VIES_URL) -> None:
        self._http = http or httpx.AsyncClient(timeout=httpx.Timeout(10.0))
        self.url = url

    async def check(self, vat_id: str) -> ViesResult:
        cc, number = vat_id[:2].upper(), vat_id[2:]
        try:
            r = await self._http.post(self.url, json={"countryCode": cc, "vatNumber": number})
            if r.status_code >= 500:
                return ViesResult(None, source="vies:unavailable")
            body = r.json()
        except (httpx.HTTPError, ValueError):
            return ViesResult(None, source="vies:unavailable")
        if body.get("userError") in (
            "MS_UNAVAILABLE",
            "SERVICE_UNAVAILABLE",
            "TIMEOUT",
            "MS_MAX_CONCURRENT_REQ",
            "GLOBAL_MAX_CONCURRENT_REQ",
        ):
            return ViesResult(None, source="vies:unavailable")
        valid = body.get("valid")
        if not isinstance(valid, bool):
            return ViesResult(None, source="vies:unavailable")
        name = body.get("name")
        addr = body.get("address")
        return ViesResult(
            valid,
            name if isinstance(name, str) and name != "---" else None,
            addr if isinstance(addr, str) and addr != "---" else None,
        )


class CachedVies:
    """Caches answers in ``vat_checks`` for 30 days; an unavailable VIES falls back to a stale answer."""

    def __init__(self, inner: ViesClient, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self.inner = inner
        self.sessionmaker = sessionmaker

    async def check(self, vat_id: str) -> ViesResult:
        vat_id = vat_id.upper()
        async with self.sessionmaker() as s:
            async with s.begin():
                row = await s.get(VatCheck, vat_id)
                if (
                    row is not None
                    and row.checked_at >= datetime.now(UTC) - CACHE_TTL
                    and row.valid is not None
                ):
                    return ViesResult(row.valid, row.name, row.address, "cache")
        res = await self.inner.check(vat_id)
        if res.valid is None:
            return (
                ViesResult(row.valid, row.name, row.address, "cache:stale")
                if row is not None and row.valid is not None
                else res
            )
        async with self.sessionmaker() as s:
            async with s.begin():
                row = await s.get(VatCheck, vat_id)
                if row is None:
                    s.add(
                        VatCheck(
                            vat_id=vat_id,
                            valid=res.valid,
                            name=res.name,
                            address=res.address,
                            checked_at=datetime.now(UTC),
                        )
                    )
                else:
                    row.valid, row.name, row.address, row.checked_at = (
                        res.valid,
                        res.name,
                        res.address,
                        datetime.now(UTC),
                    )
        return res


class StaticVies:
    """Tests: a fixed answer table; unknown ids are 'unavailable'."""

    def __init__(self, table: dict[str, bool | None]) -> None:
        self.table = table
        self.calls: list[str] = []

    async def check(self, vat_id: str) -> ViesResult:
        self.calls.append(vat_id)
        v = self.table.get(vat_id.upper(), "missing")
        if v == "missing":
            return ViesResult(None, source="vies:unavailable")
        return ViesResult(v, name="Test Co" if v else None, source="static")  # type: ignore[arg-type]
