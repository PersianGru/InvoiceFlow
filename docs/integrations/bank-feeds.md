# Bank reconciliation: statements and PSD2 feeds

**Bank** in the dashboard matches payments to invoices and sets each document's payment status
(`unpaid` → `partial` → `paid`, with `paid_amount` and `paid_at`). Two ways to bring transactions in:

## Statement files

Import a **CAMT.053** (ISO 20022, any `camt.053.001.0x`) or **MT940** file. The account is created
from the IBAN on first import (optionally attached to a client company); the file's SHA-256
refuses a second import of the same file; transactions de-duplicate by the bank's reference
(`AcctSvcrRef` / `//bank reference` in `:61:`) or, without one, by a content hash.

Recognised remittance layouts: CAMT `Ustrd` and structured creditor references; MT940 `:86:` with
`?20–?29` / `?32–?33` / `?31` subfields (`EREF+`, `SVWZ+`), and the slash layout (`/REMI/`,
`/NAME/`, `/IBAN/`, `/EREF/`).

## PSD2 feed (GoCardless Bank Account Data)

Owner → Bank → *Bank feed*: secret id and key of a GoCardless Bank Account Data application
(stored encrypted per firm). Then per account: pick the bank, **Link an account** (opens the bank's
consent flow in a new tab), come back and **Finish linking**. Transactions are pulled daily
(`bank_feed_tick`, 05:10) or with **Pull now**; the cursor keeps a three-day overlap and the
provider's transaction ids drop duplicates. Consents expire after the period the bank grants
(typically 90 days): a failing pull shows as the feed's last error and a new link is needed.

Egress: `bankaccountdata.gocardless.com:443`. Another aggregator is one class implementing
`app.bank.feeds.BankFeedProvider`.

## Matching

Rule-based and explainable (`app/bank/matching.py`): amount (full or partial), invoice number in
the remittance text, supplier IBAN, supplier name, date window. A match is **automatic** only with
the full amount plus the invoice number or the supplier IBAN, and only when no second invoice
scores the same; everything else above 50 % is a **suggestion** an accountant confirms or rejects.
Manual matching, ignoring a transaction, re-running the matcher and undoing a document's matches
are one click each. Confirming emits the `invoice.paid` webhook; the export columns
`payment_status`, `paid_amount`, `paid_at` carry the state to accounting.

Imports and decisions need the `core` tool (the unlicensed version keeps everything readable);
API keys use the scopes `bank:read` / `bank:write`.
