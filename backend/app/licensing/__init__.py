"""21CEL licensing client for InvoiceFlow (spec 1.7).

Pure pieces (no I/O): ``installid``, ``verifier``, ``keys``, ``offline``.
I/O pieces: ``client`` (HTTPS to the hub), ``store`` (encrypted rows), ``service`` (flows),
``refresh`` (background job), ``gate`` (FastAPI dependencies).
"""
