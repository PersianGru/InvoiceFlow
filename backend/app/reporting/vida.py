"""ViDA digital reporting requirements (DRR): the data set of Directive (EU) 2025/516 art. 263
(a subset of EN 16931) for intra-EU B2B transactions, per client company and period.

What is reported: approved invoices whose issue date is in the period, where supplier and
customer both carry an EU VAT id and sit in different member states. Output: JSON
(``vida-drr-v0``) and CSV. The transmission to the national portal is the ``ReportingTransport``
adapter point; the default stores the file and marks the report submitted locally.

TODO(legal): the implementing act with the exact message format and the national endpoints is not
final ("ViDA").
"""

from __future__ import annotations

import csv
import io
import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.client_company import ClientCompany
from app.models.document import Document, DocumentStatus
from app.models.firm import Firm
from app.models.reporting import REPORT_FORMAT_VERSION, VidaReport
from app.storage.base import Storage

EU = set("AT BE BG HR CY CZ DK EE FI FR DE EL GR HU IE IT LV LT LU MT NL PL PT RO SK SI ES SE XI".split())

CSV_COLUMNS = (
    "document_id",
    "invoice_number",
    "issue_date",
    "document_type",
    "direction",
    "supplier_vat_id",
    "supplier_country",
    "customer_vat_id",
    "customer_country",
    "currency",
    "net_total",
    "vat_total",
    "gross_total",
    "reverse_charge",
    "vat_lines",
)


@dataclass(slots=True)
class VidaLine:
    document_id: str
    invoice_number: str | None
    issue_date: str | None
    document_type: str
    direction: str | None
    supplier_vat_id: str
    supplier_country: str
    customer_vat_id: str
    customer_country: str
    currency: str | None
    net_total: str | None
    vat_total: str | None
    gross_total: str | None
    reverse_charge: bool | None
    vat_lines: list[dict[str, Any]] = field(default_factory=list)


def vat_country(vat_id: str | None) -> str | None:
    v = "".join(ch for ch in (vat_id or "") if ch.isalnum()).upper()
    if len(v) < 4 or not v[:2].isalpha():
        return None
    cc = v[:2]
    return "GR" if cc == "EL" else cc


def is_intra_eu_b2b(extracted: dict[str, Any]) -> bool:
    supplier = (extracted.get("supplier") or {}).get("vat_id")
    buyer = (extracted.get("buyer") or {}).get("vat_id")
    sc, bc = vat_country(supplier), vat_country(buyer)
    if not sc or not bc or sc not in EU and sc != "GR" or bc not in EU and bc != "GR":
        return False
    return sc != bc


def line_from(doc: Document) -> VidaLine:
    ex = doc.extracted or {}
    totals = ex.get("totals") or {}
    supplier, buyer = ex.get("supplier") or {}, ex.get("buyer") or {}

    def s(v: Any) -> str | None:
        return None if v is None else str(v)

    return VidaLine(
        document_id=str(doc.id),
        invoice_number=ex.get("number"),
        issue_date=ex.get("issue_date"),
        document_type=str(ex.get("document_type") or "invoice"),
        direction=ex.get("direction"),
        supplier_vat_id=str(supplier.get("vat_id") or ""),
        supplier_country=vat_country(supplier.get("vat_id")) or "",
        customer_vat_id=str(buyer.get("vat_id") or ""),
        customer_country=vat_country(buyer.get("vat_id")) or "",
        currency=ex.get("currency"),
        net_total=s(totals.get("net_total")),
        vat_total=s(totals.get("vat_total")),
        gross_total=s(totals.get("gross_total")),
        reverse_charge=ex.get("reverse_charge"),
        vat_lines=[
            {
                "category": b.get("category"),
                "rate": s(b.get("rate")),
                "taxable_amount": s(b.get("taxable_amount")),
                "tax_amount": s(b.get("tax_amount")),
                "exemption_reason_code": b.get("exemption_reason_code"),
            }
            for b in (ex.get("vat_breakdown") or [])
        ],
    )


