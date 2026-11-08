"""UN/CEFACT CII D16B (XRechnung CII, Factur-X, ZUGFeRD 2.x) → canonical fields."""

from __future__ import annotations

from typing import Any
from xml.etree.ElementTree import Element  # noqa: S405

from app.pipeline.formats.common import (
    UNCL1001_TYPES,
    ParsedInvoice,
    attr,
    child,
    children,
    clean_vat,
    dec,
    iso_date,
    normalise_iban,
    parse_root,
    text,
)
from app.schemas.invoice import DocumentType


def _dt(el: Element | None, *path: str) -> str | None:
    target = child(el, *path) if path else el
    return text(target, "DateTimeString") if target is not None else None


def _party(p: Element | None) -> dict[str, Any]:
    if p is None:
        return {}
    out: dict[str, Any] = {
        "name": text(p, "Name"),
        "email": text(p, "DefinedTradeContact", "EmailURIUniversalCommunication", "URIID"),
    }
    for reg in children(p, "SpecifiedTaxRegistration"):
        scheme = attr(reg, "ID", name="schemeID")
        value = text(reg, "ID")
        if scheme == "VA":
            out["vat_id"] = clean_vat(value)
        elif scheme == "FC":
            out["tax_id"] = value
    legal = child(p, "SpecifiedLegalOrganization")
    if legal is not None:
        out["registration_id"] = text(legal, "ID")
        out["trading_name"] = text(legal, "TradingBusinessName")
    uri = child(p, "URIUniversalCommunication")
    if uri is not None:
        out["electronic_address"] = text(uri, "URIID")
        out["electronic_address_scheme"] = attr(uri, "URIID", name="schemeID")
    addr = child(p, "PostalTradeAddress")
    if addr is not None:
        out["address"] = {
            "line1": text(addr, "LineOne"),
            "line2": text(addr, "LineTwo"),
            "city": text(addr, "CityName"),
            "postal_code": text(addr, "PostcodeCode"),
            "country_subdivision": text(addr, "CountrySubDivisionName"),
            "country": text(addr, "CountryID"),
        }
    return {k: v for k, v in out.items() if v}


