"""UBL 2.1 Invoice / CreditNote (Peppol BIS Billing 3.0, XRechnung UBL) → canonical fields."""

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
    line_adjustments,
    local,
    normalise_iban,
    parse_root,
    skonto,
    text,
)
from app.schemas.invoice import DocumentType


def _party(p: Element | None) -> dict[str, Any]:
    if p is None:
        return {}
    addr = child(p, "PostalAddress")
    out: dict[str, Any] = {
        "name": text(p, "PartyLegalEntity", "RegistrationName") or text(p, "PartyName", "Name"),
        "trading_name": text(p, "PartyName", "Name"),
        "vat_id": clean_vat(text(p, "PartyTaxScheme", "CompanyID")),
        "registration_id": text(p, "PartyLegalEntity", "CompanyID"),
        "email": text(p, "Contact", "ElectronicMail"),
        "electronic_address": text(p, "EndpointID"),
        "electronic_address_scheme": attr(p, "EndpointID", name="schemeID"),
    }
    if addr is not None:
        out["address"] = {
            "line1": text(addr, "StreetName"),
            "line2": text(addr, "AdditionalStreetName"),
            "city": text(addr, "CityName"),
            "postal_code": text(addr, "PostalZone"),
            "country_subdivision": text(addr, "CountrySubentity"),
            "country": text(addr, "Country", "IdentificationCode"),
        }
    return {k: v for k, v in out.items() if v}


def _allowance_charge(ac: Element) -> dict[str, Any]:
    cat = child(ac, "TaxCategory")
    out = {
        "charge": (text(ac, "ChargeIndicator") or "").lower() == "true",
        "amount": dec(text(ac, "Amount")),
        "base_amount": dec(text(ac, "BaseAmount")),
        "percent": dec(text(ac, "MultiplierFactorNumeric")),
        "reason": text(ac, "AllowanceChargeReason"),
        "reason_code": text(ac, "AllowanceChargeReasonCode"),
        "vat_category": text(cat, "ID"),
        "vat_rate": dec(text(cat, "Percent")),
    }
    return {k: v for k, v in out.items() if v is not None}


