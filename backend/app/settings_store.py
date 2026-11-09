"""Dashboard settings: typed sections, platform defaults, per-firm overrides.

Reads go through ``SettingsStore`` with its own short transactions under the platform bypass and a
small cache, so a request never has to switch its own tenant context to read a platform default.
Writes happen in the caller's transaction (with its audit row) and invalidate the cache.

Effective value of a section for a firm: the firm's own entry if it is enabled, else the platform's
entry, else nothing. Secrets are sealed with the owning row's data key and are never returned: the API
answers ``"set": true/false`` per secret field.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.db.rls import set_platform_bypass
from app.licensing.store import FirmSecrets
from app.models.config import ConfigEntry
from app.models.firm import Firm
from app.security.crypto import MasterKey

log = logging.getLogger("invoiceflow.settings_store")

CACHE_SECONDS = 30


@dataclass(frozen=True, slots=True)
class FieldSpec:
    name: str
    kind: str = "str"  # str | int | bool | secret
    default: Any = None
    max_len: int = 500


@dataclass(frozen=True, slots=True)
class SectionSpec:
    key: str
    fields: tuple[FieldSpec, ...]
    firm_overridable: bool = True
    required: tuple[str, ...] = ()

    def field(self, name: str) -> FieldSpec | None:
        return next((f for f in self.fields if f.name == name), None)

    @property
    def secret_names(self) -> tuple[str, ...]:
        return tuple(f.name for f in self.fields if f.kind == "secret")


SECTIONS: dict[str, SectionSpec] = {
    s.key: s
    for s in (
        SectionSpec(
            "smtp",
            (
                FieldSpec("host"),
                FieldSpec("port", "int", 587),
                FieldSpec("starttls", "bool", True),
                FieldSpec("user"),
                FieldSpec("password", "secret"),
                FieldSpec("from_address"),
                FieldSpec("from_name", default="InvoiceFlow"),
            ),
            required=("host", "from_address"),
        ),
        SectionSpec(
            "imap",
            (
                FieldSpec("host"),
                FieldSpec("port", "int", 993),
                FieldSpec("ssl", "bool", True),
                FieldSpec("user"),
                FieldSpec("password", "secret"),
                FieldSpec("folder", default="INBOX"),
            ),
            required=("host", "user"),
        ),
        SectionSpec("inbound_webhook", (FieldSpec("token", "secret"),), firm_overridable=False),
        SectionSpec(
            "ai",
            (
                FieldSpec("local_llm", default="auto"),
                FieldSpec("ollama_url"),
                FieldSpec("ollama_model"),
                FieldSpec("local_openai_base_url"),
                FieldSpec("local_openai_model"),
                FieldSpec("local_openai_api_key", "secret"),
                FieldSpec("local_openai_vision", "bool", True),
                FieldSpec("local_openai_thinking", "bool", False),
                FieldSpec("cloud_provider", default="auto"),
                FieldSpec("anthropic_api_key", "secret"),
                FieldSpec("cloud_model"),
                FieldSpec("openai_api_key", "secret"),
                FieldSpec("openai_model"),
                FieldSpec("mistral_api_key", "secret"),
                FieldSpec("cloud_openai_compat_base_url"),
                FieldSpec("cloud_openai_compat_api_key", "secret"),
                FieldSpec("cloud_openai_compat_model"),
                FieldSpec("cloud_vision", "bool", True),
            ),
            firm_overridable=False,
        ),
        SectionSpec(
            "backup",
            (
                FieldSpec("target", default="storage"),  # storage | s3
                FieldSpec("s3_endpoint"),
                FieldSpec("s3_bucket"),
                FieldSpec("s3_region", default="eu-central-1"),
                FieldSpec("s3_access_key"),
                FieldSpec("s3_secret_key", "secret"),
                FieldSpec("s3_prefix", default="invoiceflow-backups"),
                FieldSpec("object_lock_days", "int", 0),
                FieldSpec("schedule", default="daily"),  # off | daily | weekly
                FieldSpec("hour_utc", "int", 2),
                FieldSpec("keep_daily", "int", 7),
                FieldSpec("keep_weekly", "int", 4),
                FieldSpec("keep_monthly", "int", 12),
            ),
        ),
    )
}


class SettingsError(ValueError):
    pass


def coerce(spec: SectionSpec, values: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
    """Split and type-check an input dict into (plain, secrets). Unknown fields are refused."""
    plain: dict[str, Any] = {}
    secrets: dict[str, str] = {}
    for name, raw in values.items():
        f = spec.field(name)
        if f is None:
            raise SettingsError(f"unknown field '{name}' in '{spec.key}'")
        if raw is None:
            continue
        if f.kind == "secret":
            v = str(raw)
            if len(v) > 4096:
                raise SettingsError(f"'{name}' is too long")
            secrets[name] = v
        elif f.kind == "int":
            if isinstance(raw, bool) or not isinstance(raw, int | str):
                raise SettingsError(f"'{name}' must be a number")
            try:
                plain[name] = int(raw)
            except ValueError as e:
                raise SettingsError(f"'{name}' must be a number") from e
        elif f.kind == "bool":
            if not isinstance(raw, bool):
                raise SettingsError(f"'{name}' must be true or false")
            plain[name] = raw
        else:
            v = str(raw).strip()
            if len(v) > f.max_len:
                raise SettingsError(f"'{name}' is too long")
            plain[name] = v
    return plain, secrets


@dataclass(slots=True)
class Section:
    """An effective section: plain values with defaults applied, opened secrets, where it came from."""

    key: str
    values: dict[str, Any] = field(default_factory=dict)
    secrets: dict[str, str] = field(default_factory=dict)
    source: str = "none"  # firm | platform | none
    enabled: bool = False

    def get(self, name: str, default: Any = None) -> Any:
        if name in self.secrets:
            return self.secrets[name]
        v = self.values.get(name)
        return default if v in (None, "") else v

    def public(self) -> dict[str, Any]:
        spec = SECTIONS[self.key]
        out: dict[str, Any] = {}
        for f in spec.fields:
            out[f.name] = {"set": f.name in self.secrets} if f.kind == "secret" else self.values.get(f.name)
        return out


class SettingsStore:
    def __init__(
        self, master: MasterKey, sessionmaker: async_sessionmaker[AsyncSession] | None = None
    ) -> None:
        self.master = master
        self._sessionmaker = sessionmaker
        self._cache: dict[tuple[str, str], tuple[float, Section | None]] = {}

    def _sm(self) -> async_sessionmaker[AsyncSession]:
        if self._sessionmaker is None:
            from app.db.session import get_sessionmaker

            return get_sessionmaker()
        return self._sessionmaker

    def invalidate(self) -> None:
        self._cache.clear()

    # ---- reading -----------------------------------------------------------------------------
    def _open(self, firm: Firm, entry: ConfigEntry) -> Section:
        spec = SECTIONS[entry.key]
        values = {f.name: f.default for f in spec.fields if f.kind != "secret"}
        values.update({k: v for k, v in (entry.value or {}).items() if k != "_enabled"})
        secrets: dict[str, str] = {}
        if entry.secrets_enc:
            opened = FirmSecrets(self.master, firm).open(f"config:{entry.key}", entry.secrets_enc)
            if opened:
                try:
                    secrets = {k: str(v) for k, v in json.loads(opened).items()}
                except ValueError:
                    secrets = {}
        enabled = (entry.value or {}).get("_enabled", True) is not False
        return Section(entry.key, values, secrets, "platform" if firm.is_platform else "firm", enabled)

    async def _load(self, firm_id: uuid.UUID, key: str) -> Section | None:
        async with self._sm()() as session:
            async with session.begin():
                await set_platform_bypass(session)
                firm = await session.get(Firm, firm_id)
                if firm is None:
                    return None
                entry = (
                    await session.execute(
                        select(ConfigEntry).where(ConfigEntry.firm_id == firm_id, ConfigEntry.key == key)
                    )
                ).scalar_one_or_none()
                return self._open(firm, entry) if entry is not None else None

    async def _cached(self, firm_id: uuid.UUID, key: str) -> Section | None:
        ck = (str(firm_id), key)
        hit = self._cache.get(ck)
        now = time.monotonic()
        if hit is not None and now - hit[0] < CACHE_SECONDS:
            return hit[1]
        sec = await self._load(firm_id, key)
        self._cache[ck] = (now, sec)
        return sec

    async def platform_firm_id(self) -> uuid.UUID | None:
        hit = self._cache.get(("platform", "_id"))
        now = time.monotonic()
        if hit is not None and now - hit[0] < CACHE_SECONDS and hit[1] is not None:
            return uuid.UUID(hit[1].values["id"])
        async with self._sm()() as session:
            async with session.begin():
                await set_platform_bypass(session)
                pid = (
                    await session.execute(select(Firm.id).where(Firm.kind == "platform"))
                ).scalar_one_or_none()
        if pid is not None:
            self._cache[("platform", "_id")] = (now, Section("_id", {"id": str(pid)}))
        return pid

    async def effective(self, key: str, firm_id: uuid.UUID | None) -> Section:
        """The firm's own enabled section, else the platform's, else an empty one."""
        spec = SECTIONS[key]
        if firm_id is not None and spec.firm_overridable:
            own = await self._cached(firm_id, key)
            if own is not None and own.enabled:
                return own
        pid = await self.platform_firm_id()
        if pid is not None:
            plat = await self._cached(pid, key)
            if plat is not None and plat.enabled:
                return plat
        return Section(key, {f.name: f.default for f in spec.fields if f.kind != "secret"})

    async def own(self, key: str, firm_id: uuid.UUID) -> Section | None:
        return await self._cached(firm_id, key)

    async def own_in(self, session: AsyncSession, firm: Firm, key: str) -> Section | None:
        """The firm's (or platform's) own section read in the caller's transaction (sees its writes)."""
        entry = (
            await session.execute(
                select(ConfigEntry).where(ConfigEntry.firm_id == firm.id, ConfigEntry.key == key)
            )
        ).scalar_one_or_none()
        return self._open(firm, entry) if entry is not None else None

    # ---- writing (caller's transaction) --------------------------------------------------------
    async def write(
        self,
        session: AsyncSession,
        firm: Firm,
        key: str,
        values: dict[str, Any],
        *,
        enabled: bool = True,
        clear_secrets: tuple[str, ...] = (),
        user_id: uuid.UUID | None = None,
        source: str = "dashboard",
    ) -> ConfigEntry:
        spec = SECTIONS.get(key)
        if spec is None:
            raise SettingsError(f"unknown section '{key}'")
        if not firm.is_platform and not spec.firm_overridable:
            raise SettingsError(f"'{key}' is a platform setting")
        plain, new_secrets = coerce(spec, values)
        entry = (
            await session.execute(
                select(ConfigEntry).where(ConfigEntry.firm_id == firm.id, ConfigEntry.key == key)
            )
        ).scalar_one_or_none()
        sec = FirmSecrets(self.master, firm)
        old_secrets: dict[str, str] = {}
        if entry is not None and entry.secrets_enc:
            opened = sec.open(f"config:{key}", entry.secrets_enc)
            old_secrets = json.loads(opened) if opened else {}
        merged_secrets = {k: v for k, v in old_secrets.items() if k not in clear_secrets}
        merged_secrets.update({k: v for k, v in new_secrets.items() if v != ""})
        for k in [k for k, v in new_secrets.items() if v == ""]:
            merged_secrets.pop(k, None)
        value = dict(entry.value or {}) if entry is not None else {}
        value.update(plain)
        value["_enabled"] = enabled
        if entry is None:
            entry = ConfigEntry(firm_id=firm.id, key=key)
            session.add(entry)
        entry.value = value
        entry.secrets_enc = (
            sec.seal(f"config:{key}", json.dumps(merged_secrets, sort_keys=True)) if merged_secrets else None
        )
        entry.source = source
        entry.updated_by_user_id = user_id
        await session.flush()
        self.invalidate()
        return entry

    async def delete(self, session: AsyncSession, firm: Firm, key: str) -> bool:
        entry = (
            await session.execute(
                select(ConfigEntry).where(ConfigEntry.firm_id == firm.id, ConfigEntry.key == key)
            )
        ).scalar_one_or_none()
        if entry is None:
            return False
        await session.delete(entry)
        await session.flush()
        self.invalidate()
        return True