async def select_documents(
    session: AsyncSession, *, client_company_id: uuid.UUID | None, period_start: date, period_end: date
) -> list[Document]:
    stmt = select(Document).where(
        Document.deleted_at.is_(None),
        Document.processing_restricted.is_(False),
        Document.status == DocumentStatus.approved,
        Document.extracted.is_not(None),
        Document.issue_date >= period_start,
        Document.issue_date <= period_end,
    )
    if client_company_id is not None:
        stmt = stmt.where(Document.client_company_id == client_company_id)
    docs = (
        (await session.execute(stmt.order_by(Document.issue_date, Document.invoice_number))).scalars().all()
    )
    return [d for d in docs if is_intra_eu_b2b(d.extracted or {})]


def totals_of(lines: list[VidaLine]) -> dict[str, Any]:
    per_currency: dict[str, dict[str, Decimal]] = {}
    for ln in lines:
        cur = ln.currency or "EUR"
        bucket = per_currency.setdefault(cur, {"net": Decimal(0), "vat": Decimal(0), "gross": Decimal(0)})
        for key, value in (("net", ln.net_total), ("vat", ln.vat_total), ("gross", ln.gross_total)):
            if value is not None:
                bucket[key] += Decimal(value)
    return {
        "invoices": len(lines),
        "by_currency": {c: {k: str(v) for k, v in b.items()} for c, b in per_currency.items()},
    }


def render_json(
    *,
    firm: Firm,
    client: ClientCompany | None,
    period_start: date,
    period_end: date,
    lines: list[VidaLine],
    report_id: str,
) -> bytes:
    payload = {
        "format": REPORT_FORMAT_VERSION,
        "report_id": report_id,
        "generated_at": datetime.now(UTC).isoformat(),
        "reporting_party": {
            "firm": firm.name,
            "firm_country": firm.country,
            "client_company": client.name if client else None,
            "client_country": client.country if client else None,
            "client_tax_number": client.tax_number if client else None,
        },
        "period": {"start": period_start.isoformat(), "end": period_end.isoformat()},
        "basis": "Directive (EU) 2025/516 art. 263 data set; EN 16931 semantic model",
        "invoices": [asdict(ln) for ln in lines],
        "totals": totals_of(lines),
    }
    return json.dumps(payload, indent=2, ensure_ascii=False).encode("utf-8")


def render_csv(lines: list[VidaLine]) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";", lineterminator="\n")
    w.writerow(CSV_COLUMNS)
    for ln in lines:
        row = asdict(ln)
        row["vat_lines"] = json.dumps(row["vat_lines"], ensure_ascii=False)
        row["reverse_charge"] = (
            "" if row["reverse_charge"] is None else ("yes" if row["reverse_charge"] else "no")
        )
        w.writerow([row[c] if row[c] is not None else "" for c in CSV_COLUMNS])
    return buf.getvalue().encode("utf-8")


class ReportingTransport(Protocol):
    key: str

    async def submit(self, report: VidaReport, payload: bytes) -> str: ...


class StorageTransport:
    """Default: the report is kept in the archive and marked submitted locally (no national endpoint yet)."""

    key = "storage"

    async def submit(self, report: VidaReport, payload: bytes) -> str:
        return f"local:{report.id}"


async def build_report(
    session: AsyncSession,
    storage: Storage,
    *,
    firm: Firm,
    client_company_id: uuid.UUID | None,
    period_start: date,
    period_end: date,
    created_by: uuid.UUID | None,
) -> tuple[VidaReport, list[VidaLine]]:
    client = await session.get(ClientCompany, client_company_id) if client_company_id else None
    docs = await select_documents(
        session, client_company_id=client_company_id, period_start=period_start, period_end=period_end
    )
    lines = [line_from(d) for d in docs]
    report = VidaReport(
        firm_id=firm.id,
        client_company_id=client_company_id,
        period_start=period_start,
        period_end=period_end,
        document_count=len(lines),
        totals=totals_of(lines),
        created_by_user_id=created_by,
    )
    report.id = uuid.uuid4()
    report.storage_key = f"reporting/{firm.id}/vida/{report.id}"
    await storage.put(
        f"{report.storage_key}.json",
        render_json(
            firm=firm,
            client=client,
            period_start=period_start,
            period_end=period_end,
            lines=lines,
            report_id=str(report.id),
        ),
    )
    await storage.put(f"{report.storage_key}.csv", render_csv(lines))
    session.add(report)
    await session.flush()
    return report, lines
