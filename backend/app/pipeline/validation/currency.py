"""Currency detection and conversion with ECB reference rates (EUR base) for the invoice date.
Original and converted amounts are both stored."""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Protocol

import httpx
from defusedxml import ElementTree as SafeET
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.pipeline import FxRate
from app.schemas.invoice import Invoice, ValidationLevel, ValidationResult

log = logging.getLogger("invoiceflow.pipeline.currency")
ISO_CURRENCIES = frozenset(
    (
        "EUR USD GBP CHF PLN SEK DKK NOK CZK HUF RON BGN ISK JPY CAD AUD CNY HKD SGD NZD TRY ZAR "
        "BRL MXN INR KRW IDR MYR PHP THB ILS"
    ).split()
)
ECB_90D = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-hist-90d.xml"
ECB_HIST = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-hist.xml"


class RatesProvider(Protocol):
    async def rate(self, on: date, currency: str) -> Decimal | None:
        """Units of ``currency`` per 1 EUR on ``on`` (previous business day when ``on`` has none)."""


class StaticRates:
    def __init__(self, table: dict[str, Decimal]) -> None:
        self.table = {k.upper(): v for k, v in table.items()}

    async def rate(self, on: date, currency: str) -> Decimal | None:
        if currency.upper() == "EUR":
            return Decimal(1)
        return self.table.get(currency.upper())


def parse_ecb_xml(xml: bytes) -> dict[date, dict[str, Decimal]]:
    root = SafeET.fromstring(xml)
    out: dict[date, dict[str, Decimal]] = {}
    for cube in root.iter():
        t = cube.get("time")
        if t:
            day = date.fromisoformat(t)
            out[day] = {c.get("currency", ""): Decimal(c.get("rate", "0")) for c in cube if c.get("currency")}
    return out


class EcbRates:
    """Fetches the ECB 90-day history (daily, cached in ``fx_rates``); older dates use the full history."""

    def __init__(
        self, sessionmaker: async_sessionmaker[AsyncSession], http: httpx.AsyncClient | None = None
    ) -> None:
        self.sessionmaker = sessionmaker
        self._http = http or httpx.AsyncClient(timeout=httpx.Timeout(20.0))
        self._last_fetch: datetime | None = None

    async def _ensure(self, on: date) -> None:
        now = datetime.now(UTC)
        if (
            self._last_fetch
            and now - self._last_fetch < timedelta(hours=12)
            and on >= now.date() - timedelta(days=85)
        ):
            return
        url = ECB_90D if on >= now.date() - timedelta(days=85) else ECB_HIST
        try:
            r = await self._http.get(url)
            r.raise_for_status()
            table = parse_ecb_xml(r.content)
        except (httpx.HTTPError, ValueError) as e:
            log.warning("ECB rates fetch failed: %s", type(e).__name__)
            return
        async with self.sessionmaker() as s:
            async with s.begin():
                since = min(table) if table else date.today()
                existing = {
                    (row.rate_date, row.currency)
                    for row in (
                        await s.execute(
                            select(FxRate.rate_date, FxRate.currency).where(FxRate.rate_date >= since)
                        )
                    ).all()
                }
                for day, rates in table.items():
                    for cur, rate in rates.items():
                        if (day, cur) not in existing:
                            s.add(FxRate(rate_date=day, currency=cur, rate=rate))
        self._last_fetch = now

    async def rate(self, on: date, currency: str) -> Decimal | None:
        currency = currency.upper()
        if currency == "EUR":
            return Decimal(1)
        async with self.sessionmaker() as s:
            async with s.begin():
                row = (
                    (
                        await s.execute(
                            select(FxRate)
                            .where(
                                FxRate.currency == currency,
                                FxRate.rate_date <= on,
                                FxRate.rate_date >= on - timedelta(days=7),
                            )
                            .order_by(FxRate.rate_date.desc())
                            .limit(1)
                        )
                    )
                    .scalars()
                    .first()
                )
        if row is None:
            await self._ensure(on)
            async with self.sessionmaker() as s:
                async with s.begin():
                    row = (
                        (
                            await s.execute(
                                select(FxRate)
                                .where(
                                    FxRate.currency == currency,
                                    FxRate.rate_date <= on,
                                    FxRate.rate_date >= on - timedelta(days=7),
                                )
                                .order_by(FxRate.rate_date.desc())
                                .limit(1)
                            )
                        )
                        .scalars()
                        .first()
                    )
        return row.rate if row is not None else None


def check_format(inv: Invoice) -> list[ValidationResult]:
    if not inv.currency:
        return [
            ValidationResult(
                check="currency.present",
                level=ValidationLevel.warning,
                reason="No currency found",
                field="currency",
            )
        ]
    if inv.currency.upper() not in ISO_CURRENCIES:
        return [
            ValidationResult(
                check="currency.present",
                level=ValidationLevel.warning,
                reason=f"Unknown currency code {inv.currency}",
                field="currency",
            )
        ]
    return [
        ValidationResult(
            check="currency.present", level=ValidationLevel.passed, reason=f"Currency {inv.currency.upper()}"
        )
    ]


async def check(
    inv: Invoice, rates: RatesProvider, base_currency: str
) -> tuple[Invoice, list[ValidationResult]]:
    out = check_format(inv)
    if not inv.currency or inv.currency.upper() not in ISO_CURRENCIES or inv.totals.gross_total is None:
        return inv, out
    cur = inv.currency.upper()
    if cur == base_currency.upper():
        return (
            inv.model_copy(
                update={
                    "converted_currency": base_currency.upper(),
                    "converted_gross_total": inv.totals.gross_total,
                    "exchange_rate": Decimal(1),
                }
            ),
            out,
        )
    on = inv.issue_date or date.today()
    per_eur = await rates.rate(on, cur)
    base_per_eur = await rates.rate(on, base_currency) if base_currency.upper() != "EUR" else Decimal(1)
    if per_eur is None or base_per_eur is None or per_eur == 0:
        out.append(
            ValidationResult(
                check="currency.conversion",
                level=ValidationLevel.warning,
                reason=f"No ECB reference rate for {cur} on {on}; amount not converted",
                field="currency",
            )
        )
        return inv, out
    rate_to_base = (base_per_eur / per_eur).quantize(Decimal("0.000001"))
    converted = (inv.totals.gross_total * rate_to_base).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    out.append(
        ValidationResult(
            check="currency.conversion",
            level=ValidationLevel.passed,
            reason=(
                f"Converted {inv.totals.gross_total} {cur} to {converted} {base_currency.upper()} "
                f"at the ECB rate of {on}"
            ),
            details={"rate": str(rate_to_base), "date": on.isoformat()},
        )
    )
    return (
        inv.model_copy(
            update={
                "converted_currency": base_currency.upper(),
                "converted_gross_total": converted,
                "exchange_rate": rate_to_base,
            }
        ),
        out,
    )
