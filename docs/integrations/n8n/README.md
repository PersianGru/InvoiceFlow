# n8n with InvoiceFlow

Two directions, both through the public API (`docs/api.md`):

1. **InvoiceFlow → n8n**: register an n8n **Webhook** node URL under Settings → Webhooks, pick the
   events, keep the signing secret. Verify every delivery in a **Code** node (below) before acting.
2. **n8n → InvoiceFlow**: an **HTTP Request** node with the header `x-api-key: ivfk_…` (Settings →
   API keys, scopes `documents:write` for uploads, `documents:read` / `exports:*` for reading).

Both example workflows import through *Workflows → Import from file*.

| File | What it does |
|---|---|
| `invoice-approved-to-spreadsheet.json` | Webhook (`invoice.approved`) → verify signature → `GET /documents/{id}/invoice` → append a row (replace the last node with your sheet/ERP node). |
| `folder-to-invoiceflow.json` | Watches a folder (local file trigger; swap for Dropbox/Drive/SFTP) → `POST /documents` with the file and an `Idempotency-Key`. |

## Verifying a webhook in a Code node

```javascript
// Input: the Webhook node (set "Raw Body" on so the HMAC covers the exact bytes).
const crypto = require('crypto');
const secret = $env.INVOICEFLOW_WEBHOOK_SECRET;              // ivfwh_… from Settings → Webhooks
const header = $input.first().json.headers['x-invoiceflow-signature'];   // t=<unix>,v1=<hex>
const body = $input.first().binary?.data ? Buffer.from($input.first().binary.data.data, 'base64') : Buffer.from(JSON.stringify($input.first().json.body));
const parts = Object.fromEntries(header.split(',').map((p) => p.split('=')));
if (Math.abs(Date.now() / 1000 - Number(parts.t)) > 300) throw new Error('stale signature');
const want = crypto.createHmac('sha256', secret).update(`${parts.t}.`).update(body).digest('hex');
if (!crypto.timingSafeEqual(Buffer.from(want), Buffer.from(parts.v1))) throw new Error('bad signature');
return [{ json: $input.first().json.body }];
```

## Uploading from n8n

HTTP Request node → `POST https://<api>/api/v1/documents`, *Body Content Type* `multipart/form-data`,
field `file` = the binary from the previous node, headers `x-api-key` and `Idempotency-Key`
(an expression such as `{{ $json.id }}` so retries never create duplicates). Optional query
`client_company_id` picks the client; `402 license_required` means the firm's subscription is paused.

## Rate limits and errors

Keys default to 120 requests/minute (`429` + `Retry-After`). Every error is `{error, message}`.