def parse(xml: bytes) -> ParsedInvoice:
    root = parse_root(xml)
    inv = ParsedInvoice(fields={}, syntax="cii")
    doc = child(root, "ExchangedDocument")
    inv.set("document_type", UNCL1001_TYPES.get(text(doc, "TypeCode") or "", DocumentType.invoice).value)
    inv.set("number", text(doc, "ID"))
    inv.set("issue_date", iso_date(_dt(doc, "IssueDateTime")))
    inv.set("notes", [n for n in (text(n, "Content") for n in children(doc, "IncludedNote")) if n])

    trx = child(root, "SupplyChainTradeTransaction")
    agreement = child(trx, "ApplicableHeaderTradeAgreement")
    delivery = child(trx, "ApplicableHeaderTradeDelivery")
    settlement = child(trx, "ApplicableHeaderTradeSettlement")

    refs = {
        "buyer_reference": text(agreement, "BuyerReference"),
        "purchase_order": text(agreement, "BuyerOrderReferencedDocument", "IssuerAssignedID"),
        "sales_order": text(agreement, "SellerOrderReferencedDocument", "IssuerAssignedID"),
        "contract": text(agreement, "ContractReferencedDocument", "IssuerAssignedID"),
        "despatch_advice": text(delivery, "DespatchAdviceReferencedDocument", "IssuerAssignedID"),
        "receiving_advice": text(delivery, "ReceivingAdviceReferencedDocument", "IssuerAssignedID"),
        "preceding_invoice": text(settlement, "InvoiceReferencedDocument", "IssuerAssignedID"),
        "preceding_invoice_date": iso_date(
            _dt(settlement, "InvoiceReferencedDocument", "FormattedIssueDateTime")
        ),
    }
    inv.set("references", {k: v for k, v in refs.items() if v})
    inv.set("delivery_date", iso_date(_dt(delivery, "ActualDeliverySupplyChainEvent", "OccurrenceDateTime")))

    supplier = _party(child(agreement, "SellerTradeParty"))
    buyer = _party(child(agreement, "BuyerTradeParty"))
    pm = child(settlement, "SpecifiedTradeSettlementPaymentMeans")
    iban = normalise_iban(text(pm, "PayeePartyCreditorFinancialAccount", "IBANID"))
    bic = text(pm, "PayeeSpecifiedCreditorFinancialInstitution", "BICID")
    if iban:
        supplier["iban"] = iban
    if bic:
        supplier["bic"] = bic
    inv.set("supplier", supplier)
    inv.set("buyer", buyer)
    inv.set("currency", text(settlement, "InvoiceCurrencyCode"))
    inv.set("vat_currency", text(settlement, "TaxCurrencyCode"))
    terms = child(settlement, "SpecifiedTradePaymentTerms")
    payment = {
        "payment_means_code": text(pm, "TypeCode"),
        "payment_reference": text(settlement, "PaymentReference"),
        "due_date": iso_date(_dt(terms, "DueDateDateTime")),
        "terms_text": text(terms, "Description"),
    }
    inv.set("payment", {k: v for k, v in payment.items() if v})
    header_period = child(settlement, "BillingSpecifiedPeriod")
    inv.set("period_start", iso_date(_dt(header_period, "StartDateTime")))
    inv.set("period_end", iso_date(_dt(header_period, "EndDateTime")))

    breakdown = []
    for tax in children(settlement, "ApplicableTradeTax"):
        breakdown.append(
            {
                "category": text(tax, "CategoryCode"),
                "rate": dec(text(tax, "RateApplicablePercent")),
                "taxable_amount": dec(text(tax, "BasisAmount")),
                "tax_amount": dec(text(tax, "CalculatedAmount")),
                "exemption_reason": text(tax, "ExemptionReason"),
                "exemption_reason_code": text(tax, "ExemptionReasonCode"),
            }
        )
    inv.set("vat_breakdown", breakdown)
    summ = child(settlement, "SpecifiedTradeSettlementHeaderMonetarySummation")
    totals = {
        "line_net_total": dec(text(summ, "LineTotalAmount")),
        "charges_total": dec(text(summ, "ChargeTotalAmount")),
        "allowances_total": dec(text(summ, "AllowanceTotalAmount")),
        "net_total": dec(text(summ, "TaxBasisTotalAmount")),
        "vat_total": dec(text(summ, "TaxTotalAmount")),
        "gross_total": dec(text(summ, "GrandTotalAmount")),
        "prepaid": dec(text(summ, "TotalPrepaidAmount")),
        "rounding": dec(text(summ, "RoundingAmount")),
        "amount_due": dec(text(summ, "DuePayableAmount")),
    }
    inv.set("totals", {k: v for k, v in totals.items() if v is not None})

    lines = []
    for item in children(trx, "IncludedSupplyChainTradeLineItem"):
        product = child(item, "SpecifiedTradeProduct")
        agr = child(item, "SpecifiedLineTradeAgreement")
        dlv = child(item, "SpecifiedLineTradeDelivery")
        stl = child(item, "SpecifiedLineTradeSettlement")
        line_tax = child(stl, "ApplicableTradeTax")
        line_period = child(stl, "BillingSpecifiedPeriod")
        lines.append(
            {
                "id": text(item, "AssociatedDocumentLineDocument", "LineID"),
                "description": text(product, "Name") or text(product, "Description"),
                "quantity": dec(text(dlv, "BilledQuantity")),
                "unit": attr(dlv, "BilledQuantity", name="unitCode"),
                "unit_price": dec(text(agr, "NetPriceProductTradePrice", "ChargeAmount")),
                "net_amount": dec(
                    text(stl, "SpecifiedTradeSettlementLineMonetarySummation", "LineTotalAmount")
                ),
                "vat_rate": dec(text(line_tax, "RateApplicablePercent")),
                "vat_category": text(line_tax, "CategoryCode"),
                "period_start": iso_date(_dt(line_period, "StartDateTime")),
                "period_end": iso_date(_dt(line_period, "EndDateTime")),
            }
        )
    inv.set("lines", [{k: v for k, v in ln.items() if v is not None} for ln in lines])
    return inv