ENV_IMPORTS: dict[str, dict[str, str]] = {
    "smtp": {
        "host": "smtp_host",
        "port": "smtp_port",
        "starttls": "smtp_starttls",
        "user": "smtp_user",
        "password": "smtp_password",
        "from_address": "smtp_from_address",
    },
    "imap": {
        "host": "imap_host",
        "port": "imap_port",
        "ssl": "imap_ssl",
        "user": "imap_user",
        "password": "imap_password",
        "folder": "imap_folder",
    },
    "inbound_webhook": {"token": "inbound_email_webhook_token"},
    "ai": {
        "local_llm": "local_llm",
        "ollama_url": "ollama_url",
        "ollama_model": "ollama_model",
        "local_openai_base_url": "local_openai_base_url",
        "local_openai_model": "local_openai_model",
        "local_openai_api_key": "local_openai_api_key",
        "local_openai_vision": "local_openai_vision",
        "local_openai_thinking": "local_openai_thinking",
        "cloud_provider": "cloud_provider",
        "anthropic_api_key": "anthropic_api_key",
        "cloud_model": "cloud_model",
        "openai_api_key": "openai_api_key",
        "openai_model": "openai_model",
        "mistral_api_key": "mistral_api_key",
        "cloud_openai_compat_base_url": "cloud_openai_compat_base_url",
        "cloud_openai_compat_api_key": "cloud_openai_compat_api_key",
        "cloud_openai_compat_model": "cloud_openai_compat_model",
        "cloud_vision": "cloud_vision",
    },
}

