"""Canonical invoice schema v1, aligned with EN 16931 (the EU semantic model) so it converts
losslessly to UBL 2.1 / CII / XRechnung / Factur-X later.

Business-term ids (BT-*) are noted where the field maps 1:1. Every extracted field can carry
a confidence and a source location (page + bounding box) and is marked AI-generated until a
human confirms it (EU AI Act Art. 50 transparency).
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

INVOICE_SCHEMA_V1 = "https://invoiceflow.eu/schemas/invoice/v1"


class DocumentType(StrEnum):
    invoice = "invoice"  # BT-3 380
    credit_note = "credit_note"  # 381
    corrective_invoice = "corrective_invoice"  # 384
    proforma = "proforma"  # 325
    receipt = "receipt"
    reminder = "reminder"
    self_billed_invoice = "self_billed_invoice"  # 389
    non_invoice = "non_invoice"


class Direction(StrEnum):
    purchase = "purchase"
    sales = "sales"


class ValueSource(StrEnum):
    structured = "structured"  # from an e-invoice XML (100% accurate)
    ocr = "ocr"
    llm_local = "llm_local"
    llm_cloud = "llm_cloud"
    supplier_rule = "supplier_rule"
    human = "human"


class SourceLocation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    page: int = Field(ge=1)
    x0: float = Field(ge=0, le=1, description="Relative bounding box (0..1) on the page")
    y0: float = Field(ge=0, le=1)
    x1: float = Field(ge=0, le=1)
    y1: float = Field(ge=0, le=1)


class FieldMeta(BaseModel):
    """Provenance of one extracted value."""

    model_config = ConfigDict(extra="forbid")
    confidence: float = Field(ge=0, le=1)
    source: ValueSource
    location: SourceLocation | None = None
    ai_generated: bool = True
    confirmed_by: str | None = Field(default=None, description="User id of the human who confirmed the value")
    confirmed_at: datetime | None = None
    model: str | None = Field(default=None, description="Model name/version that produced the value")
    prompt_version: str | None = None
    agreed_by: int | None = Field(default=None, description="How many providers agreed on the value")


class Address(BaseModel):
    model_config = ConfigDict(extra="forbid")
    line1: str | None = None  # BT-35
    line2: str | None = None  # BT-36
    city: str | None = None  # BT-37
    postal_code: str | None = None  # BT-38
    country_subdivision: str | None = None  # BT-39
    country: str | None = Field(
        default=None, min_length=2, max_length=2, description="ISO 3166-1 alpha-2, BT-40"
    )


class Party(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = None  # BT-27 / BT-44
    trading_name: str | None = None  # BT-28 / BT-45
    vat_id: str | None = None  # BT-31 / BT-48
    tax_id: str | None = None  # BT-32 / BT-47
    registration_id: str | None = None  # BT-30 / BT-47
    address: Address | None = None
    email: str | None = None  # BT-43 / BT-58
    iban: str | None = None  # BT-84
    bic: str | None = None  # BT-86
    electronic_address: str | None = None  # BT-34 / BT-49 (Peppol id)
    electronic_address_scheme: str | None = None


class LineItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str | None = None  # BT-126
    description: str | None = None  # BT-153 / BT-154
    quantity: Decimal | None = None  # BT-129
    unit: str | None = Field(default=None, description="UN/ECE Rec 20 code, BT-130")
    unit_price: Decimal | None = None  # BT-146
    allowance_amount: Decimal | None = Field(
        default=None, description="Line discount, BT-136; net_amount is the amount after it"
    )
    allowance_percent: Decimal | None = None  # BT-138
    allowance_reason: str | None = None  # BT-139
    charge_amount: Decimal | None = Field(
        default=None, description="Line charge, BT-141; net_amount includes it"
    )
    charge_reason: str | None = None  # BT-144
    net_amount: Decimal | None = Field(
        default=None, description="BT-131: quantity × unit price − discount + charge"
    )
    vat_rate: Decimal | None = None  # BT-152
    vat_category: str | None = Field(
        default=None, description="UNCL5305 code: S, Z, E, AE, K, G, O, L, M; BT-151"
    )
    gl_account: str | None = None
    cost_category: str | None = None
    period_start: date | None = None  # BT-134
    period_end: date | None = None  # BT-135
    meta: dict[str, FieldMeta] = Field(default_factory=dict, description="Per-field provenance by field name")


class AllowanceCharge(BaseModel):
    """A discount (BG-20) or charge (BG-21) on the whole document, e.g. 3 % off the total or freight.
    Amounts are positive; ``charge`` tells which side it is on."""

    model_config = ConfigDict(extra="forbid")
    charge: bool = False
    amount: Decimal | None = None  # BT-92 / BT-99
    base_amount: Decimal | None = None  # BT-93 / BT-100
    percent: Decimal | None = None  # BT-94 / BT-101
    reason: str | None = None  # BT-97 / BT-104
    reason_code: str | None = None  # BT-98 / BT-105 (UNCL5189 / UNCL7161)
    vat_category: str | None = None  # BT-95 / BT-102
    vat_rate: Decimal | None = None  # BT-96 / BT-103


class VatBreakdown(BaseModel):
    model_config = ConfigDict(extra="forbid")
    category: str | None = None  # BT-118
    rate: Decimal | None = None  # BT-119
    taxable_amount: Decimal | None = None  # BT-116
    tax_amount: Decimal | None = None  # BT-117
    exemption_reason: str | None = None  # BT-120
    exemption_reason_code: str | None = None  # BT-121


class Totals(BaseModel):
    model_config = ConfigDict(extra="forbid")
    line_net_total: Decimal | None = None  # BT-106
    allowances_total: Decimal | None = None  # BT-107
    charges_total: Decimal | None = None  # BT-108
    net_total: Decimal | None = None  # BT-109
    vat_total: Decimal | None = None  # BT-110
    gross_total: Decimal | None = None  # BT-112
    prepaid: Decimal | None = None  # BT-113
    rounding: Decimal | None = None  # BT-114
    amount_due: Decimal | None = None  # BT-115


class PaymentTerms(BaseModel):
    model_config = ConfigDict(extra="forbid")
    due_date: date | None = None  # BT-9
    terms_text: str | None = None  # BT-20
    payment_means_code: str | None = None  # BT-81 (UNCL4461)
    payment_reference: str | None = None  # BT-83
    # early-payment discount ("2 % if paid within 10 days", German Skonto): a payment term, never
    # subtracted from the totals
    discount_percent: Decimal | None = None
    discount_until: date | None = None


class References(BaseModel):
    model_config = ConfigDict(extra="forbid")
    purchase_order: str | None = None  # BT-13
    contract: str | None = None  # BT-12
    sales_order: str | None = None  # BT-14
    buyer_reference: str | None = None  # BT-10
    preceding_invoice: str | None = None  # BT-25
    preceding_invoice_date: date | None = None  # BT-26
    project: str | None = None  # BT-11
    despatch_advice: str | None = None  # BT-16
    receiving_advice: str | None = None  # BT-15


class ValidationLevel(StrEnum):
    passed = "pass"
    warning = "warning"
    error = "error"


class ValidationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    check: str = Field(
        description="Stable check id, e.g. arithmetic.line_totals, vat.rate_plausible, iban.checksum"
    )
    level: ValidationLevel
    reason: str = Field(description="Human-readable explanation shown in the UI")
    field: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class ReviewStatus(StrEnum):
    pending = "pending"
    needs_review = "needs_review"
    needs_info = "needs_info"
    approved = "approved"
    auto_approved = "auto_approved"
    rejected = "rejected"


class AuditEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    at: datetime
    actor: str = Field(description="User id or 'system'/'ai'")
    action: str
    field: str | None = None
    before: Any = None
    after: Any = None


class SourceInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")
    channel: str = Field(description="upload | email | whatsapp | telegram | api | peppol")
    sender_identity: str | None = None
    received_at: datetime
    original_file_id: str
    original_file_sha256: str = Field(min_length=64, max_length=64)
    original_filename: str | None = None
    mime_type: str | None = None
    page_count: int | None = None
    structured_format: str | None = Field(
        default=None, description="xrechnung | zugferd | facturx | ubl | cii | fa3 | fatturapa"
    )


class Invoice(BaseModel):
    """Canonical invoice document, schema v1."""

    model_config = ConfigDict(extra="forbid", json_schema_extra={"$id": INVOICE_SCHEMA_V1})

    schema_version: str = Field(default="1", pattern=r"^1$")
    id: str
    firm_id: str
    client_company_id: str | None = None
    legal_entity_id: str | None = None
    document_type: DocumentType  # BT-3
    direction: Direction | None = None
    number: str | None = None  # BT-1
    issue_date: date | None = None  # BT-2
    delivery_date: date | None = None  # BT-72
    period_start: date | None = None  # BT-73
    period_end: date | None = None  # BT-74
    currency: str | None = Field(default=None, min_length=3, max_length=3, description="ISO 4217, BT-5")
    vat_currency: str | None = Field(default=None, min_length=3, max_length=3)  # BT-6
    exchange_rate: Decimal | None = Field(
        default=None, description="ECB reference rate for the issue date to the firm's currency"
    )
    converted_currency: str | None = Field(default=None, min_length=3, max_length=3)
    converted_gross_total: Decimal | None = None
    language: str | None = Field(default=None, description="BCP 47")
    supplier: Party = Field(default_factory=Party)
    buyer: Party = Field(default_factory=Party)
    lines: list[LineItem] = Field(default_factory=list)
    allowances_charges: list[AllowanceCharge] = Field(default_factory=list)  # BG-20 / BG-21
    vat_breakdown: list[VatBreakdown] = Field(default_factory=list)
    totals: Totals = Field(default_factory=Totals)
    payment: PaymentTerms = Field(default_factory=PaymentTerms)
    references: References = Field(default_factory=References)
    notes: list[str] = Field(default_factory=list)  # BT-22
    reverse_charge: bool | None = None
    intra_community: bool | None = None
    export: bool | None = None
    suggested_gl_account: str | None = None
    suggested_cost_category: str | None = None
    suggested_vat_code: str | None = None
    source: SourceInfo
    meta: dict[str, FieldMeta] = Field(
        default_factory=dict, description="Per-field provenance for header fields"
    )
    validation: list[ValidationResult] = Field(default_factory=list)
    review_status: ReviewStatus = ReviewStatus.pending
    duplicate_of: str | None = None
    audit: list[AuditEntry] = Field(default_factory=list)


def invoice_json_schema() -> dict[str, Any]:
    schema = Invoice.model_json_schema(mode="serialization")
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$id"] = INVOICE_SCHEMA_V1
    schema["title"] = "InvoiceFlow canonical invoice v1 (EN 16931-aligned)"
    return schema
