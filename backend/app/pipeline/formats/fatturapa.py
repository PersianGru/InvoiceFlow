"""FatturaPA 1.2 (Italy, SdI) → canonical fields. Rates per line and in DatiRiepilogo."""

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

TIPO_DOCUMENTO = {
    "TD01": DocumentType.invoice,
    "TD02": DocumentType.invoice,  # acconto
    "TD03": DocumentType.invoice,
    "TD04": DocumentType.credit_note,
    "TD05": DocumentType.invoice,  # nota di debito
    "TD06": DocumentType.invoice,  # parcella
    "TD16": DocumentType.invoice,
    "TD17": DocumentType.invoice,
    "TD18": DocumentType.invoice,
    "TD19": DocumentType.invoice,
    "TD24": DocumentType.invoice,
    "TD25": DocumentType.invoice,
    "TD26": DocumentType.invoice,
    "TD27": DocumentType.invoice,
}

NATURA_CATEGORY = {
    "N1": "E",
    "N2": "O",
    "N2.1": "O",
    "N2.2": "O",
    "N3": "E",
    "N3.1": "G",
    "N3.2": "E",
    "N3.3": "E",
    "N3.4": "E",
    "N3.5": "E",
    "N3.6": "E",
    "N4": "E",
    "N5": "E",
    "N6": "AE",
    "N6.1": "AE",
    "N6.2": "AE",
    "N6.3": "AE",
    "N6.4": "AE",
    "N6.5": "AE",
    "N6.6": "AE",
    "N6.7": "AE",
    "N6.8": "AE",
    "N6.9": "AE",
    "N7": "S",
}


def _party(p: Element | None) -> dict[str, Any]:
    if p is None:
        return {}
    dati = child(p, "DatiAnagrafici")
    anag = child(dati, "Anagrafica")
    name = text(anag, "Denominazione")
    if not name:
        name = " ".join(x for x in (text(anag, "Nome"), text(anag, "Cognome")) if x) or None
    vat = child(dati, "IdFiscaleIVA")
    vat_id = None
    if vat is not None and text(vat, "IdCodice"):
        vat_id = f"{text(vat, 'IdPaese') or 'IT'}{text(vat, 'IdCodice')}"
    sede = child(p, "Sede")
    out: dict[str, Any] = {"name": name, "vat_id": vat_id, "tax_id": text(dati, "CodiceFiscale")}
    if sede is not None:
        line1 = " ".join(x for x in (text(sede, "Indirizzo"), text(sede, "NumeroCivico")) if x)
        out["address"] = {
            "line1": line1 or None,
            "city": text(sede, "Comune"),
            "postal_code": text(sede, "CAP"),
            "country_subdivision": text(sede, "Provincia"),
            "country": text(sede, "Nazione"),
        }
    contatti = child(p, "Contatti")
    if contatti is not None:
        out["email"] = text(contatti, "Email")
    return {k: v for k, v in out.items() if v}


def parse(xml: bytes) -> ParsedInvoice:
    root = parse_root(xml)
    inv = ParsedInvoice(fields={}, syntax="fatturapa")
    header = child(root, "FatturaElettronicaHeader")
    body = child(root, "FatturaElettronicaBody")  # first body only (multi-body files are split upstream)
    supplier = _party(child(header, "CedentePrestatore"))
    buyer = _party(child(header, "CessionarioCommittente"))
    general = child(body, "DatiGenerali", "DatiGeneraliDocumento")
    inv.set(
        "document_type", TIPO_DOCUMENTO.get(text(general, "TipoDocumento") or "", DocumentType.invoice).value
    )
    inv.set("number", text(general, "Numero"))
    inv.set("issue_date", iso_date(text(general, "Data")))
    inv.set("currency", text(general, "Divisa"))
    inv.set("notes", [c for c in (text(c) for c in children(general, "Causale")) if c])
    inv.set("language", "it")
    dg = child(body, "DatiGenerali")
    refs = {
        "purchase_order": text(dg, "DatiOrdineAcquisto", "IdDocumento"),
        "contract": text(dg, "DatiContratto", "IdDocumento"),
        "despatch_advice": text(dg, "DatiDDT", "NumeroDDT"),
    }
    inv.set("references", {k: v for k, v in refs.items() if v})

    pay = child(body, "DatiPagamento", "DettaglioPagamento")
    if pay is not None:
        iban = normalise_iban(text(pay, "IBAN"))
        if iban:
            supplier["iban"] = iban
        if text(pay, "BIC"):
            supplier["bic"] = text(pay, "BIC")
        inv.set(
            "payment",
            {
                k: v
                for k, v in {
                    "payment_means_code": text(pay, "ModalitaPagamento"),
                    "due_date": iso_date(text(pay, "DataScadenzaPagamento")),
                }.items()
                if v
            },
        )
    inv.set("supplier", supplier)
    inv.set("buyer", buyer)

    beni = child(body, "DatiBeniServizi")
    lines = []
    for ln in children(beni, "DettaglioLinee"):
        natura = text(ln, "Natura")
        rate = dec(text(ln, "AliquotaIVA"))
        lines.append(
            {
                "id": text(ln, "NumeroLinea"),
                "description": text(ln, "Descrizione"),
                "quantity": dec(text(ln, "Quantita")),
                "unit": text(ln, "UnitaMisura"),
                "unit_price": dec(text(ln, "PrezzoUnitario")),
                "net_amount": dec(text(ln, "PrezzoTotale")),
                "vat_rate": rate,
                "vat_category": NATURA_CATEGORY.get(
                    natura or "", "S" if rate and rate > 0 else ("Z" if rate == 0 else None)
                ),
                "period_start": iso_date(text(ln, "DataInizioPeriodo")),
                "period_end": iso_date(text(ln, "DataFinePeriodo")),
            }
        )
    inv.set("lines", [{k: v for k, v in ln.items() if v is not None} for ln in lines])

    breakdown = []
    net_total = Decimal(0)
    vat_total = Decimal(0)
    for r in children(beni, "DatiRiepilogo"):
        rate = dec(text(r, "AliquotaIVA"))
        natura = text(r, "Natura")
        taxable = dec(text(r, "ImponibileImporto")) or Decimal(0)
        tax = dec(text(r, "Imposta")) or Decimal(0)
        net_total += taxable
        vat_total += tax
        breakdown.append(
            {
                "category": NATURA_CATEGORY.get(natura or "", "S" if rate and rate > 0 else "Z"),
                "rate": rate,
                "taxable_amount": taxable,
                "tax_amount": tax,
                "exemption_reason": text(r, "RiferimentoNormativo"),
                "exemption_reason_code": natura,
            }
        )
    inv.set("vat_breakdown", breakdown)
    gross = dec(text(general, "ImportoTotaleDocumento"))
    totals: dict[str, Any] = {
        "net_total": net_total,
        "vat_total": vat_total,
        "gross_total": gross if gross is not None else net_total + vat_total,
    }
    if pay is not None and dec(text(pay, "ImportoPagamento")) is not None:
        totals["amount_due"] = dec(text(pay, "ImportoPagamento"))
    inv.set("totals", totals)
    return inv