# the section is imported only when its trigger variable is set
ENV_TRIGGERS = {
    "smtp": "smtp_host",
    "imap": "imap_host",
    "inbound_webhook": "inbound_email_webhook_token",
    "ai": "_any",
}


async def import_environment(
    store: SettingsStore, session: AsyncSession, platform: Firm, settings: Settings
) -> list[str]:
    """Once per section: copy the environment's values into the platform settings (shown as
    "imported from environment"). A section that already exists is never overwritten."""
    imported: list[str] = []
    for key, mapping in ENV_IMPORTS.items():
        exists = (
            await session.execute(
                select(ConfigEntry.id).where(ConfigEntry.firm_id == platform.id, ConfigEntry.key == key)
            )
        ).first()
        if exists:
            continue
        trigger = ENV_TRIGGERS[key]
        values = {name: getattr(settings, attr) for name, attr in mapping.items()}
        if trigger == "_any":
            fields = Settings.model_fields
            changed = {
                n: v for n, v in values.items() if v not in (None, "") and v != fields[mapping[n]].default
            }
            if not changed:
                continue
            values = changed
        elif not getattr(settings, trigger):
            continue
        await store.write(
            session,
            platform,
            key,
            {k: v for k, v in values.items() if v not in (None, "")},
            source="environment",
        )
        imported.append(key)
    if imported:
        log.info("settings imported from the environment: %s", ", ".join(imported))
    return imported
