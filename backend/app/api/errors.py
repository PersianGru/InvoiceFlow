"""One error shape for the whole API: ``{"error": "<code>", "message": "<human text>", ...}``."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import HTTPException, RequestValidationError
from fastapi.responses import JSONResponse

from app.licensing.service import LicenseError

log = logging.getLogger("invoiceflow.api")


def _payload(detail: Any, default_code: str) -> dict[str, Any]:
    if isinstance(detail, dict) and "error" in detail:
        return detail
    return {"error": default_code, "message": str(detail)}


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(LicenseError)
    async def _license_error(_r: Request, exc: LicenseError) -> JSONResponse:
        body: dict[str, Any] = {"error": exc.code, "message": exc.message}
        if exc.hint:
            body["hint"] = exc.hint
        return JSONResponse(status_code=exc.status, content=body)

    @app.exception_handler(HTTPException)
    async def _http_error(_r: Request, exc: HTTPException) -> JSONResponse:
        codes = {
            401: "unauthenticated",
            403: "forbidden",
            404: "not_found",
            409: "conflict",
            429: "rate_limited",
        }
        return JSONResponse(
            status_code=exc.status_code,
            content=_payload(exc.detail, codes.get(exc.status_code, "error")),
            headers=exc.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_r: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "error": "invalid_request",
                "message": "The request is not valid.",
                "details": exc.errors(),
            },
        )

    @app.exception_handler(Exception)
    async def _unhandled(_r: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled error: %s", type(exc).__name__)
        return JSONResponse(
            status_code=500, content={"error": "internal", "message": "Something went wrong."}
        )
