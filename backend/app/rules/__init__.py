"""Versioned, data-driven country rule packs: VAT rates, mandatory fields, retention,
mandates. New laws are added as data, not code."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from functools import cache
from pathlib import Path
from typing import Any

PACKS_DIR = Path(__file__).resolve().parent / "packs"


@cache
def load_pack(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((PACKS_DIR / f"{name}.json").read_text(encoding="utf-8"))
    assert "version" in data, f"rule pack {name} needs a version"
    return data


def pack_version(name: str) -> str:
    return str(load_pack(name)["version"])


def vat_rates(country: str, on: date | None = None) -> list[dict[str, Any]]:
    """Rates valid in ``country`` on ``on`` (default: all current)."""
    pack = load_pack("vat_rates")
    entries = pack["countries"].get(country.upper(), [])
    out = []
    for e in entries:
        valid_from = date.fromisoformat(e["valid_from"]) if e.get("valid_from") else date.min
        valid_to = date.fromisoformat(e["valid_to"]) if e.get("valid_to") else date.max
        if on is None or valid_from <= on <= valid_to:
            out.append(
                {
                    "rate": Decimal(str(e["rate"])),
                    "kind": e["kind"],
                    "valid_from": valid_from,
                    "valid_to": valid_to,
                }
            )
    return out


def plausible_rate(country: str, rate: Decimal, on: date | None) -> bool:
    return any(r["rate"] == rate for r in vat_rates(country, on))


def mandatory_fields(country: str | None) -> list[dict[str, Any]]:
    pack = load_pack("mandatory_fields")
    fields = list(pack["eu_art_226"])
    if country:
        fields += pack["country_extras"].get(country.upper(), [])
    return fields


def retention_years(country: str | None) -> int:
    pack = load_pack("retention")
    return int(pack["countries"].get((country or "").upper(), pack["default_years"]))


def mandates() -> list[dict[str, Any]]:
    return list(load_pack("mandates")["mandates"])
