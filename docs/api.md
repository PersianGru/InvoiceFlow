# InvoiceFlow public API

Base URL: `https://<your-api-host>/api/v1`. OpenAPI: `GET /api/openapi.json`
(a copy is committed at `docs/api/openapi.json`; interactive docs at `/api/docs` outside production).

Everything below is gated by the firm's 21CEL license: the public API and webhooks are part of the
`api` entitlement, new exports of the core tool. When the firm's subscription is not active every
API-key request answers:

```json
HTTP 402
{"error": "license_required", "message": "Your accountant's InvoiceFlow subscription is not active. Existing documents stay available.", "state": "expired", "feature": "core"}
```

Nothing is deleted or hidden: the dashboard keeps reading, downloading and exporting what exists.

## Authentication

| Method | How | Notes |
|---|---|---|
| API key | `x-api-key: ivfk_<id>_<secret>` or `Authorization: Bearer ivfk_…` | Created by the firm owner under Settings → API keys; shown once. |
| OAuth2 client credentials | `POST /oauth/token` with `grant_type=client_credentials`, `client_id=ivfk_<id>`, `client_secret=<secret>` (form, JSON or HTTP Basic) → `{"access_token": "ivfat_…", "token_type": "Bearer", "expires_in": 3600, "scope": "…"}` | Tokens are stateless and signed; revoking the key invalidates its tokens at once. Optional `scope` narrows the token. |

Scopes: `documents:read`, `documents:write` (upload, review, approve), `exports:read`,
`exports:write` (exports and templates), `webhooks:manage`. A key never manages users, clients,
the license or other keys. Missing scope → `403 {"error": "insufficient_scope", "scope": "…"}`.

## Conventions

- Errors are always `{"error": "<code>", "message": "<human text>", ...}`.
- Lists are paginated with `offset` and `limit` (`GET /documents?offset=0&limit=50` returns
  `{items, total, offset, limit}`, limit ≤ 500).
- **Rate limit** per key (default 120 requests/minute, set per key): headers `x-ratelimit-limit`,
  `x-ratelimit-remaining`, `x-ratelimit-reset`; over the limit → `429 rate_limited` + `retry-after`.
- **Idempotency**: send `Idempotency-Key: <your unique id>` (≤ 200 chars) on `POST /documents` and
  `POST /exports`. A repeat with the same key and the same request returns the first response with
  `idempotent-replayed: true`; the same key with a different request is `422 idempotency_mismatch`.
  Keys are kept for 24 hours per firm.
- Dates are ISO 8601; money is a decimal string (`"643.29"`), currency ISO 4217.

## Documents

| Call | Purpose |
|---|---|
| `POST /documents` (multipart `file`, optional `?client_company_id=`) | Intake. PDF, images, e-invoice XML, or a ZIP of those. Answers `201` with the document (and `batch` for ZIPs); `402` when unlicensed; `422` on rejected files (`malware`, `unsupported_type`, …). Processing is asynchronous: watch status or subscribe to webhooks. |
| `GET /documents` | Filters: `q`, `status` (csv), `channel`, `client_company_id`, `supplier`, `assigned_to`, `date_from`, `date_to`, `amount_min`, `amount_max`, `sort`. |
| `GET /documents/{id}` · `/download` · `/invoice` · `/timeline` · `/comments` | Summary, original file, canonical invoice JSON (schema `GET /schemas/invoice/v1`), processing timeline, comments. |
| `POST /documents/{id}/review` `{action: approve|reject|needs_info|save, corrections?: {path: value}}` | Human decision; corrections create a new extraction version and teach the supplier profile. |
| `GET /documents/{id}/export?format=` | One document as `json`, `csv`, `xlsx`, `ubl`, `xrechnung`, `cii`, `facturx`, `pdf_summary`, `datev`. |

## Exports

`POST /exports` → `201 {id, format, file_name, media_type, size_bytes, sha256, document_count,
download_url}`; `GET /exports`, `GET /exports/{id}`, `GET /exports/{id}/download`.

```json
{"format": "xlsx", "template_id": "…", "document_ids": ["…"]}
{"format": "datev", "filters": {"status": "approved", "client_company_id": "…", "date_from": "2026-10-01", "date_to": "2026-10-31"}, "options": {"datev_consultant_number": "12345", "datev_client_number": "67890"}}
```

