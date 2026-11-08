# ViDA digital reporting (DRR)

Directive (EU) 2025/516 ("VAT in the Digital Age") makes EN 16931 e-invoicing and near-real-time
**digital reporting** mandatory for intra-EU B2B supplies from 1 July 2030, and lets member states
mandate domestic reporting earlier. The data set to report is a subset of the e-invoice (art. 263):
supplier and customer VAT ids, invoice number and date, document type, taxable amount and VAT per
rate, currency, and the correction reference.

## What InvoiceFlow does today

- **Reporting** (owner, accountant): choose a client company (or all) and a period; InvoiceFlow
  selects the approved invoices issued in the period where supplier and customer both carry an EU
  VAT id in different member states, and produces the data set as JSON (`vida-drr-v0`, one object
  per invoice with the VAT breakdown) and CSV. Restricted (DSR) documents are left out.
- Reports are kept in the archive (`reporting/<firm>/vida/<id>.json|csv`), listed with their
  totals per currency, and move from *draft* to *submitted* through a `ReportingTransport`.
- The default transport **stores** the report and marks it submitted locally. The national
  endpoints (and whether reporting runs through the e-invoicing platform, as France plans) are not
  published yet; a transport is one class with a `submit(report, payload)` method.

## Open points

- `TODO(legal)`: the implementing regulation's message format and per-country endpoints; the
  report's `format_version` is bumped when the schema is fixed.
- Whether purchase-side reporting (the customer's report) is required in the member states where
  the firm's clients sit — the builder reports both directions and the transport may filter.
