"""DATEV Buchungsstapel (EXTF, format version 700, category 21) as a CSV for DATEV import.

One booking per invoice: the gross amount, S/H from the direction (purchase invoices credit the
supplier account), currency, account and contra account, BU key from the VAT code, document date
(DDMM), document field 1 = invoice number, booking text = supplier + number. Header fields that
identify the consultant/client and the fiscal year come from the template options:

``datev_consultant_number``, ``datev_client_number``, ``fiscal_year_start`` (YYYYMMDD),
``account_length`` (default 4), ``payables_account`` (default 70000 → "Kreditoren Sammelkonto"),
``receivables_account`` (default 10000), ``default_expense_account`` (default 6800),
``default_revenue_account`` (default 8400).

Encoding Windows-1252, delimiter ``;``, CRLF, text fields quoted — per DATEV's public description
of the format. The DATEV-connect connector will reuse these rows.
"""

from __future__ import annotations

import io
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from app.exports.columns import ExportRow

HEADERS = [
    "Umsatz (ohne Soll/Haben-Kz)",
    "Soll/Haben-Kennzeichen",
    "WKZ Umsatz",
    "Kurs",
    "Basis-Umsatz",
    "WKZ Basis-Umsatz",
    "Konto",
    "Gegenkonto (ohne BU-Schlüssel)",
    "BU-Schlüssel",
    "Belegdatum",
    "Belegfeld 1",
    "Belegfeld 2",
    "Skonto",
    "Buchungstext",
    "Postensperre",
    "Diverse Adressnummer",
    "Geschäftspartnerbank",
    "Sachverhalt",
    "Zinssperre",
    "Beleglink",
    "Beleginfo - Art 1",
    "Beleginfo - Inhalt 1",
    "KOST1 - Kostenstelle",
    "KOST2 - Kostenstelle",
    "Kost-Menge",
    "EU-Land u. UStID (Bestimmung)",
    "EU-Steuersatz (Bestimmung)",
    "Abw. Versteuerungsart",
    "Sachverhalt L+L",
    "Funktionsergänzung L+L",
    "BU 49 Hauptfunktionstyp",
    "BU 49 Hauptfunktionsnummer",
    "BU 49 Funktionsergänzung",
    "Fälligkeit",
]

# BU keys (Buchungsschlüssel) for the common German VAT cases; the template may override per code.
BU_KEYS: dict[str, str] = {
    "S19": "9",  # 19 % input tax
    "S7": "8",  # 7 % input tax
    "S0": "",
    "Z": "",
    "E": "",
    "AE": "94",  # reverse charge §13b, 19 %
    "K": "19",  # intra-community acquisition 19 %
    "G": "",
    "O": "",
}


def _q(text: Any) -> str:
    s = "" if text is None else str(text)
    return '"' + s.replace('"', '""') + '"'


def _amount(v: Decimal | None) -> str:
    return f"{(v or Decimal(0)).quantize(Decimal('0.01'))}".replace(".", ",")


def _ddmm(d: date | None) -> str:
    return d.strftime("%d%m") if d else ""


def render_datev(rows: list[ExportRow], options: dict[str, Any], *, now: datetime | None = None) -> bytes:
    now = now or datetime.now(UTC)
    dates = [r.invoice.issue_date for r in rows if r.invoice.issue_date]
    first, last = (min(dates), max(dates)) if dates else (now.date(), now.date())
    fy = str(options.get("fiscal_year_start") or f"{first.year}0101")
    header = [
        '"EXTF"',
        "700",
        "21",
        '"Buchungsstapel"',
        "12",
        now.strftime("%Y%m%d%H%M%S000"),
        "",
        '"IF"',
        '"InvoiceFlow"',
        "",
        str(options.get("datev_consultant_number") or ""),
        str(options.get("datev_client_number") or ""),
        fy,
        str(options.get("account_length") or 4),
        first.strftime("%Y%m%d"),
        last.strftime("%Y%m%d"),
        _q(options.get("batch_name") or "InvoiceFlow export"),
        '"IF"',
        "1",
        "0",
        "0",
        _q(options.get("currency") or "EUR"),
        "",
        "",
        "",
        "",
        "",
        "",
        "",
        "",
        "",
    ]
    out = io.StringIO()
    out.write(";".join(header) + "\r\n")
    out.write(";".join(_q(h) for h in HEADERS) + "\r\n")
    payables = str(options.get("payables_account") or "70000")
    receivables = str(options.get("receivables_account") or "10000")
    expense = str(options.get("default_expense_account") or "6800")
    revenue = str(options.get("default_revenue_account") or "8400")
    bu_overrides = dict(options.get("bu_keys") or {})
    for r in rows:
        inv = r.invoice
        is_sales = inv.direction is not None and inv.direction.value == "sales"
        gross = inv.totals.gross_total or Decimal(0)
        credit = inv.document_type.value == "credit_note"
        sh = ("S" if is_sales else "H") if not credit else ("H" if is_sales else "S")
        account = receivables if is_sales else payables
        contra = inv.suggested_gl_account or (revenue if is_sales else expense)
        vat_code = inv.suggested_vat_code or ""
        bu = str(bu_overrides.get(vat_code, BU_KEYS.get(vat_code, "")))
        text = f"{inv.supplier.name or ''} {inv.number or ''}".strip()[:60]
        fields = [
            _amount(abs(gross)),
            _q(sh),
            _q(inv.currency or options.get("currency") or "EUR"),
            "",
            "",
            "",
            _q(account),
            _q(contra),
            _q(bu),
            _ddmm(inv.issue_date),
            _q((inv.number or "")[:36]),
            _q((inv.payment.payment_reference or "")[:12]),
            "",
            _q(text),
            "",
            "",
            "",
            "",
            "",
            _q(str(r.document.id)),
            _q("Beleg"),
            _q(r.document.original_filename[:36]),
            _q(inv.suggested_cost_category or ""),
            "",
            "",
            _q(inv.buyer.vat_id or "") if inv.intra_community else "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            inv.payment.due_date.strftime("%d%m%Y") if inv.payment.due_date else "",
        ]
        out.write(";".join(fields) + "\r\n")
    return out.getvalue().encode("cp1252", errors="replace")
