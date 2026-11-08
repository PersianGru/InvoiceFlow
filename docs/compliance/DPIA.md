# Data protection impact assessment (art. 35 GDPR) — InvoiceFlow

`TODO(legal)`: review with the operator's DPO; the technical facts are current for the build.

## 1. Description of the processing

InvoiceFlow receives invoices and receipts through uploads, the API, email, Telegram and WhatsApp,
extracts structured data (structured e-invoice parsing first; OCR and local language models;
optionally a cloud model), validates it, lets accountants review and correct it, exports it to
accounting software and keeps the originals for the legal retention period.

## 2. Necessity and proportionality

- Purpose limitation: bookkeeping for the firm's clients; no profiling, no decisions with legal
  effect on individuals (the output is reviewed by accountants; AI-extracted values are marked until
  a human confirms them, EU AI Act art. 50 transparency: the bots state they are automated).
- Data minimisation: only invoice content and the identities needed to route a message are stored;
  the hub receives no business data; IPs are removed from the audit log after 12 months; channel
  texts are purged after 12 months.
- Storage limitation: retention from country rule packs; deletion job; erasure requests honoured as
  far as tax law allows (`DSR.md`).
- Default settings are the protective ones: local-only AI routing, cloud AI off, auto-approval off,
  unknown senders quarantined, invited-only SSO.

## 3. Risks to data subjects and measures

| Risk | Likelihood / severity | Measures | Residual |
|---|---|---|---|
| Unauthorised access to invoices (external attacker) | low / high | TLS, encryption at rest with per-firm keys, argon2id, TOTP, SSO with PKCE, lockout and rate limits, session hardening, dependency/secret/container scanning, threat model | low |
| Cross-tenant disclosure (bug) | low / high | PostgreSQL RLS as a second line behind application filters; tests prove isolation | low |
| Disclosure to a cloud AI provider without basis | low / medium | cloud routing only with firm opt-in **and** license entitlement; every call logged; zero-data-retention terms (`TODO(legal)`) | low |
| Wrong data in the books (extraction error) | medium / medium | per-field confidence, validation checks, human review queue, audit of corrections, versioned extractions | low |
| Excessive retention | low / medium | rule packs per country, end-of-year rule, deletion job, erasure under retention, restriction of processing | low |
| Messaging platforms (Telegram/Meta) see message metadata | medium / low | task-specific bots, no free-form chat, minimal replies, mappings per client, 12-month purge; firms choose whether to enable a channel | accepted |
| Insider misuse by firm staff | low / medium | roles (junior review-only, client roles scoped to their company), full audit trail, append-only log | low |
| Prompt injection through documents | medium / low | models return schema-constrained data only; no tool calls or actions from document text | low |
| Loss of availability of the archive | low / high | operator backups with tested restore (`deploy/README.md`), hash verification endpoint, object lock | low |

## 4. Consultation and sign-off

DPO review: `TODO(legal)`. Data subjects' views: not required (no high-risk novel processing after
measures). Revisit at every phase and whenever a new sub-processor or channel is added.
