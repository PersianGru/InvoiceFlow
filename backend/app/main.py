"""FastAPI application factory."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import urlsplit

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.errors import install_error_handlers
from app.api.ratelimit import RateLimiter
from app.api.v1 import router as v1_router
from app.api.v1.health import router as health_router
from app.channels.registry import ChannelHub
from app.config import Settings, get_settings
from app.connectors.service import ConnectorRegistry, auto_push_job
from app.db.session import dispose_engine, get_sessionmaker, init_engine
from app.jobs.queue import ArqQueue, InlineQueue, JobQueue
from app.licensing.client import HubClient
from app.licensing.constants import LICENSE_SPEC
from app.licensing.keyring import TrustedKeyRing
from app.licensing.keys import keys_from_config
from app.licensing.service import LicenseService
from app.licensing.state import LicenseStateCache
from app.logging_setup import configure_logging
from app.pipeline.factory import build_runner, build_scanner
from app.pipeline.jobs import process_document_job
from app.security.crypto import MasterKey
from app.storage import Storage, build_storage
from app.webhooks.deliver import deliver_due

log = logging.getLogger("invoiceflow")


def build_license_service(settings: Settings, hub: HubClient | None = None) -> LicenseService:
    hub = hub or HubClient(settings)
    keyring = TrustedKeyRing(
        keys_from_config(settings.license_public_keys), hub, fetch_enabled=settings.license_jwks_fetch
    )
    return LicenseService(settings, MasterKey(settings.master_key_bytes()), hub, keyring, LicenseStateCache())


def create_app(
    settings: Settings | None = None,
    *,
    license_service: LicenseService | None = None,
    storage: Storage | None = None,
    queue: JobQueue | None = None,
    runner_overrides: dict[str, Any] | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)
    overrides = runner_overrides or {}

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        init_engine(settings.database_url)
        app.state.runner = build_runner(
            settings,
            app.state.storage,
            app.state.license_service,
            get_sessionmaker(),
            download_models=settings.queue_backend == "inline",
            **overrides,
        )
        if app.state.queue is None:
            if settings.queue_backend == "arq":
                app.state.queue = ArqQueue(settings.redis_url)
            else:
                app.state.queue = InlineQueue(
                    lambda doc_id: process_document_job(get_sessionmaker(), app.state.runner, doc_id),
                    lambda: deliver_due(get_sessionmaker(), app.state.license_service),
                    lambda doc_id: auto_push_job(
                        get_sessionmaker(),
                        app.state.connectors,
                        app.state.storage,
                        app.state.license_service,
                        doc_id,
                    ),
                )
        log.info(
            "InvoiceFlow %s starting (env=%s, license spec %s)",
            settings.app_version,
            settings.app_env,
            LICENSE_SPEC,
        )
        try:
            yield
        finally:
            await app.state.queue.aclose()
            await app.state.channels.aclose()
            await app.state.connectors.aclose()
            await app.state.license_service.hub.aclose()
            await dispose_engine()

    app = FastAPI(
        title="InvoiceFlow API",
        version=settings.app_version,
        lifespan=lifespan,
        docs_url="/api/docs" if not settings.is_production else None,
        openapi_url="/api/openapi.json",
    )
    app.state.settings = settings
    app.state.master_key = MasterKey(settings.master_key_bytes())
    app.state.license_service = license_service or build_license_service(settings)
    app.state.storage = storage or build_storage(settings)
    app.state.scanner = overrides.get("scanner") or build_scanner(settings)
    app.state.queue = queue
    app.state.runner = None
    app.state.rate_limiter = RateLimiter()
    app.state.channels = ChannelHub(settings)
    app.state.connectors = ConnectorRegistry()
    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=_origins(settings.cors_origins),
            allow_origin_regex=settings.cors_origin_regex if not settings.is_production else None,
            allow_credentials=True,
            allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
            allow_headers=["authorization", "content-type", "x-api-key"],
            expose_headers=["x-session-token"],
        )
    install_error_handlers(app)
    app.include_router(v1_router)
    app.include_router(health_router)

    @app.middleware("http")
    async def _rate_limit_headers(request: Any, call_next: Any) -> Any:
        response = await call_next(request)
        headers = getattr(request.state, "ratelimit_headers", None)
        if headers:
            for k, v in headers.items():
                response.headers[k] = v
        return response

    def _openapi() -> dict[str, Any]:
        if app.openapi_schema:
            return app.openapi_schema
        from fastapi.openapi.utils import get_openapi

        schema = get_openapi(
            title=app.title,
            version=app.version,
            description=OPENAPI_DESCRIPTION,
            routes=app.routes,
            tags=OPENAPI_TAGS,
        )
        comps = schema.setdefault("components", {}).setdefault("securitySchemes", {})
        comps["ApiKeyAuth"] = {"type": "apiKey", "in": "header", "name": "x-api-key"}
        comps["BearerAuth"] = {
            "type": "http",
            "scheme": "bearer",
            "description": "API key or OAuth access token",
        }
        comps["OAuth2ClientCredentials"] = {
            "type": "oauth2",
            "flows": {
                "clientCredentials": {
                    "tokenUrl": "/api/v1/oauth/token",
                    "scopes": {
                        "documents:read": "Read documents and invoices",
                        "documents:write": "Upload, review and approve documents",
                        "exports:read": "List and download exports",
                        "exports:write": "Create exports and templates",
                        "webhooks:manage": "Manage webhook endpoints",
                    },
                }
            },
        }
        comps["SessionCookie"] = {"type": "apiKey", "in": "cookie", "name": "invoiceflow_session"}
        schema["security"] = [
            {"ApiKeyAuth": []},
            {"BearerAuth": []},
            {"OAuth2ClientCredentials": []},
            {"SessionCookie": []},
        ]
        app.openapi_schema = schema
        return schema

    app.openapi = _openapi  # type: ignore[method-assign]
    return app


OPENAPI_DESCRIPTION = (
    "InvoiceFlow public API. Authenticate with an API key (`x-api-key` header or "
    "`Authorization: Bearer ivfk_…`) or an OAuth2 client-credentials access token. Every error is "
    "`{error, message}`; `402 license_required` means the firm's InvoiceFlow subscription is not "
    "active. See docs/api.md for pagination, idempotency keys, rate limits and webhooks."
)
OPENAPI_TAGS = [
    {"name": "documents", "description": "Intake, listing, invoice JSON, review."},
    {
        "name": "exports",
        "description": "Exports in JSON, CSV, XLSX, UBL/XRechnung, CII/Factur-X, PDF, DATEV; templates.",
    },
    {"name": "webhooks", "description": "Signed webhooks with retries."},
    {"name": "api-keys", "description": "API keys and the OAuth2 token endpoint."},
    {"name": "license", "description": "The firm's 21CEL license (License page API)."},
]


def _origins(configured: str) -> list[str]:
    """Origins only: PUBLIC_URL may carry a path (sub-path hosting such as https://host/dashboard)."""
    out: list[str] = []
    for raw in configured.split(","):
        raw = raw.strip()
        if not raw:
            continue
        u = urlsplit(raw)
        out.append(f"{u.scheme}://{u.netloc}" if u.scheme and u.netloc else raw)
    return out


def app_factory() -> FastAPI:
    return create_app()
