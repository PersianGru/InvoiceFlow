"""Messages from support (spec §8.5, §2 rule 10): plain text, shown at least once per id."""

from __future__ import annotations

from typing import Any

from app.licensing.state import LicenseSnapshot
from app.models.firm import Firm


def messages_with_seen(snapshot: LicenseSnapshot, firm: Firm) -> list[dict[str, Any]]:
    seen = set(firm.shown_message_ids or [])
    out = []
    for m in snapshot.messages:
        level = m["level"] if m["level"] in ("info", "warning") else "info"
        out.append({"id": m["id"], "level": level, "text": m["text"], "seen": m["id"] in seen})
    return out


def mark_seen(firm: Firm, message_id: str) -> None:
    ids = list(firm.shown_message_ids or [])
    if message_id not in ids:
        ids.append(message_id)
        firm.shown_message_ids = ids[-50:]
