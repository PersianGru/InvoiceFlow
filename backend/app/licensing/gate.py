"""The license gate: FastAPI dependencies and limit checks used by every intake,
processing, review, export, connector and API route. Reads only the firm's verified claims.

Refusals answer ``402`` with ``{"error": "license_required" | "license_limit_reached", ...}`` and a
human message; the data itself is never touched (spec §2 rule 9).
"""

from __future__ import annotations

from fastapi import HTTPException

from app.licensing.state import LicenseSnapshot

STATE_MESSAGES = {
    "unlicensed": (
        "Your accountant's InvoiceFlow subscription is not active. Existing documents stay available."
    ),
    "expired": "Your accountant's InvoiceFlow subscription has ended. Existing documents stay available.",
    "invalid": "The InvoiceFlow license could not be verified. Existing documents stay available.",
    "grace": "The InvoiceFlow license could not be refreshed and is in its grace period.",
}

FEATURE_MESSAGES = {
    "channels": "Messaging channels (WhatsApp, Telegram, email) are not part of this InvoiceFlow plan.",
    "connectors": "Accounting-software connectors are not part of this InvoiceFlow plan.",
    "cloud_ai": "Cloud AI extraction is not part of this InvoiceFlow plan.",
    "api": "The public API is not part of this InvoiceFlow plan.",
    "sso": "Single sign-on is not part of this InvoiceFlow plan.",
}

LIMIT_MESSAGES = {
    "max_client_companies": "This InvoiceFlow plan allows at most {limit} client companies.",
    "max_documents_per_month": "This InvoiceFlow plan allows at most {limit} documents per month.",
    "max_staff_users": "This InvoiceFlow plan allows at most {limit} staff users.",
}


class LicenseRequired(HTTPException):
    def __init__(self, snapshot: LicenseSnapshot, feature: str = "core") -> None:
        if feature == "core":
            message = STATE_MESSAGES.get(snapshot.state, STATE_MESSAGES["unlicensed"])
        else:
            message = FEATURE_MESSAGES.get(feature, f"The feature '{feature}' is not part of this plan.")
        super().__init__(
            status_code=402,
            detail={
                "error": "license_required",
                "message": message,
                "state": snapshot.state,
                "feature": feature,
            },
        )


class LicenseLimitReached(HTTPException):
    def __init__(self, name: str, limit: int, current: int) -> None:
        template = LIMIT_MESSAGES.get(name, "This InvoiceFlow plan's limit '{name}' ({limit}) is reached.")
        super().__init__(
            status_code=402,
            detail={
                "error": "license_limit_reached",
                "message": template.format(name=name, limit=limit),
                "limit": name,
                "allowed": limit,
                "current": current,
            },
        )


def check_feature(snapshot: LicenseSnapshot, feature: str = "core") -> LicenseSnapshot:
    """Raise ``LicenseRequired`` unless the core tool runs and ``feature`` is entitled."""
    if not snapshot.core_on:
        raise LicenseRequired(snapshot, "core")
    if feature != "core" and not snapshot.feature_on(feature):
        raise LicenseRequired(snapshot, feature)
    return snapshot


def check_limit(snapshot: LicenseSnapshot, name: str, current: int, adding: int = 1) -> None:
    """Enforce a numeric limit at the point of use. ``0`` means unlimited."""
    limit = snapshot.limit(name)
    if limit and current + adding > limit:
        raise LicenseLimitReached(name, limit, current)
