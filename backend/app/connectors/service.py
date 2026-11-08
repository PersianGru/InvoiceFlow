"""Connector service: the registry, per-firm configuration (secrets encrypted with the firm's data
key), the push orchestration with run log, and auto-push on approval. Every push re-checks the
``connectors`` entitlement (connectors are switched off in the unlicensed version)."""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.audit.service import record_audit
from app.connectors.base import ConfigField, Connector, PushItem, missing_required, split_config
from app.connectors.datev import DatevConnector
from app.connectors.ksef import KsefConnector
from app.connectors.lexoffice import LexofficeConnector
from app.connectors.networks import FrancePdpConnector, PeppolConnector
from app.db.rls import set_platform_bypass, set_tenant
from app.licensing.service import LicenseService
from app.licensing.store import FirmSecrets
from app.models.client_company import ClientCompany
from app.models.connector import ClientConnectorCredential, ConnectorRun, FirmConnector
from app.models.document import Document, DocumentStatus
from app.models.firm import Firm
from app.schemas.invoice import Invoice
from app.storage.base import Storage, StorageError
from app.tenancy.context import Actor

log = logging.getLogger("invoiceflow.connectors")


class ConnectorRegistry:
    def __init__(self, http: httpx.AsyncClient | None = None) -> None:
        self.http = http or httpx.AsyncClient(timeout=30, follow_redirects=False)
        self._all: dict[str, Connector] = {
            "datev": DatevConnector(self.http),
            "lexoffice": LexofficeConnector(self.http),
            "peppol": PeppolConnector(self.http),
            "france_pdp": FrancePdpConnector(self.http),
            "ksef": KsefConnector(self.http),
        }

    def get(self, key: str) -> Connector | None:
        return self._all.get(key)

    def all(self) -> list[Connector]:
        return list(self._all.values())

    async def aclose(self) -> None:
        await self.http.aclose()


def _label(row: FirmConnector | ClientConnectorCredential) -> str:
    if isinstance(row, ClientConnectorCredential):
        return f"connector:{row.key}:{row.client_company_id}"
    return f"connector:{row.key}"


def effective_config(secrets: FirmSecrets, row: FirmConnector | ClientConnectorCredential) -> dict[str, Any]:
    plain = dict(row.settings or {})
    if row.config_enc:
        raw = secrets.open(_label(row), row.config_enc)
        if raw:
            try:
                plain.update(json.loads(raw))
            except ValueError:
                log.warning("connector %s secret blob unreadable", row.key)
    return plain


def store_config(
    secrets: FirmSecrets,
    row: FirmConnector | ClientConnectorCredential,
    connector: Connector,
    config: dict[str, Any],
    *,
    fields: tuple[ConfigField, ...] | None = None,
) -> None:
    """Merge a (possibly partial) configuration: unknown keys dropped, omitted or empty secrets kept.
    ``fields`` defaults to the connector's firm-level fields; pass ``client_fields`` for a
    per-client credential row."""
    declared = connector.fields if fields is None else fields
    known = {f.key for f in declared}
    secret, plain = split_config(declared, {k: v for k, v in config.items() if k in known})
    existing = effective_config(secrets, row) if row.config_enc else {}
    secret_keys = {f.key for f in declared if f.secret}
    merged_secret = {k: v for k, v in existing.items() if k in secret_keys}
    merged_secret.update({k: v for k, v in secret.items() if v not in (None, "")})
    row.settings = {**(row.settings or {}), **plain}
    row.config_enc = secrets.seal(_label(row), json.dumps(merged_secret)) if merged_secret else None
    row.updated_at = datetime.now(UTC)


def public_config(
    connector: Connector,
    row: FirmConnector | ClientConnectorCredential | None,
    fields: tuple[ConfigField, ...] | None = None,
) -> dict[str, Any]:
    """Settings for the API: secrets replaced by a presence flag."""
    out: dict[str, Any] = {}
    plain = dict(row.settings or {}) if row else {}
    for f in connector.fields if fields is None else fields:
        if f.secret:
            out[f.key] = "" if not (row and row.config_enc) else "••••••"
        else:
            out[f.key] = plain.get(f.key, f.default)
    return out


async def _items_for(session: AsyncSession, storage: Storage, docs: list[Document]) -> list[PushItem]:
    client_ids = {d.client_company_id for d in docs if d.client_company_id}
    names: dict[uuid.UUID, str] = {}
    if client_ids:
        for c in (
            await session.execute(select(ClientCompany).where(ClientCompany.id.in_(client_ids)))
        ).scalars():
            names[c.id] = c.name
    items: list[PushItem] = []
    for d in docs:
        try:
            original = await storage.get(d.storage_key)
        except StorageError:
            original = None
        items.append(
            PushItem(
                d,
                Invoice.model_validate(d.extracted),
                original,
                names.get(d.client_company_id) if d.client_company_id else None,
            )
        )
    return items


