# Sub-processors

`TODO(legal)`: confirm the contractual names and locations with each provider before publishing.

| Sub-processor | Purpose | Data received | Location | When |
|---|---|---|---|---|
| 21CEL License Hub (`license.21cel.com`) | License activation and the periodic check (spec §8.5) | product id, license key (hashed on the hub side), sealed installation id, machine fingerprint (8 digits), platform code, app version, the hub's nonce; **no** firm name unless `LICENSE_SEND_INSTANCE_NAME=true`, no invoice data, no user data | EU | always (every 7 days / on activation) |
| Object storage provider (MinIO self-hosted or an EU-region S3 bucket) | Archive of original files and exports | documents, exports | EU (operator's choice) | always |
| ClamAV (self-hosted) | Virus scan at intake | the file bytes | on the installation | always |
| Ollama / vLLM (self-hosted) | Local extraction | document text | on the installation | when configured |
| Anthropic (Claude API) | Cloud extraction fallback | document text of the firm's documents | per the provider's region terms (`TODO(legal)`: zero-data-retention terms) | **only** when the firm opted in **and** the license carries `cloud_ai` |
| European Commission VIES | VAT id validation | VAT ids | EU | when enabled (default on) |
| European Central Bank | Reference exchange rates | none (public rates) | EU | when enabled (default on) |
| Email provider (inbound webhook / IMAP / SMTP) | Email channel | emails and attachments sent to the dedicated addresses; questions sent to clients | operator's choice | when configured |
| Telegram (Bot API) | Telegram channel | messages and files clients send to the bot; replies | Telegram's infrastructure | when configured |
| Meta (WhatsApp Business Cloud API) | WhatsApp channel | messages and files clients send to the number; replies | Meta's infrastructure | when configured |
| Accounting software connectors (DATEV, cloud connector) | Export of approved invoices | the exported records | per provider | when the firm connects them (`connectors` entitlement) |

Every external AI call is logged in `ai_call_log` with provider, purpose and data categories
; `docs/compliance/AI-SYSTEM-INVENTORY.md` lists the systems.
