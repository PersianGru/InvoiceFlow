"""PSD2 account-information adapters behind one protocol."""

from __future__ import annotations

from typing import Any, Protocol

from app.bank.parsers import ParsedTransaction


class BankFeedProvider(Protocol):
    key: str
    name: str

    async def test(self, config: dict[str, Any]) -> tuple[bool, str]: ...

    async def institutions(self, config: dict[str, Any], country: str) -> list[dict[str, str]]: ...

    async def start_link(
        self, config: dict[str, Any], *, institution_id: str, redirect: str, reference: str
    ) -> dict[str, str]: ...

    async def linked_accounts(self, config: dict[str, Any], requisition_id: str) -> list[dict[str, str]]: ...

    async def transactions(
        self, config: dict[str, Any], account_id: str, date_from: str
    ) -> list[ParsedTransaction]: ...