| Format | File | Notes |
|---|---|---|
| `json` | `.json` | `{schema, exported_at, count, invoices: [canonical invoice v1…]}` |
| `csv` / `xlsx` | `.csv` / `.xlsx` | Columns from the template (or the default set). CSV options: `delimiter`, `decimal_separator` (`.`/`,`), `date_format` (strftime), `bom` (default on), `per_line` (one row per line item). |
| `ubl` / `xrechnung` | `.xml` (zip for several) | UBL 2.1, Peppol BIS Billing 3.0 / XRechnung 3.0 customization id. |
| `cii` | `.xml` | UN/CEFACT CII D16B, EN 16931 profile (Factur-X/ZUGFeRD XML). |
| `facturx` | `.pdf` | The original PDF (or a generated summary page) with `factur-x.xml` attached (`AFRelationship /Data`, Factur-X XMP). Not validated as PDF/A-3. |
| `pdf_summary` | `.pdf` | One-page summary: header, parties, totals, lines, checks. |
| `datev` | `.csv` | DATEV Buchungsstapel EXTF 700 (Windows-1252, `;`). Options: `datev_consultant_number`, `datev_client_number`, `fiscal_year_start`, `account_length`, `payables_account`, `receivables_account`, `default_expense_account`, `default_revenue_account`, `bu_keys`. |

Receivers that require certified XRechnung output should run the KoSIT validator on our files;
the writers are tested by parsing every export back with InvoiceFlow's own parsers.

**Templates** (`GET/POST/PATCH/DELETE /export-templates`): `{name, format, client_company_id?,
columns: [{key, header?}], options}`. The column registry: `GET /exports/columns`.

## Webhooks

Settings → Webhooks (or `POST /webhooks` with the `webhooks:manage` scope):
`{url (https://), description, events: [...]}` → the endpoint plus its `secret` (`ivfwh_…`, shown
once; `POST /webhooks/{id}/rotate-secret` issues a new one).

Events: `document.received`, `invoice.extracted`, `invoice.needs_review`, `invoice.approved`,
`export.completed`, and `ping` (from `POST /webhooks/{id}/test`).

Delivery: `POST <url>` with `content-type: application/json` and

```
x-invoiceflow-event: invoice.approved
x-invoiceflow-delivery: <delivery id>
x-invoiceflow-signature: t=<unix seconds>,v1=<hex HMAC-SHA256>
```

The signature is HMAC-SHA256 with the endpoint secret over `"<t>.<raw body>"` — the same scheme the
21CEL hub uses (spec §14), so one verifier serves both:

```python
import hmac, hashlib, time

def verify(secret: str, header: str, body: bytes, max_age: int = 300) -> bool:
    parts = dict(p.split("=", 1) for p in header.split(","))
    t, v1 = int(parts["t"]), parts["v1"]
    if abs(time.time() - t) > max_age:
        return False
    want = hmac.new(secret.encode(), f"{t}.".encode() + body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(want, v1)
```

Payload:

```json
{"id": "<event id>", "type": "invoice.approved", "created_at": "2026-11-04T10:00:00+00:00", "firm_id": "…",
 "data": {"document": {...document summary...}, "invoice": {...canonical invoice v1...}, "version": 2}}
```

Answer `2xx` within 10 seconds. Anything else is retried after 1 min, 5 min, 30 min, 2 h and 6 h,
then marked `failed` (retry from the UI or `POST /webhooks/{id}/deliveries/{did}/retry`). Deliveries
are at-least-once: de-duplicate on `id`. After 50 consecutive failures the endpoint is disabled until
re-enabled. An unlicensed firm emits nothing; pending deliveries wait and resume when the license is
back.

## n8n and other automation

Use the HTTP Request node with the API key header, or the Webhook node as a receiver with the
verification above (set the node to return the raw body for the HMAC). Example workflows are in
`docs/integrations/n8n/`.

## Bank reconciliation and reporting

Scopes `bank:read` / `bank:write` for API keys; firm users need `bank.view` / `bank.manage`.

