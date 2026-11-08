"""Retention from the rule pack: the period depends on the client company's country
(else the firm's), counted in whole years from the end of the calendar year the document was
received (the GoBD/AO convention); the firm's own ``retention_days`` is a floor, never a cap below
the legal period."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.rules import load_pack, retention_years


def retention_until_for(
    *, received_at: datetime, country: str | None, firm_retention_days: int
) -> tuple[datetime, str]:
    """Returns (retention_until, rule label) e.g. ``("2036-12-31T23:59:59+00:00", "DE:10y")``."""
    years = retention_years(country)
    end_of_year = datetime(received_at.year, 12, 31, 23, 59, 59, tzinfo=UTC)
    legal = end_of_year.replace(year=end_of_year.year + years)
    floor = received_at + timedelta(days=max(0, firm_retention_days))
    pack_version = load_pack("retention")["version"]
    label = f"{(country or 'default').upper()}:{years}y:{pack_version}"
    if floor > legal:
        return floor, label + "+firm-floor"  # the firm keeps documents longer than the law requires
    return legal, label
