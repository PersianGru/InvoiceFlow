"""Helpers shared by the XML parsers."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any
from xml.etree.ElementTree import Element  # noqa: S405 - elements come from defusedxml parsing

from defusedxml import ElementTree as SafeET

from app.schemas.invoice import DocumentType, ValueSource


def parse_root(xml: bytes) -> Element:
    root: Element = SafeET.fromstring(xml)
    return root


def local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def child(el: Element | None, *path: str) -> Element | None:
    """Walk children by local names (namespace-agnostic)."""
    cur = el
    for name in path:
        if cur is None:
            return None
        cur = next((c for c in cur if local(c.tag) == name), None)
    return cur


def children(el: Element | None, name: str) -> list[Element]:
    if el is None:
        return []
    return [c for c in el if local(c.tag) == name]


def text(el: Element | None, *path: str) -> str | None:
    target = child(el, *path) if path else el
    if target is None or target.text is None:
        return None
    t = target.text.strip()
    return t or None


def attr(el: Element | None, *path: str, name: str) -> str | None:
    target = child(el, *path) if path else el
    if target is None:
        return None
    v = target.get(name)
    return v.strip() if v else None


def dec(value: str | None) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(value.replace(",", ".").replace(" ", ""))
    except InvalidOperation:
        return None


def iso_date(value: str | None) -> date | None:
    if not value:
        return None
    v = value.strip()
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", v)
    if m:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m = re.match(r"^(\d{4})(\d{2})(\d{2})$", v)  # CII format 102
    if m:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    return None


UNCL1001_TYPES = {
    "380": DocumentType.invoice,
    "381": DocumentType.credit_note,
    "384": DocumentType.corrective_invoice,
    "389": DocumentType.self_billed_invoice,
    "325": DocumentType.proforma,
    "326": DocumentType.invoice,  # partial invoice
    "386": DocumentType.invoice,  # prepayment invoice
    "875": DocumentType.invoice,
    "876": DocumentType.invoice,
    "877": DocumentType.invoice,
}


def structured_meta() -> dict[str, Any]:
    return {
        "confidence": 1.0,
        "source": ValueSource.structured.value,
        "location": None,
        "ai_generated": False,
    }


@dataclass(slots=True)
class ParsedInvoice:
    """What a parser returns: the canonical fields as a plain dict (validated into ``Invoice`` by the
    runner once ids and source info are known) and the set of header fields it filled."""

    fields: dict[str, Any]
    filled: set[str] = field(default_factory=set)
    structured_format: str = ""
    syntax: str = ""

    def set(self, name: str, value: Any) -> None:
        if value is None or value == "" or value == [] or value == {}:
            return
        self.fields[name] = value
        self.filled.add(name)

    def meta(self) -> dict[str, Any]:
        """Provenance per field, with dotted paths for the members of party/totals/payment blocks."""
        out: dict[str, Any] = {}
        for name in sorted(self.filled):
            out[name] = structured_meta()
            value = self.fields.get(name)
            if isinstance(value, dict):
                for sub, v in value.items():
                    if v not in (None, "", {}, []):
                        out[f"{name}.{sub}"] = structured_meta()
        return out


def clean_vat(value: str | None) -> str | None:
    if not value:
        return None
    return re.sub(r"[\s.\-]", "", value).upper()


def normalise_iban(value: str | None) -> str | None:
    if not value:
        return None
    return re.sub(r"\s", "", value).upper()


_SKONTO = re.compile(r"#SKONTO#TAGE=(\d+)#PROZENT=(\d+(?:[.,]\d+)?)#", re.IGNORECASE)


def skonto(terms_text: str | None, issue_date: date | None) -> dict[str, Any]:
    """XRechnung's early-payment discount line ``#SKONTO#TAGE=14#PROZENT=2.00#`` (first one wins)."""
    m = _SKONTO.search(terms_text or "")
    if not m:
        return {}
    days, percent = int(m.group(1)), dec(m.group(2))
    until = issue_date + timedelta(days=days) if issue_date is not None and 0 < days <= 365 else None
    return {k: v for k, v in {"discount_percent": percent, "discount_until": until}.items() if v is not None}


def line_adjustments(items: list[dict[str, Any]]) -> dict[str, Any]:
    """Line discounts and charges (BG-27 / BG-28) folded into the line's totals; reasons joined."""
    out: dict[str, Any] = {}
    for kind, is_charge in (("allowance", False), ("charge", True)):
        mine = [a for a in items if a.get("charge") is is_charge and a.get("amount") is not None]
        if not mine:
            continue
        out[f"{kind}_amount"] = sum((a["amount"] for a in mine), Decimal(0))
        reasons = [str(a.get("reason") or a.get("reason_code") or "") for a in mine]
        if any(reasons):
            out[f"{kind}_reason"] = "; ".join(r for r in reasons if r)
        if kind == "allowance" and len(mine) == 1 and mine[0].get("percent") is not None:
            out["allowance_percent"] = mine[0]["percent"]
    return out
