from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.schemas.invoice import INVOICE_SCHEMA_V1, invoice_json_schema

router = APIRouter(prefix="/schemas", tags=["schemas"])


@router.get("/invoice/v1")
async def invoice_schema_v1() -> JSONResponse:
    return JSONResponse(content=invoice_json_schema(), headers={"x-schema-id": INVOICE_SCHEMA_V1})
