"""Helpers shared by the XML parsers."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
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
