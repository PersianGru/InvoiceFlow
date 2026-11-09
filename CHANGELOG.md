# Changelog

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
