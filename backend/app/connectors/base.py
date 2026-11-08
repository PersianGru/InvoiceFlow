"""The connector protocol: declare config fields, test a configuration, push approved
documents. One module per accounting system; the registry lists them."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from app.models.document import Document
from app.schemas.invoice import Invoice


@dataclass(frozen=True, slots=True)
class ConfigField:
    key: str
    label: str
    secret: bool = False
    required: bool = True
    hint: str = ""
    default: str = ""


@dataclass(slots=True)
class PushItem:
    document: Document
    invoice: Invoice
    original: bytes | None
    client_name: str | None = None


@dataclass(slots=True)
class PushOutcome:
    document_id: str
    ok: bool
    external_id: str | None = None
    message: str = ""


@dataclass(slots=True)
class PushResult:
    outcomes: list[PushOutcome] = field(default_factory=list)
    artifacts: dict[str, bytes] = field(default_factory=dict)  # files produced (DATEV), stored by the service


@dataclass(slots=True)
class ReceivedItem:
    """One document a network delivered to us."""

    external_id: str
    filename: str
    mime: str
    data: bytes
    receiver_id: str | None = None  # the identifier the network addressed (participant id, SIREN, NIP)
    sender_identity: str = ""
    client_company_id: uuid.UUID | None = None  # set by the service for client-scoped credentials


@dataclass(slots=True)
class ReceiveResult:
    items: list[ReceivedItem] = field(default_factory=list)
    cursor: str | None = None  # position to continue from next time (None = unchanged)


class Connector(Protocol):
    key: str
    name: str
    description: str
    docs_url: str
    fields: tuple[ConfigField, ...]

    async def test(self, config: dict[str, Any]) -> tuple[bool, str]: ...

    async def push(self, items: list[PushItem], config: dict[str, Any]) -> PushResult: ...


@runtime_checkable
class ReceivingConnector(Protocol):
    """A connector that also delivers documents *to* InvoiceFlow. ``receive_scope`` says
    whose credentials the receive side uses: ``firm`` (the firm's configuration, routing by
    ``receiver_id``) or ``client`` (one credential set per client company, ``client_fields``)."""

    key: str
    receive_scope: str
    client_fields: tuple[ConfigField, ...]

    async def receive(self, config: dict[str, Any], cursor: str | None) -> ReceiveResult: ...

    async def acknowledge(self, config: dict[str, Any], external_ids: list[str]) -> None: ...


def capabilities(connector: object) -> list[str]:
    caps = ["push"]
    if isinstance(connector, ReceivingConnector):
        caps.append("receive")
    return caps


def split_config(
    fields: tuple[ConfigField, ...], config: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    """(secret part, plain part) of a configuration, by the field declarations."""
    secret_keys = {f.key for f in fields if f.secret}
    secret = {k: v for k, v in config.items() if k in secret_keys}
    plain = {k: v for k, v in config.items() if k not in secret_keys}
    return secret, plain


def missing_required(fields: tuple[ConfigField, ...], config: dict[str, Any]) -> list[str]:
    return [f.key for f in fields if f.required and not str(config.get(f.key) or f.default or "").strip()]
