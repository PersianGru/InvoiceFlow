# Peppol (access point bridge)

InvoiceFlow receives e-invoices addressed to your clients' Peppol participant ids and sends
outgoing UBL invoices through **your access point provider**. Running a certified access point
needs OpenPeppol membership and an AS4 gateway; InvoiceFlow does not pretend to be one.
Instead it talks to a small HTTPS contract that an access point provider, or a thin adapter in
front of one, implements. The same contract serves the French platform connector
(`france-pdp.md`).

## Setup

1. Settings → Clients: enter each client's **Peppol id** (`scheme:id`, e.g. `0208:0123456789`
   for a Belgian enterprise number, `9930:DE123456789` for a German VAT id). Documents the network
   delivers for an id that matches no client are stored in quarantine (never processed) and can be
   released to the right client from the review screen.
2. Settings → Connectors → Peppol: bridge base URL (`https://…`), API token, your sender id. Enable
   the connector and **Receive documents from this network**. Both need the `connectors`
   entitlement; the unlicensed version pulls nothing (the provider keeps the documents).
3. The worker polls every 5 minutes (`connector_receive_tick`); **Receive now** pulls at once.

## Bridge contract

All paths relative to the base URL; every request carries `Authorization: Bearer <token>`.

| Call | Answer |
|---|---|
| `GET /health` | 2xx when the token is valid (401/403 otherwise) |
| `GET /inbox?since=<cursor>` | `{"documents": [{"id", "receiver", "sender", "filename", "mime", "document_type", "created_at"}], "cursor": "<opaque>"}`; at most 100 are read per poll |
| `GET /inbox/{id}` | the document bytes (UBL/CII XML; Factur-X PDF for the French platform), ≤ 25 MB |
| `POST /inbox/{id}/ack` | 2xx; InvoiceFlow acknowledges **only after** the document is stored (or rejected as malformed/malware, which is logged) |
| `POST /outbox` | multipart: `document` file, fields `receiver` (participant id), `document_type`, `sender`; answers `{"id": "<provider id>"}` |

`receiver` is matched against the clients' Peppol ids case-insensitively, ignoring whitespace.
Provider ids de-duplicate re-deliveries (a document InvoiceFlow already stored is acknowledged
again and not stored twice). The cursor is stored per firm and advanced only when every delivered
document was stored; when the monthly document limit stops a poll, nothing after the stop is
acknowledged and the cursor stays.

## Sending

**Send to Peppol** (review screen or `POST /connectors/peppol/push`) sends approved documents whose
direction is *outgoing*: the original when it already is UBL/CII, otherwise UBL 2.1 (Peppol BIS
Billing 3.0) rendered from the canonical invoice. The buyer's electronic address (BT-49) is the
receiver.

## Security

The token is stored encrypted with the firm's data key and never returned; the bridge URL must be
`https://`. The intake checks (size, type by magic bytes, virus scan, PDF sanitising) run before
storage exactly as for uploads.
