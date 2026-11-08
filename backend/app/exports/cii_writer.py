"""Canonical invoice → UN/CEFACT CII D16B (Factur-X / ZUGFeRD EN 16931 profile; XRechnung 3.0 CII).
Mirrors ``app/pipeline/formats/cii.py`` so an export parses back to the same canonical fields."""

from __future__ import annotations

import xml.etree.ElementTree as ET  # noqa: S405 - writer only
from datetime import date
from decimal import Decimal
from xml.etree.ElementTree import Element, SubElement, tostring  # noqa: S405

from app.exports.ubl_writer import TYPE_CODES
from app.schemas.invoice import DocumentType, Invoice, Party

NS_RSM = "urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100"
NS_RAM = "urn:un:unece:uncefact:data:standard:ReusableAggregateBusinessInformationEntity:100"
NS_UDT = "urn:un:unece:uncefact:data:standard:UnqualifiedDataType:100"
NS_QDT = "urn:un:unece:uncefact:data:standard:QualifiedDataType:100"
FACTURX_EN16931 = "urn:cen.eu:en16931:2017"
XRECHNUNG_CII = "urn:cen.eu:en16931:2017#compliant#urn:xeinkauf.de:kosit:xrechnung_3.0"


def _ram(parent: Element, name: str, value: object = None, **attrs: str) -> Element:
    el = SubElement(parent, f"{{{NS_RAM}}}{name}", attrib=dict(attrs))
    if value is not None:
        el.text = str(value)
    return el


def _opt(parent: Element, name: str, value: object, **attrs: str) -> None:
    if value is not None and value != "":
        _ram(parent, name, value, **attrs)


def _dt(parent: Element, name: str, d: date | None) -> None:
    if d is None:
        return
    el = _ram(parent, name)
    s = SubElement(el, f"{{{NS_UDT}}}DateTimeString", format="102")
    s.text = d.strftime("%Y%m%d")


def _money(v: Decimal | None) -> str | None:
    return None if v is None else f"{v.quantize(Decimal('0.01'))}"


def _party(parent: Element, name: str, p: Party) -> None:
    el = _ram(parent, name)
    _opt(el, "Name", p.name or p.trading_name)
    if p.registration_id or p.trading_name:
        legal = _ram(el, "SpecifiedLegalOrganization")
        _opt(legal, "ID", p.registration_id)
        _opt(legal, "TradingBusinessName", p.trading_name)
    if p.email:
        contact = _ram(el, "DefinedTradeContact")
        _ram(_ram(contact, "EmailURIUniversalCommunication"), "URIID", p.email)
    if p.address is not None:
        addr = _ram(el, "PostalTradeAddress")
        _opt(addr, "PostcodeCode", p.address.postal_code)
        _opt(addr, "LineOne", p.address.line1)
        _opt(addr, "LineTwo", p.address.line2)
        _opt(addr, "CityName", p.address.city)
        _opt(addr, "CountryID", p.address.country)
        _opt(addr, "CountrySubDivisionName", p.address.country_subdivision)
    if p.electronic_address:
        uri = _ram(el, "URIUniversalCommunication")
        attrs = {"schemeID": p.electronic_address_scheme} if p.electronic_address_scheme else {}
        _ram(uri, "URIID", p.electronic_address, **attrs)
    if p.tax_id:
        _ram(_ram(el, "SpecifiedTaxRegistration"), "ID", p.tax_id, schemeID="FC")
    if p.vat_id:
        _ram(_ram(el, "SpecifiedTaxRegistration"), "ID", p.vat_id, schemeID="VA")