def parse(xml: bytes) -> ParsedInvoice:
    root = parse_root(xml)
    is_credit = local(root.tag) == "CreditNote"
    inv = ParsedInvoice(fields={}, syntax="ubl")
    type_code = text(root, "CreditNoteTypeCode" if is_credit else "InvoiceTypeCode")
    doc_type = UNCL1001_TYPES.get(
        type_code or "", DocumentType.credit_note if is_credit else DocumentType.invoice
    )
    inv.set("document_type", doc_type.value)
    inv.set("number", text(root, "ID"))
    inv.set("issue_date", iso_date(text(root, "IssueDate")))
    inv.set("currency", text(root, "DocumentCurrencyCode"))
    inv.set("vat_currency", text(root, "TaxCurrencyCode"))
    inv.set("notes", [n for n in (text(n) for n in children(root, "Note")) if n])
    inv.set("delivery_date", iso_date(text(root, "Delivery", "ActualDeliveryDate")))
    period = child(root, "InvoicePeriod")
    inv.set("period_start", iso_date(text(period, "StartDate")))
    inv.set("period_end", iso_date(text(period, "EndDate")))

    refs: dict[str, Any] = {
        "purchase_order": text(root, "OrderReference", "ID"),
        "sales_order": text(root, "OrderReference", "SalesOrderID"),
        "contract": text(root, "ContractDocumentReference", "ID"),
        "buyer_reference": text(root, "BuyerReference"),
        "project": text(root, "ProjectReference", "ID"),
        "despatch_advice": text(root, "DespatchDocumentReference", "ID"),
        "receiving_advice": text(root, "ReceiptDocumentReference", "ID"),
    }
    billing = child(root, "BillingReference", "InvoiceDocumentReference")
    refs["preceding_invoice"] = text(billing, "ID")
    refs["preceding_invoice_date"] = iso_date(text(billing, "IssueDate"))
    inv.set("references", {k: v for k, v in refs.items() if v})

    supplier = _party(child(root, "AccountingSupplierParty", "Party"))
    buyer = _party(child(root, "AccountingCustomerParty", "Party"))
    pm = child(root, "PaymentMeans")
    payment: dict[str, Any] = {
        "payment_means_code": text(pm, "PaymentMeansCode"),
        "payment_reference": text(pm, "PaymentID"),
        "due_date": iso_date(text(root, "DueDate") or text(pm, "PaymentDueDate")),
        "terms_text": text(root, "PaymentTerms", "Note"),
        **skonto(text(root, "PaymentTerms", "Note"), iso_date(text(root, "IssueDate"))),
    }
    iban = normalise_iban(text(pm, "PayeeFinancialAccount", "ID"))
    bic = text(pm, "PayeeFinancialAccount", "FinancialInstitutionBranch", "ID")
    if iban:
        supplier["iban"] = iban
    if bic:
        supplier["bic"] = bic
    inv.set("supplier", supplier)
    inv.set("buyer", buyer)
    inv.set("payment", {k: v for k, v in payment.items() if v})
    inv.set("allowances_charges", [_allowance_charge(ac) for ac in children(root, "AllowanceCharge")])

    breakdown = []
    for tt in children(root, "TaxTotal"):
        for sub in children(tt, "TaxSubtotal"):
            cat = child(sub, "TaxCategory")
            breakdown.append(
                {
                    "category": text(cat, "ID"),
                    "rate": dec(text(cat, "Percent")),
                    "taxable_amount": dec(text(sub, "TaxableAmount")),
                    "tax_amount": dec(text(sub, "TaxAmount")),
                    "exemption_reason": text(cat, "TaxExemptionReason"),
                    "exemption_reason_code": text(cat, "TaxExemptionReasonCode"),
                }
            )
    inv.set("vat_breakdown", breakdown)
    tax_total = dec(text(root, "TaxTotal", "TaxAmount"))
    lmt = child(root, "LegalMonetaryTotal")
    totals = {
        "line_net_total": dec(text(lmt, "LineExtensionAmount")),
        "net_total": dec(text(lmt, "TaxExclusiveAmount")),
        "gross_total": dec(text(lmt, "TaxInclusiveAmount")),
        "allowances_total": dec(text(lmt, "AllowanceTotalAmount")),
        "charges_total": dec(text(lmt, "ChargeTotalAmount")),
        "prepaid": dec(text(lmt, "PrepaidAmount")),
        "rounding": dec(text(lmt, "PayableRoundingAmount")),
        "amount_due": dec(text(lmt, "PayableAmount")),
        "vat_total": tax_total,
    }
    inv.set("totals", {k: v for k, v in totals.items() if v is not None})

    lines = []
    line_tag, qty_tag = (
        ("CreditNoteLine", "CreditedQuantity") if is_credit else ("InvoiceLine", "InvoicedQuantity")
    )
    for ln in children(root, line_tag):
        item = child(ln, "Item")
        tax = child(item, "ClassifiedTaxCategory")
        lp = child(ln, "InvoicePeriod")
        lines.append(
            {
                "id": text(ln, "ID"),
                "description": text(item, "Name") or text(item, "Description"),
                "quantity": dec(text(ln, qty_tag)),
                "unit": attr(ln, qty_tag, name="unitCode"),
                "unit_price": dec(text(ln, "Price", "PriceAmount")),
                "net_amount": dec(text(ln, "LineExtensionAmount")),
                "vat_rate": dec(text(tax, "Percent")),
                "vat_category": text(tax, "ID"),
                "period_start": iso_date(text(lp, "StartDate")),
                "period_end": iso_date(text(lp, "EndDate")),
                **line_adjustments([_allowance_charge(ac) for ac in children(ln, "AllowanceCharge")]),
            }
        )
    inv.set("lines", [{k: v for k, v in ln.items() if v is not None} for ln in lines])
    return inv
