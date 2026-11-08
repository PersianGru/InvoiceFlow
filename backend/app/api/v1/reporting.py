"""Digital reporting API: ViDA DRR reports per client and period."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import FirmDep, LicenseServiceDep, SessionDep, require
from app.audit.service import record_audit
from app.licensing.gate import check_feature
from app.models.client_company import ClientCompany
from app.models.reporting import VidaReport
from app.reporting.vida import ReportingTransport, StorageTransport, build_report
from app.storage.base import Storage, StorageError
from app.tenancy.context import Actor

router = APIRouter(prefix="/reporting", tags=["reporting"])


def _storage(request: Request) -> Storage:
    return request.app.state.storage  # type: ignore[no-any-return]


def _transport(request: Request) -> ReportingTransport:
    t = getattr(request.app.state, "reporting_transport", None)
    if t is None:
        t = request.app.state.reporting_transport = StorageTransport()
    return t


class ReportIn(BaseModel):
    client_company_id: uuid.UUID | None = None
    period_start: date
    period_end: date


class ReportOut(BaseModel):
    id: str
    client_company_id: str | None
    period_start: str
    period_end: str
    status: str
    format_version: str
    document_count: int
    totals: dict[str, Any]
    submitted_at: str | None
    submitted_ref: str | None
    created_at: str
    transport: str = Field(default="storage")


def _out(r: VidaReport, transport: str) -> ReportOut:
    return ReportOut(
        id=str(r.id),
        client_company_id=str(r.client_company_id) if r.client_company_id else None,
        period_start=r.period_start.isoformat(),
        period_end=r.period_end.isoformat(),
        status=r.status,
        format_version=r.format_version,
        document_count=r.document_count,
        totals=dict(r.totals or {}),
        submitted_at=r.submitted_at.isoformat() if r.submitted_at else None,
        submitted_ref=r.submitted_ref,
        created_at=r.created_at.isoformat() if r.created_at else "",
        transport=transport,
    )


@router.get("/vida", response_model=list[ReportOut])
async def list_reports(
    request: Request,
    session: SessionDep,
    actor: Actor = Depends(require("exports.view")),
    limit: int = Query(default=50, ge=1, le=200),
) -> list[ReportOut]:
    rows = (
        (await session.execute(select(VidaReport).order_by(VidaReport.created_at.desc()).limit(limit)))
        .scalars()
        .all()
    )
    return [_out(r, _transport(request).key) for r in rows]


@router.post("/vida", response_model=ReportOut, status_code=201)
async def create_report(
    request: Request,
    body: ReportIn,
    session: SessionDep,
    firm: FirmDep,
    svc: LicenseServiceDep,
    actor: Actor = Depends(require("reporting.manage")),
) -> ReportOut:
    check_feature(svc.snapshot(firm), "core")
    if body.period_end < body.period_start or (body.period_end - body.period_start).days > 370:
        raise HTTPException(
            422,
            {"error": "invalid_request", "message": "The period must be at most one year, end after start."},
        )
    if (
        body.client_company_id is not None
        and await session.get(ClientCompany, body.client_company_id) is None
    ):
        raise HTTPException(404, {"error": "not_found", "message": "Client company not found."})
    report, _lines = await build_report(
        session,
        _storage(request),
        firm=firm,
        client_company_id=body.client_company_id,
        period_start=body.period_start,
        period_end=body.period_end,
        created_by=actor.user_id if not actor.is_api_key else None,
    )
    await record_audit(
        session,
        firm_id=firm.id,
        actor=actor,
        action="reporting.generate",
        target_type="vida_report",
        target_id=str(report.id),
        after={
            "documents": report.document_count,
            "period": [body.period_start.isoformat(), body.period_end.isoformat()],
        },
    )
    return _out(report, _transport(request).key)


@router.get("/vida/{report_id}", response_model=ReportOut)
async def get_report(
    request: Request,
    report_id: uuid.UUID,
    session: SessionDep,
    actor: Actor = Depends(require("exports.view")),
) -> ReportOut:
    r = await session.get(VidaReport, report_id)
    if r is None:
        raise HTTPException(404, {"error": "not_found", "message": "Report not found."})
    return _out(r, _transport(request).key)


@router.get("/vida/{report_id}/download")
async def download_report(
    request: Request,
    report_id: uuid.UUID,
    session: SessionDep,
    actor: Actor = Depends(require("exports.view")),
    format: str = Query(default="json", pattern="^(json|csv)$"),
) -> Response:
    r = await session.get(VidaReport, report_id)
    if r is None:
        raise HTTPException(404, {"error": "not_found", "message": "Report not found."})
    try:
        data = await _storage(request).get(f"{r.storage_key}.{format}")
    except StorageError as e:
        raise HTTPException(404, {"error": "not_found", "message": "Report file missing."}) from e
    media = "application/json" if format == "json" else "text/csv"
    name = f"vida-drr-{r.period_start.isoformat()}-{r.period_end.isoformat()}.{format}"
    return Response(
        content=data, media_type=media, headers={"content-disposition": f'attachment; filename="{name}"'}
    )


@router.post("/vida/{report_id}/submit", response_model=ReportOut)
async def submit_report(
    request: Request,
    report_id: uuid.UUID,
    session: SessionDep,
    firm: FirmDep,
    svc: LicenseServiceDep,
    actor: Actor = Depends(require("reporting.manage")),
) -> ReportOut:
    check_feature(svc.snapshot(firm), "core")
    r = await session.get(VidaReport, report_id)
    if r is None:
        raise HTTPException(404, {"error": "not_found", "message": "Report not found."})
    if r.status == "submitted":
        raise HTTPException(409, {"error": "invalid_state", "message": "The report was already submitted."})
    try:
        payload = await _storage(request).get(f"{r.storage_key}.json")
    except StorageError as e:
        raise HTTPException(404, {"error": "not_found", "message": "Report file missing."}) from e
    transport = _transport(request)
    r.submitted_ref = (await transport.submit(r, payload))[:200]
    r.status, r.submitted_at = "submitted", datetime.now(UTC)
    await record_audit(
        session,
        firm_id=firm.id,
        actor=actor,
        action="reporting.submit",
        target_type="vida_report",
        target_id=str(r.id),
        after={"transport": transport.key, "ref": r.submitted_ref},
    )
    return _out(r, transport.key)