| Call | Purpose |
|---|---|
| `GET /bank/summary`, `GET /bank/accounts`, `POST /bank/accounts`, `PATCH /bank/accounts/{id}` | accounts (created on first import or by hand) |
| `POST /bank/statements` (multipart `file`, optional `client_company_id`) | import CAMT.053 / MT940; answers new / duplicate / matched / suggested counts; `409 duplicate` for a file seen before, `422 unsupported_format` |
| `GET /bank/transactions?status=&account_id=` | transactions with their suggested / confirmed matches |
| `GET /bank/matches?status=suggested|confirmed|rejected`, `POST /bank/matches/{id}/confirm|reject` | decisions |
| `POST /bank/transactions/{id}/match` `{document_id, amount?}`, `…/ignore`, `…/rematch`, `POST /bank/documents/{id}/unmatch` | manual matching, ignoring, re-running, undoing |
| `GET/PUT /bank/feeds/{provider}`, `GET …/institutions?country=`, `POST …/requisitions`, `POST …/requisitions/{id}/complete`, `POST /bank/feeds/sync` | PSD2 feed (owner) |
| `GET/POST /reporting/vida`, `GET /reporting/vida/{id}`, `GET …/download?format=json|csv`, `POST …/submit` | ViDA DRR reports |
| `GET/PUT /connectors/{key}/clients/{client_id}`, `POST …/test`, `POST /connectors/{key}/receive` | receiving side of network connectors |

Documents carry `payment_status` (`unpaid` / `partial` / `paid`), `paid_amount` and `paid_at`; the webhook event `invoice.paid` fires when a document becomes paid (payload = the document plus a `payment` object). Export columns: `payment_status`, `paid_amount`, `paid_at`.

## Additions in 0.11

| Area | Endpoints |
|---|---|
| Accounts | `POST /auth/forgot-password`, `POST /auth/link/inspect`, `POST /auth/link/complete`, `POST /users/invite` (no password; returns `link`), `POST /users/{id}/resend-invite`, `/reset-password`, `/reset-2fa`, `/deactivate`, `/reactivate`, `/delete`, `PATCH /users/{id}`, `GET /users/deleted`, `POST /users/deleted/{id}/reveal`, `POST /users/me/totp/setup` (QR SVG), `/confirm` (recovery codes), `POST /users/me/recovery-codes` |
| Platform | `GET /setup/status`, `POST /setup`, `POST /setup/restore`, `GET /platform/overview`, `POST /platform/firms`, `/platform/firms/{id}/deactivate|reactivate|delete|backup|restore`, `POST /platform/switch` |
| Settings | `GET /platform/settings`, `PUT /platform/settings/{smtp|imap|inbound_webhook|ai|backup}`, `PUT /platform/domain`, `POST /platform/domain/check`, `GET /firm-settings`, `PUT|DELETE /firm-settings/{smtp|imap|backup}`, `POST /firm-settings/{smtp|imap}/test`, `PATCH /firm-settings/domains/subdomain`, `PUT|DELETE /firm-settings/domains/custom`, `POST /firm-settings/domains/custom/check` |
| Clients | `POST /client-companies/{id}/inbound-address/regenerate`, `POST /client-companies/{id}/intake-pause` |
| Channels | `PATCH /sender-mappings/{id}` (`accept_from`, `active`), `GET|POST|DELETE /channel-bots`, `/platform/channel-bots`, `POST /channels/telegram/webhook/{bot_id}`, `GET|POST /channels/whatsapp/webhook/{bot_id}` |
| Trash | `POST /documents/trash/preview`, `POST /documents/trash`, `GET /trash`, `POST /trash/{id}/restore`, `POST /trash/{id}/purge`, `GET /trash-requests`, `POST /trash-requests/{id}/approve|reject` |
| Keys, webhooks | `api_keys.client_company_ids`, `allowed_cidrs`; `PATCH /api-keys/{id}`, `POST /api-keys/{id}/rotate`; `webhook_endpoints.client_company_ids`. A client-scoped key sees only its client companies; uploads must name one (`403 client_out_of_scope`). |
| Backups | `GET|POST /backups`, `POST /backups/{id}/reveal` (2FA code), `GET /backups/{id}/download`, `POST /backups/{id}/restore`, `POST /backups/recovery-key[/confirm]`, the same under `/platform/backups` and `/platform/recovery-key` |

New error codes: `mfa_setup_required` (403), `choose_firm` (409), `firm_inactive` (403), `max_firms_reached`
(402), `license_key_required` (422), `platform_license_on_firm` / `not_a_platform_license` (409),
`intake_paused` (423), `ip_not_allowed` (403), `client_out_of_scope` (403), `cooling_off` (409),
`reveals_used_up` (409).
