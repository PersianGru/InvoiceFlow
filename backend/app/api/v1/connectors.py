"""Connectors API : catalogue, per-firm configuration (secrets
write-only), test, push, runs; receiving side (toggle, per-client credentials, manual poll).
Enabling, testing, pushing, receiving and enabling receive credentials are points of use of the
``connectors`` entitlement."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import FirmDep, LicenseServiceDep, SessionDep, require
from app.audit.service import record_audit
from app.connectors.base import ConfigField, ReceivingConnector, capabilities, missing_required
from app.connectors.receive import receive_for_firm
from app.connectors.service import (
    ConnectorRegistry,
    effective_config,
    public_config,
    push_documents,
    store_config,
)
from app.licensing.gate import check_feature
from app.models.client_company import ClientCompany
from app.models.connector import ClientConnectorCredential, ConnectorRun, FirmConnector
from app.tenancy.context import Actor

router = APIRouter(prefix="/connectors", tags=["connectors"])


def _registry(request: Request) -> ConnectorRegistry:
    return request.app.state.connectors  # type: ignore[no-any-return]


def _fields(fields: tuple[ConfigField, ...]) -> list[dict[str, Any]]:
    return [
        {
            "key": f.key,
            "label": f.label,
            "secret": f.secret,
            "required": f.required,
            "hint": f.hint,
            "default": f.default,
        }
        for f in fields
    ]


class ConnectorOut(BaseModel):
    key: str
    name: str
    description: str
    docs_url: str
    fields: list[dict[str, Any]]
    enabled: bool
    auto_push: bool
    receive: bool = False
    capabilities: list[str] = Field(default_factory=lambda: ["push"])
    receive_scope: str | None = None
    client_fields: list[dict[str, Any]] = Field(default_factory=list)
    routing_field: str | None = None
    config: dict[str, Any]
    last_test_at: str | None
    last_test_ok: bool | None
    last_test_message: str | None


class ConnectorIn(BaseModel):
    enabled: bool = False
    auto_push: bool = False
    receive: bool = False
    config: dict[str, Any] = Field(default_factory=dict)


class PushIn(BaseModel):
    document_ids: list[uuid.UUID] = Field(min_length=1, max_length=500)


class RunOut(BaseModel):
    id: int
    key: str
    document_id: str | None
    trigger: str
    status: str
    external_id: str | None
    message: str
    created_at: str


class ClientCredentialOut(BaseModel):
    client_company_id: str
    client_name: str
    enabled: bool
    config: dict[str, Any]
    cursor: str | None
    last_pull_at: str | None
    last_error: str | None


class ClientCredentialIn(BaseModel):
    enabled: bool = False
    config: dict[str, Any] = Field(default_factory=dict)


def _out(connector: Any, row: FirmConnector | None) -> ConnectorOut:
    receiving = isinstance(connector, ReceivingConnector)
    return ConnectorOut(
        key=connector.key,
        name=connector.name,
        description=connector.description,
        docs_url=connector.docs_url,
        fields=_fields(connector.fields),
        enabled=bool(row and row.enabled),
        auto_push=bool(row and row.auto_push),
        receive=bool(row and row.enabled and (row.settings or {}).get("receive")),
        capabilities=capabilities(connector),
        receive_scope=connector.receive_scope if receiving else None,
        client_fields=_fields(connector.client_fields) if receiving else [],
        routing_field=getattr(connector, "routing_field", None),
        config=public_config(connector, row),
        last_test_at=row.last_test_at.isoformat() if row and row.last_test_at else None,
        last_test_ok=row.last_test_ok if row else None,
        last_test_message=row.last_test_message if row else None,
    )


def _run_out(r: ConnectorRun) -> RunOut:
    return RunOut(
        id=r.id,
        key=r.key,
        document_id=str(r.document_id) if r.document_id else None,
        trigger=r.trigger,
        status=r.status,
        external_id=r.external_id,
        message=r.message,
        created_at=r.created_at.isoformat() if r.created_at else "",
    )


def _cred_out(connector: Any, c: ClientConnectorCredential, name: str) -> ClientCredentialOut:
    return ClientCredentialOut(
        client_company_id=str(c.client_company_id),
        client_name=name,
        enabled=c.enabled,
        config=public_config(connector, c, connector.client_fields),
        cursor=c.cursor,
        last_pull_at=c.last_pull_at.isoformat() if c.last_pull_at else None,
        last_error=c.last_error,
    )


def _connector_or_404(request: Request, key: str) -> Any:
    connector = _registry(request).get(key)
    if connector is None:
        raise HTTPException(404, {"error": "not_found", "message": "Unknown connector."})
    return connector


def _receiving_or_422(connector: Any) -> Any:
    if not isinstance(connector, ReceivingConnector):
        raise HTTPException(
            422, {"error": "invalid_request", "message": "This connector cannot receive documents."}
        )
    return connector


@router.get("", response_model=list[ConnectorOut])
async def list_connectors(
    request: Request, session: SessionDep, actor: Actor = Depends(require("firm.manage"))
) -> list[ConnectorOut]:
    rows = {r.key: r for r in (await session.execute(select(FirmConnector))).scalars().all()}
    return [_out(c, rows.get(c.key)) for c in _registry(request).all()]


@router.get("/runs", response_model=list[RunOut])
async def list_runs(
    session: SessionDep,
    actor: Actor = Depends(require("exports.view")),
    document_id: uuid.UUID | None = None,
    limit: int = Query(default=50, ge=1, le=200),
) -> list[RunOut]:
    stmt = select(ConnectorRun).order_by(ConnectorRun.id.desc()).limit(limit)
    if document_id is not None:
        stmt = stmt.where(ConnectorRun.document_id == document_id)
    return [_run_out(r) for r in (await session.execute(stmt)).scalars().all()]


@router.get("/{key}", response_model=ConnectorOut)
async def get_connector(
    request: Request, key: str, session: SessionDep, actor: Actor = Depends(require("firm.manage"))
) -> ConnectorOut:
    connector = _connector_or_404(request, key)
    return _out(connector, await session.get(FirmConnector, (actor.firm_id, key)))


@router.put("/{key}", response_model=ConnectorOut)
async def put_connector(
    request: Request,
    key: str,
    body: ConnectorIn,
    session: SessionDep,
    firm: FirmDep,
    svc: LicenseServiceDep,
    actor: Actor = Depends(require("firm.manage")),
) -> ConnectorOut:
    connector = _connector_or_404(request, key)
    if body.receive:
        _receiving_or_422(connector)
    if body.enabled or body.receive:
        check_feature(svc.snapshot(firm), "connectors")  # point of use
    row = await session.get(FirmConnector, (firm.id, key))
    if row is None:
        row = FirmConnector(firm_id=firm.id, key=key)
        session.add(row)
    secrets = svc.secrets(firm)
    store_config(secrets, row, connector, {k: v for k, v in body.config.items() if isinstance(k, str)})
    if body.enabled:
        missing = missing_required(connector.fields, effective_config(secrets, row))
        if missing:
            raise HTTPException(
                422,
                {
                    "error": "invalid_request",
                    "message": f"Missing: {', '.join(missing)}.",
                    "missing": missing,
                },
            )
    row.enabled, row.auto_push = body.enabled, body.auto_push and body.enabled
    row.settings = {**(row.settings or {}), "receive": bool(body.receive and body.enabled)}
    await session.flush()
    await record_audit(
        session,
        firm_id=firm.id,
        actor=actor,
        action="connector.configure",
        target_type="connector",
        target_id=key,
        after={"enabled": row.enabled, "auto_push": row.auto_push, "settings": row.settings},
    )
    return _out(connector, row)


@router.post("/{key}/test")
async def test_connector(
    request: Request,
    key: str,
    session: SessionDep,
    firm: FirmDep,
    svc: LicenseServiceDep,
    actor: Actor = Depends(require("firm.manage")),
) -> dict[str, Any]:
    connector = _registry(request).get(key)
    row = await session.get(FirmConnector, (firm.id, key))
    if connector is None or row is None:
        raise HTTPException(404, {"error": "not_found", "message": "Configure the connector first."})
    check_feature(svc.snapshot(firm), "connectors")
    ok, message = await connector.test(effective_config(svc.secrets(firm), row))
    row.last_test_at, row.last_test_ok, row.last_test_message = datetime.now(UTC), ok, message[:300]
    await record_audit(
        session,
        firm_id=firm.id,
        actor=actor,
        action="connector.test",
        target_type="connector",
        target_id=key,
        result="ok" if ok else "failed",
    )
    return {"ok": ok, "message": message}


@router.post("/{key}/push", response_model=list[RunOut])
async def push(
    request: Request,
    key: str,
    body: PushIn,
    session: SessionDep,
    firm: FirmDep,
    svc: LicenseServiceDep,
    actor: Actor = Depends(require("exports.create")),
) -> list[RunOut]:
    _connector_or_404(request, key)
    check_feature(svc.snapshot(firm), "connectors")
    runs = await push_documents(
        session,
        registry=_registry(request),
        storage=request.app.state.storage,
        service=svc,
        firm=firm,
        key=key,
        document_ids=list(body.document_ids),
        trigger="manual",
        actor=actor,
    )
    return [_run_out(r) for r in runs]


# ---- receiving side -----------------------------------------------------------------
@router.get("/{key}/clients", response_model=list[ClientCredentialOut])
async def list_client_credentials(
    request: Request, key: str, session: SessionDep, actor: Actor = Depends(require("firm.manage"))
) -> list[ClientCredentialOut]:
    connector = _receiving_or_422(_connector_or_404(request, key))
    names = {c.id: c.name for c in (await session.execute(select(ClientCompany))).scalars().all()}
    rows = (
        (await session.execute(select(ClientConnectorCredential).where(ClientConnectorCredential.key == key)))
        .scalars()
        .all()
    )
    return [_cred_out(connector, c, names.get(c.client_company_id, "")) for c in rows]


@router.put("/{key}/clients/{client_id}", response_model=ClientCredentialOut)
async def put_client_credentials(
    request: Request,
    key: str,
    client_id: uuid.UUID,
    body: ClientCredentialIn,
    session: SessionDep,
    firm: FirmDep,
    svc: LicenseServiceDep,
    actor: Actor = Depends(require("firm.manage")),
) -> ClientCredentialOut:
    connector = _receiving_or_422(_connector_or_404(request, key))
    client = await session.get(ClientCompany, client_id)
    if client is None:
        raise HTTPException(404, {"error": "not_found", "message": "Client company not found."})
    if body.enabled:
        check_feature(svc.snapshot(firm), "connectors")  # point of use
    row = await session.get(ClientConnectorCredential, (firm.id, client_id, key))
    if row is None:
        row = ClientConnectorCredential(firm_id=firm.id, client_company_id=client_id, key=key)
        session.add(row)
    secrets = svc.secrets(firm)
    store_config(
        secrets,
        row,
        connector,
        {k: v for k, v in body.config.items() if isinstance(k, str)},
        fields=connector.client_fields,
    )
    if body.enabled:
        missing = missing_required(connector.client_fields, effective_config(secrets, row))
        if missing:
            raise HTTPException(
                422,
                {
                    "error": "invalid_request",
                    "message": f"Missing: {', '.join(missing)}.",
                    "missing": missing,
                },
            )
    row.enabled = body.enabled
    await session.flush()
    await record_audit(
        session,
        firm_id=firm.id,
        actor=actor,
        action="connector.client_configure",
        target_type="connector",
        target_id=f"{key}:{client_id}",
        after={"enabled": row.enabled, "settings": row.settings},
    )
    return _cred_out(connector, row, client.name)


@router.post("/{key}/clients/{client_id}/test")
async def test_client_credentials(
    request: Request,
    key: str,
    client_id: uuid.UUID,
    session: SessionDep,
    firm: FirmDep,
    svc: LicenseServiceDep,
    actor: Actor = Depends(require("firm.manage")),
) -> dict[str, Any]:
    connector = _receiving_or_422(_connector_or_404(request, key))
    row = await session.get(ClientConnectorCredential, (firm.id, client_id, key))
    firm_row = await session.get(FirmConnector, (firm.id, key))
    if row is None:
        raise HTTPException(404, {"error": "not_found", "message": "Configure the client credentials first."})
    check_feature(svc.snapshot(firm), "connectors")
    secrets = svc.secrets(firm)
    config = {**(effective_config(secrets, firm_row) if firm_row else {}), **effective_config(secrets, row)}
    ok, message = await connector.test(config)
    row.last_error = None if ok else message[:300]
    return {"ok": ok, "message": message}


@router.post("/{key}/receive")
async def receive_now(
    request: Request,
    key: str,
    session: SessionDep,
    firm: FirmDep,
    svc: LicenseServiceDep,
    actor: Actor = Depends(require("firm.manage")),
) -> dict[str, int]:
    _receiving_or_422(_connector_or_404(request, key))
    check_feature(svc.snapshot(firm), "connectors")
    row = await session.get(FirmConnector, (firm.id, key))
    if row is None or not row.enabled or not (row.settings or {}).get("receive"):
        raise HTTPException(
            422, {"error": "invalid_request", "message": "Enable the connector and its receiving first."}
        )
    stored: list[uuid.UUID] = []
    counts = await receive_for_firm(
        session,
        registry=_registry(request),
        storage=request.app.state.storage,
        service=svc,
        scanner=request.app.state.scanner,
        queue=request.app.state.queue,
        firm=firm,
        key=key,
        actor=actor,
        stored_ids=stored,
    )
    await session.commit()  # the worker must see the rows before the job starts
    queue = request.app.state.queue
    if queue is not None:
        for doc_id in stored:
            await queue.enqueue_process_document(doc_id)
    return counts
