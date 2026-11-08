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

    @classmethod
    def from_user(cls, user: User, ip: str | None = None) -> Actor:
        return cls(user.id, user.role, user.firm_id, user.client_company_id, user.email, ip)

    @property
    def is_platform_admin(self) -> bool:
        return self.role == Role.super_admin

    @property
    def is_client(self) -> bool:
        return self.role in (Role.client_admin, Role.client_user)

    @property
    def is_api_key(self) -> bool:
        return self.api_key_id is not None

    def has_scope(self, scope: str) -> bool:
        return not self.is_api_key or scope in self.scopes
