# Records of processing activities (art. 30 GDPR) — template for the operator

`TODO(legal)`: fill in the operator's name, DPO and the lawful basis per market.

| # | Activity | Purpose | Data subjects | Categories | Recipients | Retention | Measures |
|---|---|---|---|---|---|---|---|
| 1 | Invoice intake and processing | Bookkeeping for the firm's clients (contract, legal obligation) | suppliers' and clients' staff | invoice content, channel identities | the firm; sub-processors per `SUB-PROCESSORS.md` | per the retention rule pack (5–10 years by country, end-of-year rule) | encryption, RLS, audit, review by humans |
| 2 | User accounts and sessions | Access control (contract) | firm staff, client users | name, email, role, credentials (hashed/encrypted), sessions | none | account lifetime; sessions 12 h | argon2id, TOTP, SSO/PKCE, lockout |
| 3 | Audit log | Security and accountability (legal obligation, legitimate interest) | users | actions, targets, IP | none | rows kept; IP removed after 12 months | append-only table |
| 4 | Channel messages | Receipts, questions and answers (contract) | clients' staff | short message texts, identities | Telegram / Meta / mail provider | 12 months | purge job |
| 5 | License check | Licensing (contract with 21CEL) | the installation | spec §8.5 data | 21CEL License Hub | per the hub's policy | signed tokens, HTTPS |
| 6 | Exports and connectors | Transfer to accounting software (contract) | as #1 | exported records | the firm's accounting software | exports kept until deleted by the firm | per-firm storage, audit |
| 7 | AI extraction (local / cloud) | Field extraction (contract) | as #1 | document text | local model; cloud provider only with opt-in + entitlement | call log 12 months | `ai_call_log`, routing policy |
