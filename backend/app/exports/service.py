"""Build an export from a set of documents: pick the writer, name the file, zip when a per-document
format meets several documents. Pure functions over rows + bytes; the API layer stores and records."""

from __future__ import annotations

import io
import json
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.exports.cii_writer import to_cii
from app.exports.columns import ExportRow, default_template_columns, validate_columns
from app.exports.datev import render_datev
from app.exports.facturx import to_facturx_pdf
from app.exports.pdf_summary import render_pdf_summary
from app.exports.tabular import render_csv, render_xlsx
from app.exports.ubl_writer import to_ubl
from app.models.integration import EXPORT_FORMATS

MEDIA_TYPES = {
    "json": "application/json",
    "csv": "text/csv; charset=utf-8",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "ubl": "application/xml",
    "xrechnung": "application/xml",
    "cii": "application/xml",
    "facturx": "application/pdf",
    "pdf_summary": "application/pdf",
    "datev": "text/csv; charset=windows-1252",
    "zip": "application/zip",
}
EXTENSIONS = {
    "json": "json",
    "csv": "csv",
    "xlsx": "xlsx",
    "ubl": "xml",
    "xrechnung": "xml",
    "cii": "xml",
    "facturx": "pdf",
    "pdf_summary": "pdf",
    "datev": "csv",
}
PER_DOCUMENT = {"ubl", "xrechnung", "cii", "facturx", "pdf_summary"}
MAX_DOCUMENTS = 5000


class ExportError(ValueError):
    pass


@dataclass(slots=True)
class BuiltExport:
    data: bytes
    file_name: str
    media_type: str
    document_count: int


def _safe(name: str) -> str:
    return "".join(ch for ch in name if ch.isalnum() or ch in "._-")[:80] or "document"


def _single(fmt: str, row: ExportRow, original_pdf: bytes | None) -> bytes:
    inv = row.invoice
    if fmt == "ubl":
        return to_ubl(inv)
    if fmt == "xrechnung":
        return to_ubl(inv, xrechnung=True)
    if fmt == "cii":
        return to_cii(inv)
    if fmt == "facturx":
        return to_facturx_pdf(inv, original_pdf)
    if fmt == "pdf_summary":
        return render_pdf_summary(inv, {"Client": row.client_company_name or ""})
    raise ExportError(f"unsupported format '{fmt}'")


def build_export(
    fmt: str,
    rows: list[ExportRow],
    *,
    template_columns: list[dict[str, Any]] | None = None,
    options: dict[str, Any] | None = None,
    originals: dict[str, bytes] | None = None,
    stamp: datetime | None = None,
) -> BuiltExport:
    """``originals`` maps document id → original bytes (only needed for ``facturx``)."""
    if fmt not in EXPORT_FORMATS:
        raise ExportError(f"unsupported format '{fmt}'")
    if not rows:
        raise ExportError("nothing to export")
    if len(rows) > MAX_DOCUMENTS:
        raise ExportError(f"at most {MAX_DOCUMENTS} documents per export")
    stamp = stamp or datetime.now(UTC)
    base = f"invoiceflow-{fmt}-{stamp.strftime('%Y%m%d-%H%M%S')}"
    options = dict(options or {})
    columns = validate_columns(template_columns) if template_columns else default_template_columns()
    if fmt == "json":
        payload = {
            "schema": "https://invoiceflow.eu/schemas/invoice/v1",
            "exported_at": stamp.isoformat(),
            "count": len(rows),
            "invoices": [r.invoice.model_dump(mode="json") for r in rows],
        }
        return BuiltExport(
            json.dumps(payload, ensure_ascii=False, indent=1).encode("utf-8"),
            f"{base}.json",
            MEDIA_TYPES["json"],
            len(rows),
        )
    if fmt == "csv":
        return BuiltExport(render_csv(rows, columns, options), f"{base}.csv", MEDIA_TYPES["csv"], len(rows))
    if fmt == "xlsx":
        return BuiltExport(
            render_xlsx(rows, columns, options), f"{base}.xlsx", MEDIA_TYPES["xlsx"], len(rows)
        )
    if fmt == "datev":
        return BuiltExport(
            render_datev(rows, options, now=stamp), f"{base}.csv", MEDIA_TYPES["datev"], len(rows)
        )
    # per-document formats
    originals = originals or {}
    if len(rows) == 1:
        row = rows[0]
        data = _single(fmt, row, originals.get(str(row.document.id)))
        name = f"{_safe(row.invoice.number or str(row.document.id))}.{EXTENSIONS[fmt]}"
        return BuiltExport(data, f"{fmt}-{name}", MEDIA_TYPES[fmt], 1)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        used: set[str] = set()
        for row in rows:
            stem = _safe(row.invoice.number or str(row.document.id))
            name = f"{stem}.{EXTENSIONS[fmt]}"
            if name in used:
                name = f"{stem}-{str(row.document.id)[:8]}.{EXTENSIONS[fmt]}"
            used.add(name)
            zf.writestr(name, _single(fmt, row, originals.get(str(row.document.id))))
    return BuiltExport(buf.getvalue(), f"{base}.zip", MEDIA_TYPES["zip"], len(rows))
