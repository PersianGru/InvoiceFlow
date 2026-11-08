# Audit-proof archive (GoBD-style)

Originals are the evidence; everything else is derived from them.

| Requirement | How InvoiceFlow meets it |
|---|---|
| Completeness | Every inbound file is stored before any processing (`app/intake.py`), with channel, sender, time and SHA-256; refused files are audited, never silently dropped. |
| Unchangeability | The original is written once under `firms/<firm>/documents/<id>/original`. Local storage marks it read-only; with S3 and `S3_OBJECT_LOCK=compliance` the object carries Object Lock retention until `retention_until` (WORM: no overwrite or early delete, not even by an administrator). Corrections never touch the original: they create new extraction versions. |
| Tamper evidence | `GET /dsr/verify/{id}` recomputes the hash and compares it with the recorded one (audited); the hash is also in every export and webhook payload. |
| Traceability | Append-only audit log of every upload, view, download, export, review action, deletion and license action; per-document timeline. |
| Retention | From the country rule pack, end-of-year rule, firm floor; recorded per document as `retention_rule`; deletion only by the retention job after the period (also for unlicensed firms). |
| Readability over time | Originals are kept in their original format plus the canonical JSON; PDF renders for images are produced on demand. Exports in open formats (JSON, CSV, UBL, CII). |
| Access in the unlicensed version | Reading, searching and downloading stay available when the license ends (spec §2 rule 9). |

## Operator setup for WORM on S3 / MinIO

1. Create the bucket with Object Lock enabled (`aws s3api create-bucket --object-lock-enabled-for-bucket`
   or `mc mb --with-lock`); Object Lock cannot be added to an existing bucket.
2. Set `S3_OBJECT_LOCK=compliance` (or `governance` for environments where an operator may lift it).
3. Versioning is implied; lifecycle rules may expire old versions **after** the retention date only.
4. Verify with `aws s3api get-object-retention` on a stored original.

`TODO(legal)`: confirm the procedural documentation (Verfahrensdokumentation) the firm must keep
for GoBD, and the equivalents in other markets.
