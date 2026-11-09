# Changelog

## 0.11.0 — platform, users, per-client channels, backups (2026-11-09)

Behaviour changes (read before upgrading):

- **No public sign-up any more.** On a fresh installation the first visitor sets up the platform admin
  with a one-time setup code printed in the API log (`docker compose logs api`). Firms are created by the
  platform admin. Existing installations: run the setup once after the upgrade (the existing firms stay as
  they are). `POST /api/v1/firms` is gone (405).
- **2FA is mandatory for every account.** Users without an authenticator app set it up (QR code) at their
  next sign-in before anything else works. `MFA_ENFORCED=false` exists for development and is refused in
  production.
- **Invites have no password field.** An invite sends a one-time link (24 h) by email and shows the same
  link to copy; the person sets the password and 2FA. Password reset links last 15 minutes.
- **Settings move to the dashboard.** SMTP, IMAP, the inbound-mail webhook token and the AI engines are
  set under Platform → Settings (and per firm for mail and backups). Values from the environment are
  imported once at the first start after the setup and shown as "imported from environment".
- **A sender may be paired with several client companies**, also of different firms; the platform bot asks
  which one (or `/company`, or a `#tag`). Before, a second pairing was refused.

Added:

- Users: resend invite, reset password, reset 2FA (owner), change role, deactivate/reactivate
  (pairings follow), delete with an anonymous placeholder and a sealed trace only the firm owner can
  reveal, 10 recovery codes, `POST /auth/forgot-password`. Accountants manage client users; owners manage
  staff; the last owner cannot be removed.
- Platform: first-run setup, super admin, platform overview, create/deactivate/
  delete firms, switching into a firm (audited in that firm), the platform license (`platform` tool,
  `max_firms`; without it one firm), license kinds cannot be swapped, every further firm needs its own key
  checked before the firm exists.
- Settings and domains: dashboard settings with write-only secrets and platform defaults, the
  verified platform root domain (TXT `_invoiceflow.<domain>`), a platform subdomain per firm (renamable),
  a firm's own verified domain, daily DNS checks that never switch anything off, IMAP per firm, SMTP test,
  AI settings picked up by the worker without a restart.
- Email routing: every recipient header is read (`X-Original-To`, all `Delivered-To`,
  `Envelope-To`, `X-Forwarded-To`, `To`, `Cc`), so forwarding, catch-all mailboxes and CC route correctly;
  two client companies in one mail are quarantined (`ambiguous_recipient`).
- Compromise response: regenerate a client company's address (old one never reissued, grace
  0–14 days for known senders), intake pause per client company and channel (email, Telegram, WhatsApp,
  upload, API).
- Chat channels: a Telegram group or channel per client company (`/pair CODE`, members-only
  switch, supergroup migration), channel posts, own bots and WhatsApp numbers per firm or dedicated to one
  client company (webhook registered automatically, tokens encrypted), held files for multi-company
  senders (24 h, then quarantine), receipts name the client company.
- Trash: justification for every step, more than 5 documents need a firm owner's approval
  (requests expire after 7 days), bookkeeping records are protected (approved, exported, pushed, network),
  restore, delete-now after 24 h, automatic purge after 7–90 days (default 30).
- Scoped integrations: API keys and webhooks limited to client companies, enforced in the
  application and by a restrictive database policy; IP allowlist, key rotation and editing.
- Backups: encrypted firm and platform backups (`.tar.gz.age`) in a separate
  `backup` container with CPU/memory limits; passwords shown at most 5 times after a fresh 2FA code (the
  audit log records who, never the password); recovery keys; restore with a safety backup first; platform
  restore on a fresh server from the setup page, firms restored from their own backups with the same ids;
  daily schedule with 7/4/12 retention; built-in storage or S3 with optional object lock.
- Database migration `0009` runs automatically (the `migrate` service).

## 0.10.2 — first public release (2026-11-09)

- The image is built with the current Debian security updates and scanned (secrets, Trivy) before it is
  published.

- One published image, `ghcr.io/persiangru/invoiceflow`, runs the API, the worker and the web dashboards;
  the customer `docker-compose.yml` and `env.example` are attached to every release. Nothing to build.
- Intake from WhatsApp, Telegram, email, the client portal and app, drag-and-drop, ZIP batches and the API,
  with a quarantine for unknown senders.
- Reading: structured e-invoices (UBL, CII, XRechnung, Factur-X) parsed directly; built-in OCR and a small
  local model that run on a plain CPU; bring your own local model (Ollama, LM Studio, vLLM) or connect a
  cloud provider (OpenAI, Mistral, Claude), opt-in per firm with a monthly cap and a call log.
- Rule packs for every EU country: totals, VAT, ids, IBANs, dates, duplicates and mandatory fields.
- Review inbox with assignment, bulk actions, comments and a per-document timeline; supplier profiles
  that learn from corrections.
- Exports (JSON, CSV, XLSX, UBL, XRechnung, Factur-X, DATEV), DATEV and Lexware Office connectors,
  signed webhooks, public API with keys and OAuth2, n8n workflows.
- Bank reconciliation (CAMT.053, MT940, PSD2 feeds), Peppol, French PDP and KSeF, ViDA reporting data sets.
- Per-firm encryption, retention from rule packs, data-subject rights tooling, archive integrity, OIDC
  single sign-on.
- Licensing per accounting firm: plans by size (Starter, Business, Firm, Unlimited), monthly or yearly,
  and a limited free trial.

## 0.10.3 — faster local reasoning models (2026-11-09)

- Your own local model through an OpenAI-compatible server or gateway: `LOCAL_OPENAI_THINKING=false` (the
  default) makes reasoning models such as qwen3-vl answer without a thinking phase; a receipt went from
  89 s to 10 s with qwen3-vl:8b. Servers that do not know the switch are retried without it.
- Answers that a gateway returns in the reasoning field are read, and the model is given InvoiceFlow's
  field template, so local models fill the right fields.
- `deploy/.env.example` lists the own-local-model variables (`LOCAL_OPENAI_*`).
