# France — certified platforms (PDP)

From 1 September 2026 every French business must be able to receive e-invoices through a
certified platform (plateforme de dématérialisation partenaire, PDP); issuing follows in 2026
(large and mid-size) and 2027 (small). Platforms expose vendor-specific APIs, so InvoiceFlow uses the
same bridge contract as Peppol (`peppol.md`) with two differences:

- **Routing by SIREN**: Settings → Clients → *Tax number* holds the client's SIREN; the bridge's
  `receiver` is the SIREN (or SIRET; both are normalised to digits and compared by prefix of the
  SIREN when the client stored one).
- **Formats both ways**: Factur-X PDF, UBL and CII are accepted from the inbox; outgoing documents
  are deposited as the original when it is Factur-X/UBL/CII, otherwise as UBL.

Connector key: `france_pdp`; channel on received documents: `pdp`. Lifecycle statuses (déposée,
rejetée, refusée, encaissée) are a platform feature that the bridge may expose later; InvoiceFlow
records its own payment status from the bank reconciliation.

The e-reporting obligations (transaction and payment data for B2C / cross-border) are covered by
the digital reporting module for the ViDA data set (`docs/compliance/VIDA.md`) once the French
formats are published.
