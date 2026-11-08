"""Role-based access control, enforced server-side on every route."""

from __future__ import annotations

from collections.abc import Iterable

from app.models.user import Role

# What each role may do; routes name the permission they need.
PERMISSIONS: dict[str, frozenset[Role]] = {
    "platform.admin": frozenset({Role.super_admin}),
    "firm.manage": frozenset({Role.firm_owner}),
    "firm.license.manage": frozenset({Role.firm_owner}),
    "firm.license.view": frozenset({Role.firm_owner, Role.accountant, Role.junior_accountant}),
    "staff.invite": frozenset({Role.firm_owner}),
    "clients.manage": frozenset({Role.firm_owner, Role.accountant}),
    "clients.view": frozenset({Role.firm_owner, Role.accountant, Role.junior_accountant, Role.api_key}),
    "documents.upload": frozenset(
        {Role.firm_owner, Role.accountant, Role.client_admin, Role.client_user, Role.api_key}
    ),
    "documents.view": frozenset(
        {
            Role.firm_owner,
            Role.accountant,
            Role.junior_accountant,
            Role.client_admin,
            Role.client_user,
            Role.api_key,
        }
    ),
    "documents.review": frozenset({Role.firm_owner, Role.accountant, Role.junior_accountant, Role.api_key}),
    "documents.approve": frozenset({Role.firm_owner, Role.accountant, Role.api_key}),
    "exports.create": frozenset({Role.firm_owner, Role.accountant, Role.api_key}),
    "exports.view": frozenset({Role.firm_owner, Role.accountant, Role.junior_accountant, Role.api_key}),
    "api_keys.manage": frozenset({Role.firm_owner}),
    "webhooks.manage": frozenset({Role.firm_owner, Role.api_key}),
    "audit.view": frozenset({Role.firm_owner, Role.accountant}),
    "dsr.manage": frozenset({Role.firm_owner, Role.accountant}),
    "bank.view": frozenset({Role.firm_owner, Role.accountant, Role.junior_accountant, Role.api_key}),
    "bank.manage": frozenset({Role.firm_owner, Role.accountant, Role.api_key}),
    "reporting.manage": frozenset({Role.firm_owner, Role.accountant}),
}


# The scope an API key needs for a permission; permissions without an entry are never granted to keys.
SCOPE_FOR_PERMISSION: dict[str, str] = {
    "clients.view": "documents:read",
    "documents.view": "documents:read",
    "documents.upload": "documents:write",
    "documents.review": "documents:write",
    "documents.approve": "documents:write",
    "exports.view": "exports:read",
    "exports.create": "exports:write",
    "webhooks.manage": "webhooks:manage",
    "bank.view": "bank:read",
    "bank.manage": "bank:write",
}


def allowed(role: Role, permission: str) -> bool:
    roles = PERMISSIONS.get(permission)
    if roles is None:
        return False  # unknown permission: deny by default
    return role in roles


def any_allowed(role: Role, permissions: Iterable[str]) -> bool:
    return any(allowed(role, p) for p in permissions)
