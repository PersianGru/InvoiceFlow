"""Per-key rate limit: fixed one-minute window, in process. Returns the headers to send
and whether the request is allowed; the caller answers 429 with ``Retry-After``."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Decision:
    allowed: bool
    limit: int
    remaining: int
    reset_in: int  # seconds until the window ends

    def headers(self) -> dict[str, str]:
        h = {
            "x-ratelimit-limit": str(self.limit),
            "x-ratelimit-remaining": str(self.remaining),
            "x-ratelimit-reset": str(self.reset_in),
        }
        if not self.allowed:
            h["retry-after"] = str(self.reset_in)
        return h


class RateLimiter:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._windows: dict[str, tuple[int, int]] = {}  # key → (window start, count)

    def check(self, key: str, limit: int, now: float | None = None) -> Decision:
        now = time.time() if now is None else now
        window = int(now // 60) * 60
        with self._lock:
            start, count = self._windows.get(key, (window, 0))
            if start != window:
                start, count = window, 0
            allowed = count < limit
            if allowed:
                count += 1
            self._windows[key] = (start, count)
            if len(self._windows) > 50_000:  # bounded memory: drop stale windows
                self._windows = {k: v for k, v in self._windows.items() if v[0] == window}
        return Decision(allowed, limit, max(0, limit - count), max(1, int(start + 60 - now)))
