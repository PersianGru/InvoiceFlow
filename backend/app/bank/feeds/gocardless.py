"""GoCardless Bank Account Data (the former Nordigen) — PSD2 account information for ~2,500 EU
banks. Flow: secret id/key → access token; requisition (bank consent link) → linked accounts →
booked transactions since a date. Documented at https://developer.gocardless.com/bank-account-data/."""

from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from app.bank.parsers import ParsedTransaction, normalise_iban

log = logging.getLogger("invoiceflow.bank.gocardless")

BASE_URL = "https://bankaccountdata.gocardless.com/api/v2"


class FeedError(Exception):
    pass


class GocardlessFeed:
    key = "gocardless"
    name = "GoCardless Bank Account Data (PSD2)"

    def __init__(self, http: httpx.AsyncClient | None = None, base_url: str = BASE_URL) -> None:
        self.http = http or httpx.AsyncClient(timeout=30, follow_redirects=False)
        self.base = base_url.rstrip("/")

    async def _token(self, config: dict[str, Any]) -> str:
        sid, key = str(config.get("secret_id") or ""), str(config.get("secret_key") or "")
        if not sid or not key:
            raise FeedError("secret id and key are required")
        r = await self.http.post(f"{self.base}/token/new/", json={"secret_id": sid, "secret_key": key})
        if r.status_code != 200:
            raise FeedError(f"token refused ({r.status_code})")
        access = str(r.json().get("access") or "")
        if not access:
            raise FeedError("no access token")
        return access

    async def _get(self, config: dict[str, Any], path: str, **params: Any) -> Any:
        token = await self._token(config)
        r = await self.http.get(
            f"{self.base}{path}", headers={"authorization": f"Bearer {token}"}, params=params or None
        )
        if r.status_code >= 400:
            raise FeedError(f"provider answered {r.status_code} on {path.split('/')[1]}")
        return r.json()

    async def test(self, config: dict[str, Any]) -> tuple[bool, str]:
        try:
            await self._token(config)
        except (FeedError, httpx.HTTPError) as e:
            return False, f"Connection failed: {e if isinstance(e, FeedError) else type(e).__name__}."
        return True, "Connected to GoCardless Bank Account Data."

    async def institutions(self, config: dict[str, Any], country: str) -> list[dict[str, str]]:
        data = await self._get(config, "/institutions/", country=country.upper())
        return [
            {"id": str(i.get("id", "")), "name": str(i.get("name", "")), "bic": str(i.get("bic") or "")}
            for i in data
            if isinstance(i, dict)
        ]

    async def start_link(
        self, config: dict[str, Any], *, institution_id: str, redirect: str, reference: str
    ) -> dict[str, str]:
        token = await self._token(config)
        r = await self.http.post(
            f"{self.base}/requisitions/",
            headers={"authorization": f"Bearer {token}"},
            json={
                "redirect": redirect,
                "institution_id": institution_id,
                "reference": reference,
                "user_language": "EN",
            },
        )
        if r.status_code not in (200, 201):
            raise FeedError(f"requisition refused ({r.status_code})")
        body = r.json()
        return {
            "id": str(body.get("id", "")),
            "link": str(body.get("link", "")),
            "status": str(body.get("status", "")),
        }

    async def linked_accounts(self, config: dict[str, Any], requisition_id: str) -> list[dict[str, str]]:
        req = await self._get(config, f"/requisitions/{requisition_id}/")
        if str(req.get("status")) != "LN":
            raise FeedError(f"bank consent not completed (status {req.get('status')})")
        out = []
        for account_id in req.get("accounts") or []:
            details = (await self._get(config, f"/accounts/{account_id}/details/")).get("account") or {}
            out.append(
                {
                    "id": str(account_id),
                    "iban": normalise_iban(str(details.get("iban") or "")) or "",
                    "currency": str(details.get("currency") or "EUR").upper(),
                    "name": str(
                        details.get("name") or details.get("ownerName") or details.get("product") or ""
                    ),
                    "bic": str(details.get("bic") or ""),
                }
            )
        return out

    async def transactions(
        self, config: dict[str, Any], account_id: str, date_from: str
    ) -> list[ParsedTransaction]:
        data = await self._get(config, f"/accounts/{account_id}/transactions/", date_from=date_from)
        booked = ((data.get("transactions") or {}).get("booked") or []) if isinstance(data, dict) else []
        out: list[ParsedTransaction] = []
        for t in booked:
            if not isinstance(t, dict):
                continue
            amt = t.get("transactionAmount") or {}
            try:
                amount = Decimal(str(amt.get("amount"))).quantize(Decimal("0.01"))
            except (InvalidOperation, TypeError):
                continue
            try:
                booking = date.fromisoformat(str(t.get("bookingDate"))[:10])
            except ValueError:
                continue
            try:
                value_date = date.fromisoformat(str(t.get("valueDate"))[:10]) if t.get("valueDate") else None
            except ValueError:
                value_date = None
            if amount < 0:
                name, iban = t.get("creditorName"), (t.get("creditorAccount") or {}).get("iban")
            else:
                name, iban = t.get("debtorName"), (t.get("debtorAccount") or {}).get("iban")
            remittance = t.get("remittanceInformationUnstructured") or " ".join(
                t.get("remittanceInformationUnstructuredArray") or []
            )
            ext = str(t.get("transactionId") or t.get("internalTransactionId") or "")
            if not ext:
                ext = f"h:{account_id[:8]}:{booking}:{amount}:{remittance[:40]}"
            out.append(
                ParsedTransaction(
                    external_id=ext[:120],
                    booking_date=booking,
                    value_date=value_date,
                    amount=amount,
                    currency=str(amt.get("currency") or "EUR").upper(),
                    counterparty_name=(str(name)[:200] if name else None),
                    counterparty_iban=normalise_iban(str(iban)) if iban else None,
                    remittance_info=(str(remittance)[:1000] or None),
                    end_to_end_id=(str(t.get("endToEndId"))[:70] if t.get("endToEndId") else None),
                    raw={"bankTransactionCode": t.get("bankTransactionCode")},
                )
            )
        return out