def to_cii(inv: Invoice, *, xrechnung: bool = False) -> bytes:
    currency = (inv.currency or "EUR").upper()
    root = Element(f"{{{NS_RSM}}}CrossIndustryInvoice")
    ctx = SubElement(root, f"{{{NS_RSM}}}ExchangedDocumentContext")
    _ram(
        _ram(ctx, "GuidelineSpecifiedDocumentContextParameter"),
        "ID",
        XRECHNUNG_CII if xrechnung else FACTURX_EN16931,
    )
    doc = SubElement(root, f"{{{NS_RSM}}}ExchangedDocument")
    _ram(doc, "ID", inv.number or inv.id)
    _ram(
        doc,
        "TypeCode",
        "381" if inv.document_type == DocumentType.credit_note else TYPE_CODES.get(inv.document_type, "380"),
    )
    _dt(doc, "IssueDateTime", inv.issue_date)
    for n in inv.notes:
        _ram(_ram(doc, "IncludedNote"), "Content", n)

    trx = SubElement(root, f"{{{NS_RSM}}}SupplyChainTradeTransaction")
    for i, ln in enumerate(inv.lines, start=1):
        item = _ram(trx, "IncludedSupplyChainTradeLineItem")
        _ram(_ram(item, "AssociatedDocumentLineDocument"), "LineID", ln.id or str(i))
        _ram(_ram(item, "SpecifiedTradeProduct"), "Name", ln.description or "Item")
        agr = _ram(item, "SpecifiedLineTradeAgreement")
        if ln.unit_price is not None:
            _ram(_ram(agr, "NetPriceProductTradePrice"), "ChargeAmount", f"{ln.unit_price:f}")
        dlv = _ram(item, "SpecifiedLineTradeDelivery")
        if ln.quantity is not None:
            _ram(dlv, "BilledQuantity", f"{ln.quantity:f}", unitCode=ln.unit or "C62")
        stl = _ram(item, "SpecifiedLineTradeSettlement")
        tax = _ram(stl, "ApplicableTradeTax")
        _ram(tax, "TypeCode", "VAT")
        _ram(tax, "CategoryCode", ln.vat_category or ("S" if (ln.vat_rate or 0) > 0 else "Z"))
        _opt(tax, "RateApplicablePercent", ln.vat_rate)
        if ln.period_start or ln.period_end:
            period = _ram(stl, "BillingSpecifiedPeriod")
            _dt(period, "StartDateTime", ln.period_start)
            _dt(period, "EndDateTime", ln.period_end)
        _opt(
            _ram(stl, "SpecifiedTradeSettlementLineMonetarySummation"),
            "LineTotalAmount",
            _money(ln.net_amount),
        )

    agreement = _ram(trx, "ApplicableHeaderTradeAgreement")
    _opt(agreement, "BuyerReference", inv.references.buyer_reference)
    _party(agreement, "SellerTradeParty", inv.supplier)
    _party(agreement, "BuyerTradeParty", inv.buyer)
    if inv.references.sales_order:
        _ram(_ram(agreement, "SellerOrderReferencedDocument"), "IssuerAssignedID", inv.references.sales_order)
    if inv.references.purchase_order:
        _ram(
            _ram(agreement, "BuyerOrderReferencedDocument"), "IssuerAssignedID", inv.references.purchase_order
        )
    if inv.references.contract:
        _ram(_ram(agreement, "ContractReferencedDocument"), "IssuerAssignedID", inv.references.contract)

    delivery = _ram(trx, "ApplicableHeaderTradeDelivery")
    if inv.delivery_date:
        _dt(_ram(delivery, "ActualDeliverySupplyChainEvent"), "OccurrenceDateTime", inv.delivery_date)
    if inv.references.despatch_advice:
        _ram(
            _ram(delivery, "DespatchAdviceReferencedDocument"),
            "IssuerAssignedID",
            inv.references.despatch_advice,
        )
    if inv.references.receiving_advice:
        _ram(
            _ram(delivery, "ReceivingAdviceReferencedDocument"),
            "IssuerAssignedID",
            inv.references.receiving_advice,
        )

    settlement = _ram(trx, "ApplicableHeaderTradeSettlement")
    _opt(settlement, "PaymentReference", inv.payment.payment_reference)
    _opt(settlement, "TaxCurrencyCode", inv.vat_currency)
    _ram(settlement, "InvoiceCurrencyCode", currency)
    if inv.payment.payment_means_code or inv.supplier.iban:
        pm = _ram(settlement, "SpecifiedTradeSettlementPaymentMeans")
        _ram(pm, "TypeCode", inv.payment.payment_means_code or ("30" if inv.supplier.iban else "1"))
        if inv.supplier.iban:
            _ram(_ram(pm, "PayeePartyCreditorFinancialAccount"), "IBANID", inv.supplier.iban)
            if inv.supplier.bic:
                _ram(_ram(pm, "PayeeSpecifiedCreditorFinancialInstitution"), "BICID", inv.supplier.bic)
    for b in inv.vat_breakdown:
        tax = _ram(settlement, "ApplicableTradeTax")
        _opt(tax, "CalculatedAmount", _money(b.tax_amount))
        _ram(tax, "TypeCode", "VAT")
        _opt(tax, "ExemptionReason", b.exemption_reason)
        _opt(tax, "BasisAmount", _money(b.taxable_amount))
        _ram(tax, "CategoryCode", b.category or ("S" if (b.rate or 0) > 0 else "Z"))
        _opt(tax, "ExemptionReasonCode", b.exemption_reason_code)
        _opt(tax, "RateApplicablePercent", b.rate)
    if inv.period_start or inv.period_end:
        period = _ram(settlement, "BillingSpecifiedPeriod")
        _dt(period, "StartDateTime", inv.period_start)
        _dt(period, "EndDateTime", inv.period_end)
    if inv.payment.due_date or inv.payment.terms_text:
        terms = _ram(settlement, "SpecifiedTradePaymentTerms")
        _opt(terms, "Description", inv.payment.terms_text)
        _dt(terms, "DueDateDateTime", inv.payment.due_date)
    summ = _ram(settlement, "SpecifiedTradeSettlementHeaderMonetarySummation")
    t = inv.totals
    _opt(summ, "LineTotalAmount", _money(t.line_net_total if t.line_net_total is not None else t.net_total))
    _opt(summ, "ChargeTotalAmount", _money(t.charges_total))
    _opt(summ, "AllowanceTotalAmount", _money(t.allowances_total))
    _opt(summ, "TaxBasisTotalAmount", _money(t.net_total))
    _opt(summ, "TaxTotalAmount", _money(t.vat_total), currencyID=currency)
    _opt(summ, "RoundingAmount", _money(t.rounding))
    _opt(summ, "GrandTotalAmount", _money(t.gross_total))
    _opt(summ, "TotalPrepaidAmount", _money(t.prepaid))
    _opt(summ, "DuePayableAmount", _money(t.amount_due if t.amount_due is not None else t.gross_total))
    if inv.references.preceding_invoice:
        ref = _ram(settlement, "InvoiceReferencedDocument")
        _ram(ref, "IssuerAssignedID", inv.references.preceding_invoice)
        if inv.references.preceding_invoice_date:
            f = _ram(ref, "FormattedIssueDateTime")
            s = SubElement(f, f"{{{NS_QDT}}}DateTimeString", format="102")
            s.text = inv.references.preceding_invoice_date.strftime("%Y%m%d")

    ET.register_namespace("rsm", NS_RSM)
    ET.register_namespace("ram", NS_RAM)
    ET.register_namespace("udt", NS_UDT)
    ET.register_namespace("qdt", NS_QDT)
    return b'<?xml version="1.0" encoding="UTF-8"?>\n' + bytes(tostring(root, encoding="utf-8"))
