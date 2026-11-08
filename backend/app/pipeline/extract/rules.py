"""Deterministic rule-based extraction: the baseline every other provider is measured against, and
the only provider that always exists. Regex and keyword heuristics over the recognised text in the
seven launch languages; every value gets a confidence and the location of its first word."""

from __future__ import annotations

import re
import time
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from app.pipeline.types import DocumentText, ExtractionContext, ExtractionResult, Word, field_meta
from app.pipeline.validation.iban import iban_valid
from app.schemas.invoice import DocumentType, Invoice, SourceInfo, ValueSource

NAME = "rules"
VERSION = "rules-2026.11"

_NUMBER_LABELS = (
    r"(?:invoice\s*(?:no|number|#|nr)\.?|inv\.?\s*(?:no|#)|rechnungs?-?\s*(?:nr|nummer)\.?"
    r"|rechnung\s*(?:nr|nummer)?|facture\s*(?:n[°o]|num[ée]ro)?|factuur(?:nummer|nr)\.?"
    r"|factura\s*(?:n[°oº]|núm(?:ero)?)?\.?|fattura\s*(?:n[°o]|numero)?\.?|faktura\s*(?:nr|numer)?\.?"
    r"|nr\s*faktury|numer\s*faktury|n[°o]\s*de\s*facture)"
)
_NUMBER_RE = re.compile(_NUMBER_LABELS + r"\s*[:#]?\s*([A-Z0-9][A-Z0-9/\-_.]{2,30})", re.I)
_DATE_RE = re.compile(
    r"\b(\d{1,2})[./-](\d{1,2})[./-](\d{4})\b|\b(\d{4})-(\d{2})-(\d{2})\b|\b(\d{1,2})\s+(jan|feb|mar|apr|ma[iy]|jun|jul|aug|sep|o[ck]t|nov|de[cz])[a-zéû]*\.?\s+(\d{4})\b",
    re.I,
)
_MONTHS = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "mai": 5,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "okt": 10,
    "oct": 10,
    "nov": 11,
    "dec": 12,
    "dez": 12,
}
_ISSUE_LABELS = re.compile(
    r"(?:invoice\s*date|date\s*of\s*issue|rechnungsdatum|datum|date\s*de\s*facture|date|factuurdatum|fecha(?:\s*de\s*factura)?|data(?:\s*fattura)?|data\s*wystawienia)\s*[:]?\s*$",
    re.I,
)
_DUE_LABELS = re.compile(
    r"(?:due\s*date|payment\s*due|zahlbar\s*bis|fällig(?:keit)?(?:sdatum)?|f[äa]llig\s*am|date\s*d.?[ée]ch[ée]ance|[ée]ch[ée]ance|vervaldatum|betalen\s*voor|vencimiento|scadenza|termin\s*p[łl]atno[śs]ci)\s*[:]?",
    re.I,
)
_VAT_ID_RE = re.compile(
    r"\b(ATU\d{8}|BE0?\d{9,10}|BG\d{9,10}|CY\d{8}[A-Z]|CZ\d{8,10}|DE\s?\d{9}|DK\s?\d{8}|EE\d{9}|EL\d{9}|ES[A-Z0-9]\d{7}[A-Z0-9]|FI\d{8}|FR\s?[A-Z0-9]{2}\s?\d{9}|GB\s?\d{9}(?:\d{3})?|HR\d{11}|HU\d{8}|IE\d[A-Z0-9+*]\d{5}[A-Z]{1,2}|IT\s?\d{11}|LT\d{9,12}|LU\d{8}|LV\d{11}|MT\d{8}|NL\s?\d{9}B\d{2}|PL\s?\d{10}|PT\d{9}|RO\d{2,10}|SE\d{12}|SI\d{8}|SK\d{10}|CHE[-\s]?\d{3}[.\s]?\d{3}[.\s]?\d{3})\b",
    re.I,
)
_IBAN_RE = re.compile(r"\b([A-Z]{2}\d{2}(?:\s?[A-Z0-9]{4}){2,7}\s?[A-Z0-9]{1,4})\b")
_BIC_RE = re.compile(r"\b(?:BIC|SWIFT)\s*[:]?\s*([A-Z]{6}[A-Z0-9]{2}(?:[A-Z0-9]{3})?)\b", re.I)
_AMOUNT_RE = re.compile(
    r"(?<![\d.,])(\d{1,3}(?:[.\s ]\d{3})*(?:,\d{2})|\d{1,3}(?:,\d{3})*(?:\.\d{2})|\d+[.,]\d{2})(?![\d])"
)
_CURRENCY_RE = re.compile(r"\b(EUR|USD|GBP|CHF|PLN|SEK|DKK|NOK|CZK|HUF|RON|BGN|HRK)\b|(€|\$|£|zł|Kč|kr)")
_TOTAL_LABELS = re.compile(
    r"(?:total\s*(?:amount)?(?:\s*due)?|grand\s*total|amount\s*due|gesamt(?:betrag|summe)?|rechnungsbetrag|endbetrag|zu\s*zahlen|brutto(?:betrag)?|total\s*ttc|montant\s*(?:total|ttc|à\s*payer)|totaal(?:bedrag)?|te\s*betalen|total\s*(?:factura)?|importe\s*total|totale\s*(?:fattura|documento)?|importo\s*totale|razem\s*(?:do\s*zap[łl]aty)?|do\s*zap[łl]aty|suma\s*brutto|warto[śs][ćc]\s*brutto)\b",
    re.I,
)
_NET_LABELS = re.compile(
    r"(?:sub\s*-?total|net(?:to)?(?:\s*(?:amount|betrag|summe))?|total\s*ht|montant\s*ht|subtotaal|excl\.?\s*btw|base\s*imponible|imponibile|suma\s*netto|warto[śs][ćc]\s*netto|razem\s*netto|zwischensumme)\b",
    re.I,
)
_VAT_LABELS = re.compile(
    r"(?:\bvat\b|\bmwst\.?|\bust\.?|umsatzsteuer|\btva\b|\bbtw\b|\biva\b|\bpodatek\b|\bvat\s*\d|kwota\s*vat)",
    re.I,
)
_RATE_RE = re.compile(r"(\d{1,2}(?:[.,]\d{1,2})?)\s*%")
_PO_RE = re.compile(
    r"(?:purchase\s*order|p\.?o\.?\s*(?:no|number|#)|bestell(?:nr|nummer)|commande\s*n[°o]|inkooporder|pedido|ordine|zam[óo]wienie)\s*[:#]?\s*([A-Z0-9][A-Z0-9/\-_.]{2,30})",
    re.I,
)
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

