"""Poland KSeF FA(2)/FA(3) XML → canonical fields (rates by P_12 code; totals from P_13/P_14/P_15)."""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from xml.etree.ElementTree import Element  # noqa: S405

from app.pipeline.formats.common import (
    ParsedInvoice,
    child,
    children,
    dec,
    iso_date,
    normalise_iban,
    parse_root,
    text,
)
from app.schemas.invoice import DocumentType

RODZAJ = {
    "VAT": DocumentType.invoice,
    "KOR": DocumentType.corrective_invoice,
    "ZAL": DocumentType.invoice,
    "ROZ": DocumentType.invoice,
    "UPR": DocumentType.invoice,
    "KOR_ZAL": DocumentType.corrective_invoice,
    "KOR_ROZ": DocumentType.corrective_invoice,
}

# P_12 codes → (rate, category)
P12 = {
    "23": (Decimal(23), "S"),
    "22": (Decimal(22), "S"),
    "8": (Decimal(8), "S"),
    "7": (Decimal(7), "S"),
    "5": (Decimal(5), "S"),
    "4": (Decimal(4), "S"),
    "3": (Decimal(3), "S"),
    "0": (Decimal(0), "Z"),
    "zw": (Decimal(0), "E"),
    "np": (Decimal(0), "O"),
    "oo": (Decimal(0), "AE"),
}

# summary field pairs (net, vat) per rate group in the Fa element
SUMMARY = (
    ("P_13_1", "P_14_1", Decimal(23), "S"),
    ("P_13_2", "P_14_2", Decimal(8), "S"),
    ("P_13_3", "P_14_3", Decimal(5), "S"),
    ("P_13_6_1", None, Decimal(0), "Z"),
    ("P_13_6_2", None, Decimal(0), "Z"),
    ("P_13_6_3", None, Decimal(0), "Z"),
    ("P_13_7", None, Decimal(0), "E"),
    ("P_13_8", None, Decimal(0), "O"),
    ("P_13_9", None, Decimal(0), "O"),
    ("P_13_10", None, Decimal(0), "AE"),
    ("P_13_11", None, Decimal(0), "O"),
)


def _party(p: Element | None) -> dict[str, Any]:
    if p is None:
        return {}
    ident = child(p, "DaneIdentyfikacyjne")
    addr = child(p, "Adres")
    nip = text(ident, "NIP")
    kod_ue, nr_vat_ue = text(ident, "KodUE"), text(ident, "NrVatUE")
    vat_id = f"PL{nip}" if nip else (f"{kod_ue}{nr_vat_ue}" if kod_ue and nr_vat_ue else None)
    out: dict[str, Any] = {
        "name": text(ident, "Nazwa")
        or " ".join(x for x in (text(ident, "ImiePierwsze"), text(ident, "Nazwisko")) if x),
        "vat_id": vat_id,
    }
    if addr is not None:
        out["address"] = {
            "line1": text(addr, "AdresL1"),
            "line2": text(addr, "AdresL2"),
            "country": text(addr, "KodKraju"),
        }
    email = text(p, "DaneKontaktowe", "Email")
    if email:
        out["email"] = email
    return {k: v for k, v in out.items() if v}


def parse(xml: bytes) -> ParsedInvoice:
    root = parse_root(xml)
    inv = ParsedInvoice(fields={}, syntax="fa3")
    inv.set("language", "pl")
    inv.set("supplier", _party(child(root, "Podmiot1")))
    inv.set("buyer", _party(child(root, "Podmiot2")))
    fa = child(root, "Fa")
    inv.set("document_type", RODZAJ.get(text(fa, "RodzajFaktury") or "", DocumentType.invoice).value)
    inv.set("number", text(fa, "P_2"))
    inv.set("issue_date", iso_date(text(fa, "P_1")))
    inv.set("delivery_date", iso_date(text(fa, "P_6")))
    inv.set("currency", text(fa, "KodWaluty"))
    inv.set(
        "notes",
        [n for n in (text(a, "WartoscAdnotacji") for a in children(child(fa, "Adnotacje"), "P_16")) if n],
    )
    platnosc = child(fa, "Platnosc")
    payment: dict[str, Any] = {}
    if platnosc is not None:
        payment["due_date"] = iso_date(text(platnosc, "TerminPlatnosci", "Termin"))
        payment["payment_means_code"] = text(platnosc, "FormaPlatnosci")
        iban = normalise_iban(text(platnosc, "RachunekBankowy", "NrRB"))
        if iban:
            inv.fields.setdefault("supplier", {})["iban"] = iban
    inv.set("payment", {k: v for k, v in payment.items() if v})
    po = None
    for extra in children(fa, "DodatkowyOpis"):
        key = (text(extra, "Klucz") or "").lower()
        if any(k in key for k in ("zamow", "zamów", "order", "po ")) or key == "po":
            po = text(extra, "Wartosc")
            break
    refs = {
        "purchase_order": po,
        "despatch_advice": text(fa, "WZ"),
        "preceding_invoice": text(fa, "DaneFaKorygowanej", "NrFaKorygowanej"),
        "preceding_invoice_date": iso_date(text(fa, "DaneFaKorygowanej", "DataWystFaKorygowanej")),
    }
    inv.set("references", {k: v for k, v in refs.items() if v})

    lines = []
    for w in children(fa, "FaWiersz"):
        code = (text(w, "P_12") or "").lower()
        rate, cat = P12.get(code, (None, None))
        lines.append(
            {
                "id": text(w, "NrWierszaFa"),
                "description": text(w, "P_7"),
                "unit": text(w, "P_8A"),
                "quantity": dec(text(w, "P_8B")),
                "unit_price": dec(text(w, "P_9A")),
                "net_amount": dec(text(w, "P_11")),
                "vat_rate": rate,
                "vat_category": cat,
            }
        )
    inv.set("lines", [{k: v for k, v in ln.items() if v is not None} for ln in lines])

    breakdown = []
    net_total = Decimal(0)
    vat_total = Decimal(0)
    for net_tag, vat_tag, rate, cat in SUMMARY:
        net = dec(text(fa, net_tag))
        if net is None:
            continue
        vat = dec(text(fa, vat_tag)) if vat_tag else Decimal(0)
        net_total += net
        vat_total += vat or Decimal(0)
        breakdown.append(
            {"category": cat, "rate": rate, "taxable_amount": net, "tax_amount": vat or Decimal(0)}
        )
    inv.set("vat_breakdown", breakdown)
    gross = dec(text(fa, "P_15"))
    inv.set(
        "totals",
        {
            "net_total": net_total,
            "vat_total": vat_total,
            "gross_total": gross if gross is not None else net_total + vat_total,
            "amount_due": gross,
        },
    )
    return inv
