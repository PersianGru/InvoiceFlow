from __future__ import annotations

import uuid
from dataclasses import dataclass

from app.models.user import Role, User


@dataclass(frozen=True, slots=True)
class Actor:
    user_id: uuid.UUID
    role: Role
    firm_id: uuid.UUID | None
    client_company_id: uuid.UUID | None
    email: str
    ip: str | None = None
    api_key_id: uuid.UUID | None = None  # set when the principal is a public-API key
    scopes: frozenset[str] = frozenset()
    client_scope: frozenset[uuid.UUID] = frozenset()  # API key limited to these client companies (F)

    @classmethod
    def from_user(cls, user: User, ip: str | None = None, acting_firm_id: uuid.UUID | None = None) -> Actor:
        firm_id = acting_firm_id if user.role == Role.super_admin else user.firm_id
        return cls(user.id, user.role, firm_id, user.client_company_id, user.email, ip)

    @property
    def is_platform_admin(self) -> bool:
        return self.role == Role.super_admin

    @property
    def is_client(self) -> bool:
        return self.role in (Role.client_admin, Role.client_user)

    @property
    def is_api_key(self) -> bool:
        return self.api_key_id is not None

    def scoped_client_ids(self) -> frozenset[uuid.UUID] | None:
        """The client companies this principal may see, or None for the whole firm."""
        if self.is_client:
            return frozenset({self.client_company_id}) if self.client_company_id else frozenset()
        return self.client_scope or None

    def in_scope(self, client_company_id: uuid.UUID | None) -> bool:
        ids = self.scoped_client_ids()
        return ids is None or (client_company_id is not None and client_company_id in ids)

    def has_scope(self, scope: str) -> bool:
        return not self.is_api_key or scope in self.scopes
