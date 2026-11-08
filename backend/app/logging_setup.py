"""Structured logging with secret redaction (spec §2 rule 6, TESTING.md §3 log scan).

The filter runs on every record of every logger: license keys (``IVF-…`` and any other prefix),
JWS tokens, hub credentials (``21c_sk_``, ``21c_at_``, ``whsec_``), installation keys (``ik1.``),
bearer headers, order tokens and private keys are replaced before formatting. Personal data is
never logged by design (log decisions, not data); the filter is the safety net.
"""

from __future__ import annotations

import logging
import re
import sys
from collections.abc import Mapping
from typing import Any

REDACTED = "[redacted]"

_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    # formatted license key: PREFIX-XXXXX-XXXXX-... (5 to 8 groups), any 2-6 char prefix
    ("license_key", re.compile(r"\b[A-Z0-9]{2,6}-(?:[0-9A-Z]{5}-){4,7}[0-9A-Z]{5}\b")),
    # canonical (no dashes) key: prefix + 25..40 crockford chars, uppercase only
    ("license_key_flat", re.compile(r"\b[A-Z]{2,6}[0-9A-HJKMNP-TV-Z]{25,40}\b")),
    # JWS compact serialisation (three base64url parts, first begins with eyJ)
    ("jws", re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")),
    ("hub_api_key", re.compile(r"21c_sk_[A-Za-z0-9_-]+")),
    ("hub_access_token", re.compile(r"21c_at_[A-Za-z0-9_.-]+")),
    ("webhook_secret", re.compile(r"whsec_[A-Za-z0-9_-]+")),
    ("invoiceflow_api_key", re.compile(r"ivfk_[a-z0-9]{8}_[a-z0-9]{16,64}")),
    ("invoiceflow_access_token", re.compile(r"ivfat_[A-Za-z0-9_-]{16,}")),
    ("invoiceflow_webhook_secret", re.compile(r"ivfwh_[A-Za-z0-9]{16,}")),
    ("telegram_bot_token", re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{35}\b")),
    ("meta_access_token", re.compile(r"\bEAA[A-Za-z0-9]{20,}\b")),
    ("installation_key", re.compile(r"ik1\.[1-9]\.[A-Za-z0-9_-]{43}")),
    ("bearer", re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]{8,}")),
    (
        "private_key",
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
    ),
    ("order_token", re.compile(r"\b(?:ord|tok)_[A-Za-z0-9]{12,}\b")),
)


def redact(text: str) -> str:
    out = text
    for _name, pat in _PATTERNS:
        out = pat.sub(REDACTED, out)
    return out


def redact_value(value: Any) -> Any:
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, Mapping):
        return {k: redact_value(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return type(value)(redact_value(v) for v in value)
    return value


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact(record.msg)
        if record.args:
            if isinstance(record.args, Mapping):
                record.args = redact_value(dict(record.args))
            else:
                record.args = tuple(redact_value(a) for a in record.args)
        if record.exc_text:
            record.exc_text = redact(record.exc_text)
        return True


class RedactingFormatter(logging.Formatter):
    """Second net: the final string (including tracebacks) is redacted once more."""

    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))


_installed = False


def configure_logging(level: str = "INFO") -> None:
    global _installed  # noqa: PLW0603
    root = logging.getLogger()
    root.setLevel(level.upper())
    if not _installed:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(RedactingFormatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
        handler.addFilter(RedactingFilter())
        root.addHandler(handler)
        _installed = True
    for h in root.handlers:
        if not any(isinstance(f, RedactingFilter) for f in h.filters):
            h.addFilter(RedactingFilter())
    for name in ("httpx", "httpcore"):  # request lines are noise and may carry paths with tokens
        logging.getLogger(name).setLevel(logging.WARNING)


def scrub_event(event: dict[str, Any], _hint: Any = None) -> dict[str, Any]:
    """``before_send`` for error tracking (Sentry-compatible): the same redaction on the whole event."""
    scrubbed = redact_value(event)
    return scrubbed if isinstance(scrubbed, dict) else {}
