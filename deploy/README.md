# Deploying InvoiceFlow (Portainer or Docker Compose)

A release is three files:

| File | What it is |
|---|---|
| `invoiceflow-<v>.tar.gz` | the source with one `Dockerfile` at the root. It builds **one image**, `invoiceflow:<v>`, that runs the API, the worker and the web dashboards (the compose file starts it three times with different commands). |
| `docker-compose.yml` | the whole stack: `postgres`, `redis`, `clamav`, `migrate`, `api`, `worker`, `web`. |
| `.env` | the variables the stack reads at deploy time. The required ones are empty; fill them in. |

## 1. Get the image

Nothing to build: the stack pulls `ghcr.io/persiangru/invoiceflow:<v>`, published by the Release workflow of
the GitHub repository for every version. Pin another one with `INVOICEFLOW_VERSION` in `.env`.

Building it yourself (an air-gapped host, or your own changes): `docker build -t invoiceflow:<v> .` in the source
tree, or Portainer -> Images -> Build a new image -> upload `invoiceflow-<v>.tar.gz` (Dockerfile path
`Dockerfile`), then set `IMAGE=invoiceflow:<v>` in `.env`. The first build downloads about 1 GB of Python wheels
and Node modules and needs no GPU and no system package on the host; Tesseract and the built-in CPU engines
(RapidOCR, llama.cpp) are inside.

## 2. Deploy the stack (Portainer → Stacks → Add stack → Upload)

Upload `docker-compose.yml`, then paste the contents of `.env` under *Environment variables*
(Portainer's *Advanced mode* takes the file as is). Required: `SECRET_KEY`, `MASTER_KEY`,
`POSTGRES_PASSWORD`, `APP_DB_PASSWORD`, `PUBLIC_URL`. The stack refuses to start without them
(`${VAR:?}`), and the API refuses to start in production without a `MASTER_KEY` of the right shape
and ClamAV. The licensing material (the project's public keys and installation key) is built into
the product: nothing to enter. Firms activate their own license keys on Settings → License.

With the CLI: put `.env` next to `docker-compose.yml` and run `docker compose up -d`.

Services: `postgres` (16), `redis`, `clamav`, `migrate` (runs the schema migrations, exits), `api`,
`worker`, `web` (port `WEB_PORT`, default 3000; it proxies `/api` to the API, so the browser needs one
origin only). Put your reverse proxy with TLS in front of `web` and set `PUBLIC_URL` to that https
address; keep `COOKIE_SECURE=true`.

Host requirements: 4 GB RAM (ClamAV takes ~1.3 GB, the built-in model ~1.5 GB), 2+ CPU cores, a
readable `/etc/machine-id` (or point `MACHINE_ID_PATH` at a world-readable file with 32 random hex characters that
never changes: the sealed installation id is derived from it), outbound HTTPS to
`license.21cel.com` (licensing), `huggingface.co` (one-time model download, or pre-seed
`/data/models`) and ClamAV's database mirrors.

### Advanced variables

`.env` holds only what is decided at deploy time. Everything a firm configures itself (cloud opt-in,
policy and monthly cap, connectors, SSO, API keys, webhooks, users, the license) is in the dashboard.
The variables below have built-in defaults and can be added to `.env` when needed:

| Area | Variables (default) |
|---|---|
| Product | `APP_ENV` (production), `APP_VERSION`, `LOG_LEVEL` (INFO), `STORE_URL`, `SUPPORT_URL` |
| Licensing | `LICENSE_KEY_PREFIX` (IVF), `LICENSE_JWKS_FETCH` (true), `LICENSE_SEND_INSTANCE_NAME` (false) |
| Storage | `S3_OBJECT_LOCK` ("", governance, compliance), `VIRUS_SCAN_REQUIRED` (required in production) |
| OCR | `OCR_ENGINE` (auto, tesseract, rapidocr, none), `OCR_LANGUAGES`, `OCR_CLOUD` (none, mistral), `MISTRAL_OCR_MODEL` |
| Local model | `LOCAL_LLM` (auto, llamacpp, ollama, openai_compat, none), `LLAMACPP_CONTEXT` (8192), `LLAMACPP_AUTO_DOWNLOAD` (true), `OLLAMA_URL`, `OLLAMA_MODEL`, `LOCAL_OPENAI_BASE_URL`, `LOCAL_OPENAI_API_KEY`, `LOCAL_OPENAI_MODEL`, `LOCAL_OPENAI_VISION` (true), `LOCAL_OPENAI_THINKING` (false); all of them are also set under Platform → Settings → AI engines |
| Cloud AI | `CLOUD_PROVIDER` (auto), `CLOUD_MODEL`, `OPENAI_MODEL`, `OPENAI_BASE_URL`, `MISTRAL_MODEL`, `CLOUD_OPENAI_COMPAT_BASE_URL`, `CLOUD_OPENAI_COMPAT_API_KEY`, `CLOUD_OPENAI_COMPAT_MODEL`, `CLOUD_VISION` (true) |
| Validation | `VIES_ENABLED` (true), `ECB_RATES_ENABLED` (true) |
| Sessions and API | `SESSION_TTL_SECONDS` (43200), `PASSWORD_MIN_LENGTH` (12), `LOGIN_LOCK_THRESHOLD` (5), `LOGIN_LOCK_MINUTES` (15), `LOGIN_IP_RATE_PER_MINUTE` (20), `API_ACCESS_TOKEN_TTL_SECONDS` (3600), `API_DEFAULT_RATE_LIMIT_PER_MINUTE` (120), `WEBHOOK_TIMEOUT_SECONDS` (10) |
| Mail and channels | `IMAP_PORT` (993), `IMAP_SSL` (true), `IMAP_FOLDER` (INBOX), `SMTP_PORT` (587), `SMTP_STARTTLS` (true), `WHATSAPP_API_VERSION` (v21.0) |

## 3. First start

1. Open `PUBLIC_URL`, create the firm (first user is the firm owner).
2. Settings → License shows the installation id. Buy or start a trial in the 21CEL store with that
   id, paste the key, **Activate**. Without a license the product shows its unlicensed version:
   everything stays readable, intake is paused.
3. Settings → Processing: engines and the self-test; Settings → Users, Clients, Channels, Connectors
   as needed.

## 4. Upgrades and backups

Set `INVOICEFLOW_VERSION` in `.env` to the new version (the image is pulled from the GitHub Container
Registry; an image you built yourself: change `IMAGE`) and redeploy (`docker compose pull && docker compose up -d`): `migrate` applies the schema changes before `api` and `worker` start. Back up the
`pgdata` and `appdata` volumes together (documents are encrypted per firm under `MASTER_KEY`:
keep that secret with the backup, in your secret manager, never in the compose file).

## 5. What the release does not contain

No firm license keys, no hub credentials, no customer data, no test keys, no mock hub. The product
carries only what the 21CEL spec says ships inside a product: the project's public keys (to verify
license tokens offline) and its installation key (to seal installation ids). Keys for firms come
from the store.