_TYPE_HINTS: list[tuple[DocumentType, tuple[str, ...]]] = [
    (
        DocumentType.credit_note,
        (
            "credit note",
            "gutschrift",
            "avoir",
            "creditnota",
            "creditfactuur",
            "nota de crédito",
            "nota di credito",
            "faktura korygująca",
            "korekta",
        ),
    ),
    (DocumentType.proforma, ("proforma", "pro-forma", "pro forma")),
    (
        DocumentType.reminder,
        (
            "reminder",
            "mahnung",
            "zahlungserinnerung",
            "rappel",
            "relance",
            "herinnering",
            "aanmaning",
            "recordatorio",
            "sollecito",
            "wezwanie do zapłaty",
            "przypomnienie",
        ),
    ),
    (
        DocumentType.receipt,
        (
            "receipt",
            "kassenbon",
            "kassenzettel",
            "quittung",
            "beleg",
            "ticket de caisse",
            "reçu",
            "kassabon",
            "bon",
            "recibo",
            "ticket",
            "scontrino",
            "ricevuta",
            "paragon",
        ),
    ),
    (DocumentType.invoice, ("invoice", "rechnung", "facture", "factuur", "factura", "fattura", "faktura")),
]


def _dec(s: str) -> Decimal | None:
    s = s.strip()
    if re.fullmatch(r"\d{1,3}(?:[.\s ]\d{3})*(?:,\d{2})", s):
        s = s.replace(".", "").replace(" ", "").replace(" ", "").replace(",", ".")
    elif re.fullmatch(r"\d{1,3}(?:,\d{3})*(?:\.\d{2})", s):
        s = s.replace(",", "")
    else:
        s = s.replace(",", ".")
    try:
        return Decimal(s)
    except InvalidOperation:
        return None


