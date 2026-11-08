"""The InvoiceFlow project's licensing material, built into the product (21CEL spec §7.1, §8).

- ``PROJECT_PUBLIC_KEYS``: the public keys that verify license tokens offline (public by nature).
- ``PROJECT_INSTALLATION_KEY``: seals installation ids. It ships inside the released image but is
  kept out of the repository: the Release workflow fills it in from the repository secret
  ``LICENSE_INSTALLATION_KEY`` before building. It is never logged.
"""

PROJECT_PUBLIC_KEYS = (
    '{"keys":[{"alg":"EdDSA","kty":"OKP","crv":"Ed25519","x":"JtL'
    'N2KIYOsdy6ZBImlhMw_3pHl68nGEZoL4e_xY8WqY","kid":"invoiceflow-202611-u4n5l4bv","use":"sig","status":"active"}]}'
)
PROJECT_INSTALLATION_KEY = ""
