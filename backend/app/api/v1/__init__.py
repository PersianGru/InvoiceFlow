from fastapi import APIRouter

from app.api.v1 import (
    api_keys,
    audit,
    auth,
    bank,
    channels,
    client_companies,
    connectors,
    dashboard,
    documents,
    dsr,
    engines,
    exports,
    firms,
    license,
    portal,
    reporting,
    schemas,
    sso,
    users,
    webhooks,
)

router = APIRouter(prefix="/api/v1")
router.include_router(auth.router)
router.include_router(firms.router)
router.include_router(license.router)
router.include_router(client_companies.router)
router.include_router(users.router)
router.include_router(documents.router)
router.include_router(dashboard.router)
router.include_router(audit.router)
router.include_router(exports.router)
router.include_router(api_keys.router)
router.include_router(webhooks.router)
router.include_router(channels.router)
router.include_router(portal.router)
router.include_router(dsr.router)
router.include_router(sso.router)
router.include_router(connectors.router)
router.include_router(bank.router)
router.include_router(reporting.router)
router.include_router(engines.router)
router.include_router(schemas.router)