def _parse_date(m: re.Match[str]) -> date | None:
    try:
        if m.group(1):
            d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
            if mo > 12 and d <= 12:
                d, mo = mo, d
            return date(y, mo, d)
        if m.group(4):
            return date(int(m.group(4)), int(m.group(5)), int(m.group(6)))
        if m.group(7):
            mon = _MONTHS.get(m.group(8).lower()[:3])
            return date(int(m.group(9)), mon, int(m.group(7))) if mon else None
    except ValueError:
        return None
    return None


class RulesProvider:
    name = NAME
    external = False

    async def available(self) -> bool:
        return True

    async def extract(self, text: DocumentText, ctx: ExtractionContext) -> ExtractionResult:
        t0 = time.monotonic()
        full = text.full_text
        words = text.words
        meta: dict[str, Any] = {}
        fields: dict[str, Any] = {
            "supplier": {},
            "buyer": {},
            "totals": {},
            "payment": {},
            "references": {},
            "vat_breakdown": [],
            "lines": [],
        }

        def locate(value: str) -> Word | None:
            first = value.split()[0] if value.split() else value
            return next(
                (w for w in words if first and (first in w.text or w.text in first) and len(w.text) >= 2),
                None,
            )

        def put(path: str, value: Any, conf: float, source_text: str | None = None) -> None:
            if value is None:
                return
            parts = path.split(".")
            d = fields
            for p in parts[:-1]:
                d = d.setdefault(p, {})
            d[parts[-1]] = value
            w = locate(source_text or str(value))
            meta[path] = field_meta(conf, ValueSource.ocr, w.location if w else None)

        # document type
        low = full.lower()
        for dtype, hints in _TYPE_HINTS:
            if any(h in low for h in hints):
                put("document_type", dtype.value, 0.7 if dtype != DocumentType.invoice else 0.8, hints[0])
                break
        else:
            put("document_type", DocumentType.non_invoice.value, 0.4)

        # number: the first labelled candidate that contains a digit (a bare title never qualifies)
        pos = 0
        while (m := _NUMBER_RE.search(full, pos)) is not None:
            cand = m.group(1).rstrip(".:,")
            if any(ch.isdigit() for ch in cand):
                put("number", cand, 0.85, cand)
                break
            pos = m.start() + 1

        # dates: issue = first date near an issue label (or the earliest date); due = date after a due label
        dates: list[tuple[int, date, str]] = []
        for dm in _DATE_RE.finditer(full):
            d = _parse_date(dm)
            if d and 2000 <= d.year <= 2100:
                dates.append((dm.start(), d, dm.group(0)))
        if dates:
            due = None
            lm = _DUE_LABELS.search(full)
            if lm:
                after = [x for x in dates if x[0] >= lm.end() and x[0] - lm.end() < 60]
                if after:
                    due = after[0]
            issue = None
            for pos, d, raw in dates:
                before = full[max(0, pos - 40) : pos]
                if _ISSUE_LABELS.search(before.strip() + " ") or re.search(
                    r"(?:date|datum|fecha|data)\s*[:]?\s*$", before, re.I
                ):
                    issue = (pos, d, raw)
                    break
            if issue is None:
                candidates = [x for x in dates if x != due]
                issue = min(candidates, key=lambda x: x[1]) if candidates else None
            if issue:
                put(
                    "issue_date",
                    issue[1].isoformat(),
                    0.8 if _ISSUE_LABELS.search(full[max(0, issue[0] - 40) : issue[0]] + " ") else 0.55,
                    issue[2],
                )
            if due:
                put("payment.due_date", due[1].isoformat(), 0.75, due[2])

        # currency
        cm = _CURRENCY_RE.search(full)
        if cm:
            code = (
                cm.group(1)
                or {"€": "EUR", "$": "USD", "£": "GBP", "zł": "PLN", "Kč": "CZK", "kr": "SEK"}[cm.group(2)]
            )
            put("currency", code.upper(), 0.8 if cm.group(1) else 0.6, cm.group(0))

        # parties: VAT ids (first = supplier unless it is one of the client's own), IBAN, BIC, email
        vat_ids = []
        for vm in _VAT_ID_RE.finditer(full):
            vid = re.sub(r"[\s.\-]", "", vm.group(1)).upper()
            if vid not in vat_ids:
                vat_ids.append(vid)
        own = {re.sub(r"[\s.\-]", "", v).upper() for v in ctx.buyer_vat_ids}
        supplier_vat = next((v for v in vat_ids if v not in own), None)
        buyer_vat = next((v for v in vat_ids if v in own), None) or (
            vat_ids[1] if len(vat_ids) > 1 and vat_ids[0] == supplier_vat else None
        )
        if supplier_vat:
            put("supplier.vat_id", supplier_vat, 0.8, supplier_vat[:4])
        if buyer_vat:
            put("buyer.vat_id", buyer_vat, 0.7, buyer_vat[:4])
        for im in _IBAN_RE.finditer(full):
            cand = re.sub(r"\s", "", im.group(1)).upper()
            if iban_valid(cand):
                put("supplier.iban", cand, 0.85, im.group(1)[:8])
                break
        bm = _BIC_RE.search(full)
        if bm:
            put("supplier.bic", bm.group(1).upper(), 0.8, bm.group(1))
        em = _EMAIL_RE.search(full)
        if em:
            put("supplier.email", em.group(0), 0.5, em.group(0))
        # supplier name: first non-empty line that is not a label/number
        for line in full.split("\n")[:8]:
            s = line.strip()
            if (
                len(s) >= 3
                and not re.search(r"\d{3,}", s)
                and not re.match(_NUMBER_LABELS, s, re.I)
                and "@" not in s
            ):
                put("supplier.name", s[:200], 0.5, s)
                break
        po = _PO_RE.search(full)
        if po:
            put("references.purchase_order", po.group(1), 0.7, po.group(1))

        # totals by labelled lines
        gross: tuple[Decimal, str] | None = None
        net: tuple[Decimal, str] | None = None
        vat: tuple[Decimal, str] | None = None
        for line in full.split("\n"):
            amounts = [a for a in (_dec(x) for x in _AMOUNT_RE.findall(line)) if a is not None]
            if not amounts:
                continue
            if (
                gross is None
                and _TOTAL_LABELS.search(line)
                and not _NET_LABELS.search(line)
                and not re.search(r"\bvat\b|mwst|tva|btw|iva", line, re.I)
            ):
                gross = (amounts[-1], line)
            elif net is None and _NET_LABELS.search(line):
                net = (amounts[-1], line)
            elif vat is None and _VAT_LABELS.search(line) and not _TOTAL_LABELS.search(line):
                vat = (amounts[-1], line)
        if gross is None:
            all_amounts = [a for a in (_dec(x) for x in _AMOUNT_RE.findall(full)) if a is not None]
            if all_amounts:
                gross = (max(all_amounts), str(max(all_amounts)))
                put("totals.gross_total", str(gross[0]), 0.4, gross[1])
        else:
            put("totals.gross_total", str(gross[0]), 0.75, str(gross[0]))
        if net:
            put("totals.net_total", str(net[0]), 0.7, str(net[0]))
        if vat:
            put("totals.vat_total", str(vat[0]), 0.65, str(vat[0]))
        if gross is not None and net is not None and vat is None:
            gross_amount, net_amount = gross[0], net[0]
            if gross_amount >= net_amount:
                put("totals.vat_total", str(gross_amount - net_amount), 0.5)
        rate = None
        for rm in _RATE_RE.finditer(full):
            r = _dec(rm.group(1))
            if r is not None and 0 <= r <= 30:
                rate = r
                break
        if rate is not None and net:
            fields["vat_breakdown"] = [
                {
                    "rate": str(rate),
                    "taxable_amount": str(net[0]),
                    "tax_amount": str(vat[0]) if vat else None,
                    "category": "S" if rate > 0 else "Z",
                }
            ]
            meta["vat_breakdown"] = field_meta(0.5, ValueSource.ocr)

        if ctx.language_hint or text.language:
            fields["language"] = ctx.language_hint or text.language
        invoice = Invoice.model_validate(
            {
                "id": ctx.document_id,
                "firm_id": ctx.firm_id,
                "client_company_id": ctx.client_company_id,
                "source": SourceInfo(
                    channel="upload",
                    received_at="1970-01-01T00:00:00Z",
                    original_file_id=ctx.document_id,
                    original_file_sha256="0" * 64,
                ).model_dump(),
                "meta": meta,
                **{k: v for k, v in fields.items() if v not in ({}, [], None)},
            }
        )
        return ExtractionResult(
            invoice=invoice,
            provider=NAME,
            model=None,
            prompt_version=VERSION,
            duration_ms=int((time.monotonic() - t0) * 1000),
        )
