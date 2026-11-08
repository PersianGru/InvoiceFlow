# InvoiceFlow

Invoice intake for accounting firms. Clients send invoices however they like (WhatsApp, Telegram, email,
the client portal or app, drag-and-drop, ZIP batches, the API); InvoiceFlow reads them, checks them
against country rule packs, and delivers clean data to your accounting software. Only uncertain documents
reach a person.

- **Receive**: one intake for every channel; unknown senders wait in quarantine until mapped to a client.
- **Read**: structured e-invoices (UBL, CII, XRechnung, Factur-X) are parsed directly; scans and photos go
  through OCR and a local language model, with a confidence score on every field. Cloud AI is optional and
  opt-in per firm.
- **Check**: totals, VAT rates and ids, IBANs, dates, duplicates and mandatory fields per country.
- **Review**: side-by-side view, highlighted fields, keyboard shortcuts, questions back to the client.
- **Export**: JSON, CSV, XLSX, UBL, XRechnung, Factur-X and DATEV formats; DATEV and Lexware Office
  connectors, signed webhooks, a documented API (keys and OAuth2) and n8n workflows.
- Bank reconciliation (CAMT.053, MT940, PSD2 feeds), Peppol / French PDP / KSeF networks, ViDA reporting
  data sets, per-firm encryption, OIDC single sign-on.

Self-hosted on your own servers. Every installation is licensed per accounting firm.

## Install

The stack runs from one published image, `ghcr.io/persiangru/invoiceflow`, with PostgreSQL, Redis and
ClamAV next to it. Nothing to build:

```bash
curl -LO https://github.com/PersianGru/InvoiceFlow/releases/latest/download/docker-compose.yml
curl -L -o .env https://github.com/PersianGru/InvoiceFlow/releases/latest/download/env.example
# fill in SECRET_KEY, MASTER_KEY, POSTGRES_PASSWORD, APP_DB_PASSWORD and PUBLIC_URL in .env
docker compose up -d
```

[deploy/README.md](deploy/README.md) covers Portainer, host requirements, every variable, the first start,
upgrades and backups. [docs/api.md](docs/api.md) documents the public API and webhooks, and
[docs/integrations](docs/integrations) the connectors and networks.

## License

After the first start, open Settings → License: it shows the installation id. Buy a plan or start a
trial at [ivf.21cel.com/store](https://ivf.21cel.com/store) with that id and paste the key. Without a
license everything stays readable and intake is paused.

## Repository

| Path | What it is |
|---|---|
| `backend/` | API and worker (Python 3.12, FastAPI, SQLAlchemy 2, PostgreSQL with row-level security, Arq). |
| `web/` | Dashboards and client portal (Next.js). |
| `apps/mobile/` | Client app (Expo / React Native). |
| `deploy/` | The customer `docker-compose.yml`, `.env.example` and deployment guide. |
| `Dockerfile` | Builds the product image (API, worker and web server in one image). |
| `docs/` | API, integrations and compliance documentation. |

Build the image yourself with `docker build -t invoiceflow:<version> .` and set `IMAGE=invoiceflow:<version>`
in `.env`.

See [CHANGELOG.md](CHANGELOG.md) for what changed in each version.
