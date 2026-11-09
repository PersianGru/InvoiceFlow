"""IMAP polling (the alternative to the inbound webhook): fetch unseen messages, hand the raw bytes
to the intake, mark them seen. Network work runs in a thread; the mailbox client is injectable."""

from __future__ import annotations

import imaplib
import logging
from collections.abc import Awaitable, Callable, Iterator
from typing import Protocol

from app.settings_store import Section

log = logging.getLogger("invoiceflow.channels.imap")


class Mailbox(Protocol):
    def unseen(self) -> Iterator[tuple[str, bytes]]: ...

    def mark_seen(self, uid: str) -> None: ...

    def close(self) -> None: ...


class ImapMailbox:
    def __init__(
        self,
        host: str,
        port: int = 993,
        ssl: bool = True,
        user: str = "",
        password: str = "",
        folder: str = "INBOX",
    ) -> None:
        cls = imaplib.IMAP4_SSL if ssl else imaplib.IMAP4
        self.conn = cls(host, port, timeout=30)
        self.conn.login(user, password)
        status, _ = self.conn.select(folder)
        if status != "OK":
            raise imaplib.IMAP4.error(f"folder {folder!r} not found")

    @classmethod
    def from_section(cls, section: Section) -> ImapMailbox:
        return cls(
            str(section.get("host", "")),
            int(section.get("port", 993)),
            bool(section.get("ssl", True)),
            str(section.get("user", "")),
            str(section.get("password", "")),
            str(section.get("folder", "INBOX")),
        )

    def unseen(self) -> Iterator[tuple[str, bytes]]:
        status, data = self.conn.uid("SEARCH", "UNSEEN")
        if status != "OK" or not data or not data[0]:
            return
        for uid in data[0].split()[:50]:
            status, parts = self.conn.uid("fetch", uid, "(RFC822)")
            if status != "OK" or not parts or not isinstance(parts[0], tuple):
                continue
            yield uid.decode(), parts[0][1]

    def mark_seen(self, uid: str) -> None:
        self.conn.uid("store", uid, "+FLAGS", "(\\Seen)")

    def close(self) -> None:
        try:
            self.conn.logout()
        except (OSError, imaplib.IMAP4.error):
            pass


async def poll_mailbox(mailbox: Mailbox, handle_raw: Callable[[bytes], Awaitable[str]]) -> dict[str, int]:
    """Runs the handler per message; a handler error leaves the message unseen for the next poll."""
    counts = {"handled": 0, "failed": 0}
    try:
        for uid, raw in list(mailbox.unseen()):
            try:
                await handle_raw(raw)
                mailbox.mark_seen(uid)
                counts["handled"] += 1
            except Exception as e:  # noqa: BLE001 - one bad message must not stop the poll
                log.exception("inbound mail %s failed: %s", uid, type(e).__name__)
                counts["failed"] += 1
    finally:
        mailbox.close()
    return counts