async def push_documents(
    session: AsyncSession,
    *,
    registry: ConnectorRegistry,
    storage: Storage,
    service: LicenseService,
    firm: Firm,
    key: str,
    document_ids: list[uuid.UUID],
    trigger: str,
    actor: Actor | None,
) -> list[ConnectorRun]:
    """Push the given approved documents through one connector; one run row per document."""
    connector = registry.get(key)
    row = await session.get(FirmConnector, (firm.id, key))
    runs: list[ConnectorRun] = []
    if connector is None or row is None or not row.enabled:
        for did in document_ids:
            runs.append(
                ConnectorRun(
                    firm_id=firm.id,
                    key=key,
                    document_id=did,
                    trigger=trigger,
                    status="skipped",
                    message="connector not enabled",
                )
            )
        session.add_all(runs)
        return runs
    snap = service.snapshot(firm)
    if not snap.core_on or not snap.feature_on("connectors"):
        for did in document_ids:
            runs.append(
                ConnectorRun(
                    firm_id=firm.id,
                    key=key,
                    document_id=did,
                    trigger=trigger,
                    status="skipped",
                    message="license_required: connectors",
                )
            )
        session.add_all(runs)
        await record_audit(
            session,
            firm_id=firm.id,
            actor=actor,
            action="connector.push",
            target_type="connector",
            target_id=key,
            result="refused",
            after={"reason": "license_required", "documents": len(document_ids)},
        )
        return runs
    docs = (
        (
            await session.execute(
                select(Document).where(
                    Document.id.in_(document_ids),
                    Document.deleted_at.is_(None),
                    Document.processing_restricted.is_(False),
                )
            )
        )
        .scalars()
        .all()
    )
    eligible = [d for d in docs if d.status == DocumentStatus.approved and d.extracted is not None]
    for d in docs:
        if d not in eligible:
            runs.append(
                ConnectorRun(
                    firm_id=firm.id,
                    key=key,
                    document_id=d.id,
                    trigger=trigger,
                    status="skipped",
                    message="not approved",
                )
            )
    if eligible:
        config = effective_config(service.secrets(firm), row)
        missing = missing_required(connector.fields, config)
        if missing:
            for d in eligible:
                runs.append(
                    ConnectorRun(
                        firm_id=firm.id,
                        key=key,
                        document_id=d.id,
                        trigger=trigger,
                        status="error",
                        message=f"configuration incomplete: {', '.join(missing)}",
                    )
                )
        else:
            items = await _items_for(session, storage, eligible)
            try:
                result = await connector.push(items, config)
            except Exception as e:  # noqa: BLE001 - a connector crash is a run error, never a request crash
                log.exception("connector %s push failed: %s", key, type(e).__name__)
                result = None
            if result is None:
                for d in eligible:
                    runs.append(
                        ConnectorRun(
                            firm_id=firm.id,
                            key=key,
                            document_id=d.id,
                            trigger=trigger,
                            status="error",
                            message="connector failed",
                        )
                    )
            else:
                for name, data in result.artifacts.items():
                    await storage.put(
                        f"connectors/{firm.id}/{key}/{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}/{name}",
                        data,
                    )
                for o in result.outcomes:
                    runs.append(
                        ConnectorRun(
                            firm_id=firm.id,
                            key=key,
                            document_id=uuid.UUID(o.document_id),
                            trigger=trigger,
                            status="ok" if o.ok else "error",
                            external_id=o.external_id,
                            message=o.message[:500],
                        )
                    )
    session.add_all(runs)
    await session.flush()
    await record_audit(
        session,
        firm_id=firm.id,
        actor=actor,
        action="connector.push",
        target_type="connector",
        target_id=key,
        after={
            "trigger": trigger,
            "ok": sum(1 for r in runs if r.status == "ok"),
            "error": sum(1 for r in runs if r.status == "error"),
            "skipped": sum(1 for r in runs if r.status == "skipped"),
        },
    )
    return runs


async def auto_push_job(
    sessionmaker: async_sessionmaker[AsyncSession],
    registry: ConnectorRegistry,
    storage: Storage,
    service: LicenseService,
    document_id: uuid.UUID,
) -> dict[str, int]:
    """Worker job: push one approved document to every enabled connector with auto_push."""
    counts = {"ok": 0, "error": 0, "skipped": 0}
    async with sessionmaker() as session:
        async with session.begin():
            await set_platform_bypass(session)
            doc = await session.get(Document, document_id)
            if doc is None:
                return counts
            firm = await session.get(Firm, doc.firm_id)
            if firm is None:
                return counts
            await set_tenant(session, firm.id)
            rows = (
                (
                    await session.execute(
                        select(FirmConnector).where(
                            FirmConnector.enabled.is_(True), FirmConnector.auto_push.is_(True)
                        )
                    )
                )
                .scalars()
                .all()
            )
            for row in rows:
                runs = await push_documents(
                    session,
                    registry=registry,
                    storage=storage,
                    service=service,
                    firm=firm,
                    key=row.key,
                    document_ids=[doc.id],
                    trigger="auto",
                    actor=None,
                )
                for r in runs:
                    counts[r.status] = counts.get(r.status, 0) + 1
    return counts
