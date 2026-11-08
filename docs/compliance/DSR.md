# Handling data-subject requests

The firm (controller) receives the request; InvoiceFlow gives the firm owner and accountants the
tools (role `dsr.manage`). None of them is behind the license gate: they work in the unlicensed
version too (spec §2 rule 9).

| Right | How | Where |
|---|---|---|
| Access / portability | `POST /dsr/export` with a `client_company_id` or a `document_id` → one ZIP: `documents.json` (metadata + extracted records), `originals/`, `comments/`, `audit.json` | API; audited as `dsr.export` |
| Rectification | Correct fields on the review screen (`POST /documents/{id}/review` with `corrections`); every correction is a new extraction version with the author in the audit trail | dashboard |
| Restriction of processing | `POST /dsr/restrict` → the pipeline skips the documents (timeline step `restricted`), exports and bulk actions leave them out; they stay readable. `POST /dsr/unrestrict` lifts it (not while an erasure request is open) | API; audited |
| Erasure | `POST /dsr/erase` → documents past their retention are deleted now (original removed, record cleared, tombstone kept); documents still under tax retention are restricted and marked `erasure_requested_at`; the retention job deletes them when the period ends. The answer tells the firm what was deleted and what is scheduled | API; audited `dsr.erase`, later `document.retention_delete` |
| Objection | Restriction plus the firm's own assessment; invoices are kept on a legal obligation (tax law), which the firm explains to the subject | — |

Supporting facts for the answer to the subject:

- Retention period and rule applied: `retention_until` and `retention_rule` on each document
  (`GET /documents/{id}`), e.g. `DE:10y:2026.11`.
- Integrity of what is kept: `GET /dsr/verify/{id}` recomputes the SHA-256 of the archived original.
- Who saw or changed what: the audit log (`GET /audit`), IPs removed after 12 months.
- Channel identities: Settings → Channels (sender mappings can be deleted; the 12-month purge
  removes message texts).

User accounts of a client user (name, email) are deleted by the firm owner by deactivating the
user; the audit rows keep the user id, not the name.
