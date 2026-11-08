"""Canonical invoice → UBL 2.1 Invoice/CreditNote (Peppol BIS Billing 3.0; XRechnung 3.0 UBL with the
KoSIT customization id). Mirrors ``app/pipeline/formats/ubl.py`` so an export parses back to the same
canonical fields (tested)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from xml.etree.ElementTree import Element, SubElement, tostring  # noqa: S405 - writer only

from app.schemas.invoice import DocumentType, Invoice, Party

NS_INVOICE = "urn:oasis:names:specification:ubl:schema:xsd:Invoice-2"
NS_CREDIT = "urn:oasis:names:specification:ubl:schema:xsd:CreditNote-2"
NS_CAC = "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2"
NS_CBC = "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2"
PEPPOL_CUSTOMIZATION = "urn:cen.eu:en16931:2017#compliant#urn:fdc:peppol.eu:2017:poacc:billing:3.0"
XRECHNUNG_CUSTOMIZATION = "urn:cen.eu:en16931:2017#compliant#urn:xeinkauf.de:kosit:xrechnung_3.0"
PROFILE_ID = "urn:fdc:peppol.eu:2017:poacc:billing:01:1.0"

TYPE_CODES = {
    DocumentType.invoice: "380",
    DocumentType.credit_note: "381",
    DocumentType.corrective_invoice: "384",
    DocumentType.self_billed_invoice: "389",
    DocumentType.proforma: "325",
}


def _cbc(parent: Element, name: str, value: object, **attrs: str) -> Element | None:
    if value is None or value == "":
        return None
    el = SubElement(parent, f"{{{NS_CBC}}}{name}", attrib=dict(attrs))
    el.text = value.isoformat() if isinstance(value, date) else str(value)
    return el


def _cac(parent: Element, name: str) -> Element:
    return SubElement(parent, f"{{{NS_CAC}}}{name}")


def _amt(parent: Element, name: str, value: Decimal | None, currency: str) -> None:
    if value is not None:
        _cbc(parent, name, f"{value.quantize(Decimal('0.01'))}", currencyID=currency)


def _party(parent: Element, p: Party, role: str) -> None:
    wrapper = _cac(parent, role)
    party = _cac(wrapper, "Party")
    if p.electronic_address:
        attrs = {"schemeID": p.electronic_address_scheme} if p.electronic_address_scheme else {}
        _cbc(party, "EndpointID", p.electronic_address, **attrs)
    if p.trading_name or p.name:
        _cbc(_cac(party, "PartyName"), "Name", p.trading_name or p.name)
    if p.address is not None:
        addr = _cac(party, "PostalAddress")
        _cbc(addr, "StreetName", p.address.line1)
        _cbc(addr, "AdditionalStreetName", p.address.line2)
        _cbc(addr, "CityName", p.address.city)
        _cbc(addr, "PostalZone", p.address.postal_code)
        _cbc(addr, "CountrySubentity", p.address.country_subdivision)
        if p.address.country:
            _cbc(_cac(addr, "Country"), "IdentificationCode", p.address.country)
    if p.vat_id:
        tax = _cac(party, "PartyTaxScheme")
        _cbc(tax, "CompanyID", p.vat_id)
        _cbc(_cac(tax, "TaxScheme"), "ID", "VAT")
    legal = _cac(party, "PartyLegalEntity")
    _cbc(legal, "RegistrationName", p.name or p.trading_name or "")
    _cbc(legal, "CompanyID", p.registration_id)
    if p.email:
        _cbc(_cac(party, "Contact"), "ElectronicMail", p.email)


def to_ubl(inv: Invoice, *, xrechnung: bool = False) -> bytes:
    is_credit = inv.document_type == DocumentType.credit_note
    ns = NS_CREDIT if is_credit else NS_INVOICE
    root = Element(f"{{{ns}}}{'CreditNote' if is_credit else 'Invoice'}")
    currency = (inv.currency or "EUR").upper()
    _cbc(root, "CustomizationID", XRECHNUNG_CUSTOMIZATION if xrechnung else PEPPOL_CUSTOMIZATION)
    _cbc(root, "ProfileID", PROFILE_ID)
    _cbc(root, "ID", inv.number or inv.id)
    _cbc(root, "IssueDate", inv.issue_date)
    if not is_credit:
        _cbc(root, "DueDate", inv.payment.due_date)
    _cbc(
        root,
        "CreditNoteTypeCode" if is_credit else "InvoiceTypeCode",
        "381" if is_credit else TYPE_CODES.get(inv.document_type, "380"),
    )
    for n in inv.notes:
        _cbc(root, "Note", n)
    _cbc(root, "DocumentCurrencyCode", currency)
    _cbc(root, "TaxCurrencyCode", inv.vat_currency)
    _cbc(root, "BuyerReference", inv.references.buyer_reference)
    if inv.period_start or inv.period_end:
        period = _cac(root, "InvoicePeriod")
        _cbc(period, "StartDate", inv.period_start)
        _cbc(period, "EndDate", inv.period_end)
    if inv.references.purchase_order or inv.references.sales_order:
        order = _cac(root, "OrderReference")
        _cbc(order, "ID", inv.references.purchase_order or "")
        _cbc(order, "SalesOrderID", inv.references.sales_order)
    if inv.references.preceding_invoice:
        ref = _cac(_cac(root, "BillingReference"), "InvoiceDocumentReference")
        _cbc(ref, "ID", inv.references.preceding_invoice)
        _cbc(ref, "IssueDate", inv.references.preceding_invoice_date)
    if inv.references.despatch_advice:
        _cbc(_cac(root, "DespatchDocumentReference"), "ID", inv.references.despatch_advice)
    if inv.references.receiving_advice:
        _cbc(_cac(root, "ReceiptDocumentReference"), "ID", inv.references.receiving_advice)
    if inv.references.contract:
        _cbc(_cac(root, "ContractDocumentReference"), "ID", inv.references.contract)
    if inv.references.project:
        _cbc(_cac(root, "ProjectReference"), "ID", inv.references.project)
    _party(root, inv.supplier, "AccountingSupplierParty")
    _party(root, inv.buyer, "AccountingCustomerParty")
    if inv.delivery_date:
        _cbc(_cac(root, "Delivery"), "ActualDeliveryDate", inv.delivery_date)
    if inv.payment.payment_means_code or inv.supplier.iban or inv.payment.payment_reference:
        pm = _cac(root, "PaymentMeans")
        _cbc(pm, "PaymentMeansCode", inv.payment.payment_means_code or ("30" if inv.supplier.iban else "1"))
        _cbc(pm, "PaymentID", inv.payment.payment_reference)
        if inv.supplier.iban:
            acct = _cac(pm, "PayeeFinancialAccount")
            _cbc(acct, "ID", inv.supplier.iban)
            if inv.supplier.bic:
                _cbc(_cac(acct, "FinancialInstitutionBranch"), "ID", inv.supplier.bic)
    if inv.payment.terms_text:
        _cbc(_cac(root, "PaymentTerms"), "Note", inv.payment.terms_text)
    tax_total = _cac(root, "TaxTotal")
    _amt(
        tax_total,
        "TaxAmount",
        inv.totals.vat_total if inv.totals.vat_total is not None else Decimal(0),
        currency,
    )
    for b in inv.vat_breakdown:
        sub = _cac(tax_total, "TaxSubtotal")
        _amt(sub, "TaxableAmount", b.taxable_amount, currency)
        _amt(sub, "TaxAmount", b.tax_amount, currency)
        cat = _cac(sub, "TaxCategory")
        _cbc(cat, "ID", b.category or ("S" if (b.rate or 0) > 0 else "Z"))
        _cbc(cat, "Percent", b.rate)
        _cbc(cat, "TaxExemptionReasonCode", b.exemption_reason_code)
        _cbc(cat, "TaxExemptionReason", b.exemption_reason)
        _cbc(_cac(cat, "TaxScheme"), "ID", "VAT")
    lmt = _cac(root, "LegalMonetaryTotal")
    t = inv.totals
    _amt(
        lmt,
        "LineExtensionAmount",
        t.line_net_total if t.line_net_total is not None else t.net_total,
        currency,
    )
    _amt(lmt, "TaxExclusiveAmount", t.net_total, currency)
    _amt(lmt, "TaxInclusiveAmount", t.gross_total, currency)
    _amt(lmt, "AllowanceTotalAmount", t.allowances_total, currency)
    _amt(lmt, "ChargeTotalAmount", t.charges_total, currency)
    _amt(lmt, "PrepaidAmount", t.prepaid, currency)
    _amt(lmt, "PayableRoundingAmount", t.rounding, currency)
    _amt(lmt, "PayableAmount", t.amount_due if t.amount_due is not None else t.gross_total, currency)
    line_tag, qty_tag = (
        ("CreditNoteLine", "CreditedQuantity") if is_credit else ("InvoiceLine", "InvoicedQuantity")
    )
    for i, ln in enumerate(inv.lines, start=1):
        line = _cac(root, line_tag)
        _cbc(line, "ID", ln.id or str(i))
        if ln.quantity is not None:
            _cbc(line, qty_tag, f"{ln.quantity:f}", unitCode=ln.unit or "C62")
        _amt(line, "LineExtensionAmount", ln.net_amount, currency)
        if ln.period_start or ln.period_end:
            lp = _cac(line, "InvoicePeriod")
            _cbc(lp, "StartDate", ln.period_start)
            _cbc(lp, "EndDate", ln.period_end)
        item = _cac(line, "Item")
        _cbc(item, "Name", ln.description or "Item")
        if ln.vat_rate is not None or ln.vat_category:
            cat = _cac(item, "ClassifiedTaxCategory")
            _cbc(cat, "ID", ln.vat_category or ("S" if (ln.vat_rate or 0) > 0 else "Z"))
            _cbc(cat, "Percent", ln.vat_rate)
            _cbc(_cac(cat, "TaxScheme"), "ID", "VAT")
        if ln.unit_price is not None:
            _cbc(_cac(line, "Price"), "PriceAmount", f"{ln.unit_price:f}", currencyID=currency)
    # ElementTree writes ns0/ns1 prefixes unless registered; register per call for readable output.
    import xml.etree.ElementTree as ET  # noqa: S405

    ET.register_namespace("", ns)
    ET.register_namespace("cac", NS_CAC)
    ET.register_namespace("cbc", NS_CBC)
    return b'<?xml version="1.0" encoding="UTF-8"?>\n' + bytes(tostring(root, encoding="utf-8"))
