"""Row-Level Security context (second line of defence behind the application filters).

Every tenant table has the policy ``firm_id = current_setting('app.firm_id')`` (or the platform
bypass ``app.bypass_rls = 'on'`` used only by ``super_admin`` code paths and by workers that walk
all firms). The settings are transaction-local (``set_config(..., true)``), so a pooled connection
never carries a tenant into the next request.
"""

from __future__ import annotations

import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def set_tenant(session: AsyncSession, firm_id: uuid.UUID | str) -> None:
    await session.execute(
        text(
            "SELECT set_config('app.firm_id', :fid, true), set_config('app.bypass_rls', 'off', true), "
            "set_config('app.client_scope', :scope, true)"
        ),
        {"fid": str(firm_id), "scope": session.info.get("client_scope", "")},
    )


async def set_client_scope(session: AsyncSession, client_ids: frozenset[uuid.UUID]) -> None:
    """rows of other client companies become invisible for the rest of the request
    (kept in ``session.info`` so later ``set_tenant`` calls in new transactions keep it)."""
    value = ",".join(sorted(str(c) for c in client_ids))
    session.info["client_scope"] = value
    await session.execute(text("SELECT set_config('app.client_scope', :scope, true)"), {"scope": value})


async def set_platform_bypass(session: AsyncSession) -> None:
    await session.execute(
        text("SELECT set_config('app.firm_id', '', true), set_config('app.bypass_rls', 'on', true)")
    )


async def clear_context(session: AsyncSession) -> None:
    await session.execute(
        text("SELECT set_config('app.firm_id', '', true), set_config('app.bypass_rls', 'off', true)")
    )


TENANT_TABLES = (
    "users",
    "user_sessions",
    "client_companies",
    "legal_entities",
    "documents",
    "audit_log",
    "license_events",
)


def policy_sql(table: str, column: str = "firm_id") -> list[str]:
    """DDL for one tenant table (used by the migration)."""
    cond = (
        f"({column} = NULLIF(current_setting('app.firm_id', true), '')::uuid "
        "OR current_setting('app.bypass_rls', true) = 'on')"
    )
    return [
        f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY",
        f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY",
        f"CREATE POLICY {table}_tenant_select ON {table} FOR SELECT USING {cond}",
        f"CREATE POLICY {table}_tenant_insert ON {table} FOR INSERT WITH CHECK {cond}",
        f"CREATE POLICY {table}_tenant_update ON {table} FOR UPDATE USING {cond} WITH CHECK {cond}",
        f"CREATE POLICY {table}_tenant_delete ON {table} FOR DELETE USING {cond}",
    ]


def append_only_policy_sql(table: str, column: str = "firm_id") -> list[str]:
    """Audit tables: select and insert within the tenant, never update or delete (no policy = denied)."""
    cond = (
        f"({column} IS NULL AND current_setting('app.bypass_rls', true) = 'on' "
        f"OR {column} = NULLIF(current_setting('app.firm_id', true), '')::uuid "
        "OR current_setting('app.bypass_rls', true) = 'on')"
    )
    return [
        f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY",
        f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY",
        f"CREATE POLICY {table}_tenant_select ON {table} FOR SELECT USING {cond}",
        f"CREATE POLICY {table}_tenant_insert ON {table} FOR INSERT WITH CHECK {cond}",
    ]
