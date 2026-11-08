"""CSV and XLSX writers driven by a template (column list + options)."""

from __future__ import annotations

import csv
import io
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from app.exports.columns import COLUMN_INDEX, ExportRow, format_value, value_of


def _expand_rows(rows: list[ExportRow], per_line: bool) -> list[ExportRow]:
    if not per_line:
        return rows
    out: list[ExportRow] = []
    for r in rows:
        if not r.invoice.lines:
            out.append(r)
            continue
        for i in range(len(r.invoice.lines)):
            out.append(ExportRow(r.invoice, r.document, r.client_company_name, i))
    return out


def render_csv(rows: list[ExportRow], columns: list[dict[str, str]], options: dict[str, Any]) -> bytes:
    """``options``: delimiter (default ';' when the decimal separator is ',' else ','), decimal_separator
    ('.' or ','), date_format (strftime), bom (True: UTF-8 with BOM so Excel opens it), per_line (one
    row per line item), encoding (utf-8 default)."""
    dec = str(options.get("decimal_separator") or ".")
    delimiter = str(options.get("delimiter") or (";" if dec == "," else ","))
    date_format = str(options.get("date_format") or "%Y-%m-%d")
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=delimiter, quoting=csv.QUOTE_MINIMAL, lineterminator="\r\n")
    writer.writerow([c["header"] for c in columns])
    for row in _expand_rows(rows, bool(options.get("per_line"))):
        writer.writerow(
            [
                format_value(
                    value_of(row, c["key"]),
                    COLUMN_INDEX[c["key"]].kind,
                    decimal_separator=dec,
                    date_format=date_format,
                )
                for c in columns
            ]
        )
    encoding = str(options.get("encoding") or "utf-8")
    data = buf.getvalue().encode(encoding, errors="replace")
    if options.get("bom", True) and encoding.lower().replace("-", "") == "utf8":
        data = b"\xef\xbb\xbf" + data
    return data


def render_xlsx(rows: list[ExportRow], columns: list[dict[str, str]], options: dict[str, Any]) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Font
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    ws = wb.active
    assert ws is not None
    ws.title = str(options.get("sheet_name") or "Invoices")[:31]
    ws.append([c["header"] for c in columns])
    for cell in ws[1]:
        cell.font = Font(bold=True)
    ws.freeze_panes = "A2"
    for row in _expand_rows(rows, bool(options.get("per_line"))):
        values: list[Any] = []
        for c in columns:
            v = value_of(row, c["key"])
            if isinstance(v, Decimal):
                v = float(v)
            elif isinstance(v, datetime):
                v = v.replace(tzinfo=None)
            values.append(v)
        ws.append(values)
    for idx, c in enumerate(columns, start=1):
        kind = COLUMN_INDEX[c["key"]].kind
        letter = get_column_letter(idx)
        width = 14
        if kind == "money":
            for cell in ws[letter][1:]:
                cell.number_format = "#,##0.00"
        elif kind == "date":
            width = 12
            for cell in ws[letter][1:]:
                cell.number_format = "yyyy-mm-dd"
        elif kind == "datetime":
            width = 18
            for cell in ws[letter][1:]:
                cell.number_format = "yyyy-mm-dd hh:mm"
        elif kind == "text":
            width = max(14, min(40, len(c["header"]) + 4))
        ws.column_dimensions[letter].width = width
    ws.auto_filter.ref = ws.dimensions
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def _as_date(v: Any) -> date | None:
    return v if isinstance(v, date) else None
