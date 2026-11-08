# Data Processing Agreement (template) — InvoiceFlow

`TODO(legal)`: this template is drafted for the operator's lawyer to finalise per market.
The technical statements below are true for the software as built; the contractual wording is a
starting point, not legal advice.

## Parties and roles

- **Controller**: the accounting firm (the "Firm") that licenses InvoiceFlow for its client companies'
  invoices. Where the firm processes on behalf of its clients, the firm is itself a processor and
  InvoiceFlow's operator is a sub-processor; the same technical measures apply.
- **Processor**: the operator of the InvoiceFlow installation (the firm itself when self-hosted; the
  hosting provider otherwise).

## Subject matter, duration, nature and purpose

Intake, extraction, validation, review and export of invoices and receipts the firm's clients send
in; processing lasts for the term of the license plus the legal retention periods of the documents
(the retention rule pack, `docs/compliance/ARCHIVE.md`). Processing continues for the legal duties
(retention, deletion, data-subject rights) after the license ends (spec §2 rule 9).

## Categories of data and data subjects

| Category | Examples | Data subjects |
|---|---|---|
| Invoice content | supplier and buyer names and addresses, VAT ids, IBAN/BIC, amounts, line items, references | suppliers' and clients' staff, sole traders |
| Channel identities | email addresses, phone numbers, Telegram ids of senders | clients' staff |
| User accounts | name, email, role, hashed password, TOTP secret (encrypted), SSO subject | firm staff, client users |
| Audit | who did what, when, from which IP (IP removed after 12 months) | firm staff, client users |
| License check | see sub-processor table (spec §8.5) | the installation, not a person |

## Instructions

The processor processes only on the controller's documented instructions: the configuration in the
product (routing policy, cloud AI opt-in, retention, channels, exports, connectors) **is** the
instruction. Cloud AI providers are used only when the firm enabled them and the license allows it.

## Confidentiality, security (art. 32)

Technical and organisational measures as built: TLS 1.2+ in transit; encryption at rest (per-firm
AES-256-GCM data keys wrapped by a platform master key; object storage with SSE); row-level
security in PostgreSQL; role-based access, 2FA, SSO with PKCE; session management; append-only
audit log; secrets only in the environment; dependency, secret and container scanning in CI;
backups (operator's duty, `deploy/README.md`).

## Sub-processors

See `SUB-PROCESSORS.md`. The controller is informed of changes with 30 days to object.

## Data-subject rights

The processor assists with the tools in `DSR.md` (export, restriction, erasure under retention law,
rectification through review corrections). Requests reach the controller; the processor answers
within the time the controller sets.

## Deletion and return

At the end of the agreement the controller exports its data (exports, DSR export) and the processor
deletes it after the retention periods the law requires, with the retention job and a written
confirmation. Backups age out on the operator's schedule.

## Audits

The controller may audit once a year with reasonable notice; the operator provides the compliance
folder (`docs/compliance`), the threat model and CI evidence.

## Records of processing

See `RECORDS-OF-PROCESSING.md`.
