# Poland — KSeF (Krajowy System e-Faktur)

Since 2026 B2B invoices in Poland are issued and received through the national system. InvoiceFlow
talks to the **KSeF 2.0 API** directly (no bridge): one KSeF token per client company (the token is
issued in the KSeF web application for the taxpayer's NIP and carries the rights to read and issue).

## Setup

1. Settings → Clients: the client's NIP as *Tax number*.
2. Settings → Connectors → Poland — KSeF: environment `test`, `demo` or `prod`, enable and switch
   on receiving. Then, per client: NIP and KSeF token (write-only, encrypted with the firm's data
   key); **Test** authenticates once.
3. The worker polls every 5 minutes; the cursor per client is the last pulled date.

Receiving and the per-client credentials are points of use of the `connectors` entitlement.

## What the connector does

| Step | KSeF 2.0 call |
|---|---|
| Authenticate | `POST /api/v2/auth/challenge` → `POST /api/v2/auth/ksef-token` with `"<token>|<timestamp ms>"` encrypted RSA-OAEP(SHA-256) under the certificate from `GET /api/v2/security/public-key-certificates` → `GET /api/v2/auth/{ref}` → `POST /api/v2/auth/token/redeem` |
| Receive | `POST /api/v2/invoices/query/metadata` (`subjectType = Subject2`, invoicing date from the cursor) → `GET /api/v2/invoices/ksef/{ksefNumber}` (FA(3) XML, parsed by the structured-first pipeline) |
| Send | `POST /api/v2/sessions/online` with an AES-256-CBC session key encrypted for KSeF, `POST …/invoices` with the encrypted FA(3) and its SHA-256, `POST …/close`. Only outgoing documents whose **original is FA(3)** are sent — InvoiceFlow does not author FA(3). |

Environment base URLs: `api-test.ksef.mf.gov.pl`, `api-demo.ksef.mf.gov.pl`, `api.ksef.mf.gov.pl`.

## Before production

The flow is implemented from the public API description and tested against mocked endpoints
(`tests/test_networks.py`). Run **Test** and one **Receive now** against the KSeF *test*
environment with a test NIP before switching a client to `prod`; the Ministry changes field
names between API milestones. Nothing in the connector weakens the intake checks: every FA(3)
goes through size, type and virus checks before storage.
