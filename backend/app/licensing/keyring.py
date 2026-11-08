"""Trusted keys at run time (spec §8.2 step 2, §11).

Shipped keys (``LICENSE_PUBLIC_KEYS``) are trusted from the start. An unknown ``kid`` triggers one
JWKS fetch over HTTPS (rate-limited); after a successful fetch the trusted set is exactly what the
hub lists, so retired keys keep verifying and revoked keys disappear.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping

from app.licensing.client import HubClient, HubUnreachableError
from app.licensing.clock import Clock, system_clock
from app.licensing.keys import PublicKeyConfigError, keys_from_jwks

log = logging.getLogger("invoiceflow.licensing.keyring")


class TrustedKeyRing:
    def __init__(
        self,
        shipped: Mapping[str, bytes],
        client: HubClient | None,
        *,
        clock: Clock = system_clock,
        min_fetch_interval: int = 600,
        fetch_enabled: bool = True,
    ) -> None:
        self._shipped = dict(shipped)
        self._fetched: dict[str, bytes] | None = None
        self._client = client
        self._clock = clock
        self._min_interval = min_fetch_interval
        self._last_fetch = 0
        self._fetch_enabled = fetch_enabled
        self.fetch_count = 0

    @property
    def keys(self) -> dict[str, bytes]:
        return dict(self._fetched) if self._fetched is not None else dict(self._shipped)

    def knows(self, kid: str | None) -> bool:
        return kid is not None and kid in self.keys

    async def refresh_from_hub(self) -> bool:
        """Fetch the JWKS once; replace the trusted set with what is listed. False when nothing changed."""
        if self._client is None or not self._fetch_enabled:
            return False
        now = self._clock()
        if now - self._last_fetch < self._min_interval:
            return False
        self._last_fetch = now
        self.fetch_count += 1
        try:
            jwks = await self._client.get_keys()
            parsed = keys_from_jwks(jwks)
        except HubUnreachableError as e:
            log.warning("JWKS fetch failed: %s", e.kind)
            return False
        except (PublicKeyConfigError, TypeError, KeyError, ValueError):
            log.warning("JWKS answer was not usable; keeping the current keys")
            return False
        if not parsed:
            log.warning("JWKS listed no Ed25519 key; keeping the current keys")
            return False
        dropped = set(self.keys) - set(parsed)
        self._fetched = parsed
        if dropped:
            log.warning("dropped %d key(s) no longer listed by the hub", len(dropped))
        return True

    async def ensure(self, kid: str | None) -> bool:
        """True when ``kid`` is trusted, after at most one JWKS fetch (spec §8.2 step 2)."""
        if self.knows(kid):
            return True
        await self.refresh_from_hub()
        return self.knows(kid)
