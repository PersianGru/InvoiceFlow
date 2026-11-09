"""Firm and platform backups and restores.

Archive (before age encryption): a gzip tar with

* ``manifest.json``: format, kind, owner id and name, installation id, schema revision, app version,
  row counts, the owner's raw data key (base64; the archive itself is the protection) and, for a
  platform backup, the firm directory;
* ``tables/<table>.jsonl``: one ``row_to_json`` line per row, in foreign-key order;
* ``files/<storage key>``: every stored object the rows point to (``storage_key`` columns);
* ``SHA256SUMS``: a hash per member, checked before anything is restored.

Restores insert with an explicit column list, so a backup from an older schema gets the defaults of
newer columns; a backup from a newer schema is refused.
"""

from __future__ import annotations

import base64
import gzip
import hashlib
import io
import json
import logging
import shutil
import tarfile
import tempfile
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.backup import crypto
from app.config import Settings
from app.db.rls import set_platform_bypass
from app.licensing.store import FirmSecrets
from app.models.backup import Backup, RecoveryKey, Restore
from app.models.firm import Firm
from app.security.crypto import MasterKey
from app.settings_store import Section, SettingsStore
from app.storage.base import Storage

log = logging.getLogger("invoiceflow.backup")

FORMAT = "invoiceflow-backup/1"
MAX_REVEALS = 5
# never in a firm backup: live sessions and one-time links are useless after a restore
SKIP_TABLES = frozenset({"user_sessions", "user_links", "idempotency_keys"})
PLATFORM_TABLES = ("config_entries", "inbound_domains", "channel_bots", "recovery_keys")


class BackupError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


# ---- bookkeeping used by other packages --------------------------------------------------------------
async def has_recent_backup(session: AsyncSession, firm_id: uuid.UUID, max_age: timedelta) -> bool:
    row = (
        await session.execute(
            select(Backup.id)
            .where(
                Backup.owner_id == firm_id,
                Backup.status == "done",
                Backup.deleted_at.is_(None),
                Backup.finished_at >= datetime.now(UTC) - max_age,
            )
            .limit(1)
        )
    ).first()
    return row is not None


async def last_backups(session: AsyncSession) -> dict[uuid.UUID, str]:
    rows = (
        await session.execute(
            select(Backup.owner_id, Backup.finished_at)
            .where(Backup.status == "done", Backup.deleted_at.is_(None))
            .order_by(Backup.finished_at)
        )
    ).all()
    return {owner: at.isoformat() for owner, at in rows if at is not None}


# ---- schema introspection ------------------------------------------------------------------------------
async def firm_tables(session: AsyncSession) -> list[str]:
    """Every table with a ``firm_id`` column, parents before children (foreign keys)."""
    names = (
        set(
            (
                await session.execute(
                    text(
                        "SELECT table_name FROM information_schema.columns "
                        "WHERE table_schema = 'public' AND column_name = 'firm_id'"
                    )
                )
            ).scalars()
        )
        - SKIP_TABLES
    )
    deps = (
        await session.execute(
            text(
                # pg_catalog: information_schema hides constraints of tables the app role does not own
                "SELECT conrelid::regclass::text, confrelid::regclass::text FROM pg_constraint "
                "WHERE contype = 'f' AND connamespace = 'public'::regnamespace"
            )
        )
    ).all()
    graph: dict[str, set[str]] = {n: set() for n in names}
    for child, parent in deps:
        if child in graph and parent in graph and child != parent:
            graph[child].add(parent)
    ordered: list[str] = []
    while graph:
        ready = sorted(n for n, parents in graph.items() if not parents)
        if not ready:  # a cycle: keep a stable order, restores defer nothing so this must not happen
            ready = sorted(graph)
        for n in ready:
            ordered.append(n)
            graph.pop(n)
        for parents in graph.values():
            parents.difference_update(ready)
    return ordered


async def table_columns(session: AsyncSession, table: str) -> dict[str, str | None]:
    rows = (
        await session.execute(
            text(
                "SELECT column_name, column_default FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = :t"
            ),
            {"t": table},
        )
    ).all()
    return {name: default for name, default in rows}


def _serial(columns: dict[str, str | None]) -> bool:
    d = columns.get("id")
    return bool(d and d.startswith("nextval("))


async def schema_revision(session: AsyncSession) -> str:
    try:
        return str((await session.execute(text("SELECT version_num FROM alembic_version"))).scalar() or "")
    except Exception:  # noqa: BLE001 - the app role may not read it; the code revision is the fallback
        return CODE_REVISION


CODE_REVISION = "0009"


# ---- targets ---------------------------------------------------------------------------------------------
class Target:
    """Where encrypted archives live: the document storage (local or its S3/MinIO bucket) or an
    S3-compatible target from the dashboard."""

    def __init__(self, storage: Storage, section: Section | None) -> None:
        self.storage = storage
        self.section = section
        self.kind = "s3" if section is not None and section.get("target") == "s3" else "storage"

    def _s3(self) -> Any:
        import boto3

        assert self.section is not None
        return boto3.client(
            "s3",
            endpoint_url=self.section.get("s3_endpoint") or None,
            region_name=self.section.get("s3_region") or None,
            aws_access_key_id=self.section.get("s3_access_key") or None,
            aws_secret_access_key=self.section.get("s3_secret_key") or None,
        )

    def put(self, key: str, path: Path) -> None:
        if self.kind == "s3":
            assert self.section is not None
            extra: dict[str, Any] = {}
            days = int(self.section.get("object_lock_days", 0) or 0)
            if days > 0:
                extra = {
                    "ObjectLockMode": "GOVERNANCE",
                    "ObjectLockRetainUntilDate": datetime.now(UTC) + timedelta(days=days),
                }
            self._s3().upload_file(
                str(path), str(self.section.get("s3_bucket")), key, ExtraArgs=extra or None
            )
            return
        st: Any = self.storage
        if hasattr(st, "_client") and hasattr(st, "bucket"):
            st._client.upload_file(str(path), st.bucket, key)
        elif hasattr(st, "_path"):
            dest = st._path(key)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, dest)
        else:  # pragma: no cover - other stores: small archives through the byte API
            raise BackupError("target", "This storage backend cannot hold backups.")

    def get(self, key: str, path: Path) -> None:
        if self.kind == "s3":
            assert self.section is not None
            self._s3().download_file(str(self.section.get("s3_bucket")), key, str(path))
            return
        st: Any = self.storage
        if hasattr(st, "_client") and hasattr(st, "bucket"):
            st._client.download_file(st.bucket, key, str(path))
        else:
            shutil.copyfile(st._path(key), path)

    def delete(self, key: str) -> None:
        try:
            if self.kind == "s3":
                assert self.section is not None
                self._s3().delete_object(Bucket=str(self.section.get("s3_bucket")), Key=key)
                return
            st: Any = self.storage
            if hasattr(st, "_client") and hasattr(st, "bucket"):
                st._client.delete_object(Bucket=st.bucket, Key=key)
            else:
                st._path(key).unlink(missing_ok=True)
        except Exception as e:  # noqa: BLE001 - object lock or already gone: the row records the attempt
            log.warning("backup object not deleted: %s", type(e).__name__)


async def target_for(store: SettingsStore, storage: Storage, owner: Firm) -> Target:
    section = await store.effective("backup", None if owner.is_platform else owner.id)
    return Target(storage, section if section.get("target") == "s3" and section.get("s3_bucket") else None)


# ---- making a backup -------------------------------------------------------------------------------------
@dataclass(slots=True)
class Deps:
    sessionmaker: async_sessionmaker[AsyncSession]
    storage: Storage
    store: SettingsStore
    master: MasterKey
    settings: Settings


def _add(tar: tarfile.TarFile, name: str, data: bytes, sums: list[str]) -> None:
    info = tarfile.TarInfo(name)
    info.size = len(data)
    info.mtime = int(datetime.now(UTC).timestamp())
    tar.addfile(info, io.BytesIO(data))
    sums.append(f"{hashlib.sha256(data).hexdigest()}  {name}")


async def _dump_rows(session: AsyncSession, table: str, where: str, params: dict[str, Any]) -> Iterator[str]:
    result = await session.stream(
        text(f"SELECT row_to_json(t)::text FROM {table} t WHERE {where}"), params  # noqa: S608
    )  # table names come from information_schema
    lines = []
    async for (line,) in result:
        lines.append(line)
    return iter(lines)


async def _collect(session: AsyncSession, owner: Firm, kind: str) -> tuple[dict[str, list[str]], list[str]]:
    """(rows per table, storage keys to copy)."""
    tables: dict[str, list[str]] = {}
    keys: list[str] = []
    if kind == "firm":
        tables["firms"] = list(await _dump_rows(session, "firms", "id = :f", {"f": owner.id}))
        for t in await firm_tables(session):
            if t == "firms":
                continue
            rows = list(await _dump_rows(session, t, "firm_id = :f", {"f": owner.id}))
            tables[t] = rows
            cols = await table_columns(session, t)
            if "storage_key" in cols and rows:
                for line in rows:
                    k = json.loads(line).get("storage_key")
                    if k:
                        keys.append(str(k))
    else:
        tables["firms"] = list(await _dump_rows(session, "firms", "id = :f", {"f": owner.id}))
        tables["platform_state"] = list(await _dump_rows(session, "platform_state", "true", {}))
        tables["users"] = list(await _dump_rows(session, "users", "firm_id IS NULL", {}))
        for t in PLATFORM_TABLES:
            col = "owner_id" if t == "recovery_keys" else "firm_id"
            tables[t] = list(await _dump_rows(session, t, f"{col} = :f", {"f": owner.id}))
        tables["audit_log"] = list(await _dump_rows(session, "audit_log", "firm_id IS NULL", {}))
        tables["channel_messages"] = list(
            await _dump_rows(
                session, "channel_messages", "firm_id IS NULL AND created_at > now() - interval '30 days'", {}
            )
        )
    return tables, keys


async def _firm_directory(session: AsyncSession) -> list[dict[str, Any]]:
    from app.domains import firm_domains_summary
    from app.models.user import Role, User

    domains = await firm_domains_summary(session)
    out = []
    for f in (await session.execute(select(Firm).where(Firm.kind == "firm"))).scalars():
        owners = (
            (
                await session.execute(
                    select(User.email).where(
                        User.firm_id == f.id, User.role == Role.firm_owner, User.deleted_at.is_(None)
                    )
                )
            )
            .scalars()
            .all()
        )
        out.append(
            {
                "id": str(f.id),
                "name": f.name,
                "country": f.country,
                "instance_id": f.instance_id,
                "is_active": f.is_active,
                "domain": domains.get(f.id),
                "owners": list(owners),
            }
        )
    return out


async def run_backup(deps: Deps, backup_id: uuid.UUID) -> dict[str, Any]:
    """The job (backup container): dump, pack, encrypt, store. Never holds the archive in memory."""
    async with deps.sessionmaker() as session:
        async with session.begin():
            await set_platform_bypass(session)
            b = await session.get(Backup, backup_id)
            if b is None or b.status not in ("queued", "failed"):
                return {"status": "skipped"}
            b.status = "running"
    work = Path(tempfile.mkdtemp(prefix="ivf-backup-"))
    try:
        async with deps.sessionmaker() as session:
            async with session.begin():
                await set_platform_bypass(session)
                b = await session.get(Backup, backup_id)
                assert b is not None
                owner = await session.get(Firm, b.owner_id)
                if owner is None:
                    raise BackupError("no_owner", "The firm no longer exists.")
                tables, keys = await _collect(session, owner, b.kind)
                data_key = deps.master.unwrap(owner.data_key_wrapped, str(owner.id))
                manifest: dict[str, Any] = {
                    "format": FORMAT,
                    "kind": b.kind,
                    "backup_id": str(b.id),
                    "owner_id": str(owner.id),
                    "owner_name": owner.name,
                    "instance_id": owner.instance_id,
                    "created_at": datetime.now(UTC).isoformat(),
                    "app_version": deps.settings.app_version,
                    "schema_revision": await schema_revision(session),
                    "tables": {t: len(r) for t, r in tables.items()},
                    "table_order": list(tables),
                    "files": len(keys),
                    "data_key": base64.b64encode(data_key).decode(),
                }
                if b.kind == "platform":
                    manifest["firm_directory"] = await _firm_directory(session)
                password = crypto.new_password()
                b.password_enc = FirmSecrets(deps.master, owner).seal(f"backup:{b.id}", password)
                rk = (
                    await session.execute(
                        select(RecoveryKey).where(
                            RecoveryKey.owner_id == owner.id, RecoveryKey.active.is_(True)
                        )
                    )
                ).scalar_one_or_none()
                b.recovery_key_id = rk.id if rk is not None else None
                target = await target_for(deps.store, deps.storage, owner)
        plain = work / "backup.tar.gz"
        sums: list[str] = []
        with gzip.open(plain, "wb", compresslevel=6) as gz, tarfile.open(fileobj=gz, mode="w") as tar:
            _add(tar, "manifest.json", json.dumps(manifest, indent=1).encode(), sums)
            for t, rows in tables.items():
                _add(tar, f"tables/{t}.jsonl", ("\n".join(rows) + ("\n" if rows else "")).encode(), sums)
            for k in keys:
                try:
                    _add(tar, f"files/{k}", await deps.storage.get(k), sums)
                except Exception as e:  # noqa: BLE001 - a missing object is listed, not fatal
                    log.warning("backup: object missing: %s", type(e).__name__)
                    sums.append(f"missing  files/{k}")
            _add(tar, "SHA256SUMS", ("\n".join(sums) + "\n").encode(), [])
        recipients = [crypto.password_identity(password, crypto.PASSWORD_SALT).to_public()]
        if rk is not None:
            recipients.append(crypto.recipient(rk.public_key))
        enc = work / "backup.tar.gz.age"
        crypto.encrypt_file(plain, enc, recipients)
        digest = hashlib.sha256(enc.read_bytes()).hexdigest()
        key = f"backups/{owner.id}/{datetime.now(UTC):%Y%m%dT%H%M%S}-{backup_id.hex[:8]}.tar.gz.age"
        target.put(key, enc)
        async with deps.sessionmaker() as session:
            async with session.begin():
                await set_platform_bypass(session)
                b = await session.get(Backup, backup_id)
                assert b is not None
                b.status, b.object_key, b.size_bytes, b.sha256 = "done", key, enc.stat().st_size, digest
                b.target, b.schema_revision, b.app_version = (
                    target.kind,
                    manifest["schema_revision"],
                    deps.settings.app_version,
                )
                b.counts = {"tables": manifest["tables"], "files": len(keys)}
                b.finished_at = datetime.now(UTC)
                await _audit(
                    session, b, "backup.done", {"size": b.size_bytes, "files": len(keys), "target": b.target}
                )
        return {"status": "done", "size": enc.stat().st_size}
    except Exception as e:
        log.exception("backup %s failed", backup_id)
        async with deps.sessionmaker() as session:
            async with session.begin():
                await set_platform_bypass(session)
                b = await session.get(Backup, backup_id)
                if b is not None:
                    b.status, b.error, b.finished_at = (
                        "failed",
                        str(getattr(e, "code", type(e).__name__))[:500],
                        datetime.now(UTC),
                    )
                    await _audit(session, b, "backup.failed", {"error": b.error})
        return {"status": "failed"}
    finally:
        shutil.rmtree(work, ignore_errors=True)


async def _audit(session: AsyncSession, b: Backup, action: str, after: dict[str, Any]) -> None:
    from app.audit.service import record_audit

    await record_audit(
        session,
        firm_id=b.owner_id if b.kind == "firm" else None,
        actor=None,
        action=action,
        target_type="backup",
        target_id=str(b.id),
        after=after,
    )


# ---- opening an archive -------------------------------------------------------------------------------
def open_archive(
    enc: Path, work: Path, *, password: str | None, backup_id: str | None, recovery_key: str | None
) -> Path:
    """Decrypt and unpack into ``work``; returns the directory. Hashes are checked."""
    plain = work / "restore.tar.gz"
    try:
        if recovery_key:
            crypto.decrypt_file(enc, plain, crypto.identity(recovery_key))
        else:
            if not password:
                raise BackupError("password_required", "Enter the backup password or the recovery key.")
            crypto.decrypt_file(enc, plain, crypto.password_identity(password, crypto.PASSWORD_SALT))
    except BackupError:
        raise
    except Exception as e:
        raise BackupError("wrong_password", "The password or recovery key does not open this backup.") from e
    out = work / "content"
    out.mkdir()
    with tarfile.open(plain, mode="r:gz") as tar:
        for m in tar.getmembers():
            if not m.isfile() or m.name.startswith(("/", "..")) or ".." in Path(m.name).parts:
                raise BackupError("bad_archive", "The archive contains an unexpected entry.")
        tar.extractall(out, filter="data")
    sums = (out / "SHA256SUMS").read_text(encoding="utf-8").splitlines()
    for line in sums:
        digest, _, name = line.partition("  ")
        if digest == "missing" or not name:
            continue
        if hashlib.sha256((out / name).read_bytes()).hexdigest() != digest:
            raise BackupError("hash_mismatch", f"The archive is damaged ({name}).")
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("format") != FORMAT:
        raise BackupError("bad_archive", "This is not an InvoiceFlow backup.")
    return out


# ---- restoring ---------------------------------------------------------------------------------------------
async def _insert_rows(session: AsyncSession, table: str, lines: list[str]) -> int:
    cols = await table_columns(session, table)
    drop_id = _serial(cols)
    n = 0
    for line in lines:
        row = json.loads(line)
        keys = [k for k in row if k in cols and not (drop_id and k == "id")]
        if not keys:
            continue
        payload = json.dumps({k: row[k] for k in keys})
        col_list = ", ".join(f'"{k}"' for k in keys)
        sql = (
            f"INSERT INTO {table} ({col_list}) SELECT {col_list} "  # noqa: S608 - names from information_schema
            f"FROM json_populate_record(NULL::{table}, CAST(:r AS json))"
        )
        await session.execute(
            text(sql),
            {"r": payload},
        )
        n += 1
    return n


def _revision_newer(backup_rev: str, current: str) -> bool:
    try:
        return int(backup_rev) > int(current)
    except ValueError:
        return False


async def restore_firm(
    deps: Deps, content: Path, *, target_firm_id: uuid.UUID, replace: bool
) -> dict[str, Any]:
    """Restore a firm backup into this installation with the same firm id and installation id."""
    manifest = json.loads((content / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("kind") != "firm":
        raise BackupError("wrong_kind", "This is not a firm backup.")
    if manifest.get("owner_id") != str(target_firm_id):
        raise BackupError("wrong_firm", "This backup belongs to another firm.")
    counts: dict[str, int] = {}
    async with deps.sessionmaker() as session:
        async with session.begin():
            await set_platform_bypass(session)
            if _revision_newer(str(manifest.get("schema_revision")), await schema_revision(session)):
                raise BackupError(
                    "newer_backup", "This backup comes from a newer InvoiceFlow version. Update first."
                )
            existing = await session.get(Firm, target_firm_id)
            if existing is not None:
                if not replace and not (existing.settings or {}).get("restore_pending"):
                    raise BackupError("firm_exists", "This firm exists. Restore over it explicitly.")
                await session.execute(text("DELETE FROM firms WHERE id = :f"), {"f": target_firm_id})
                await session.flush()
            order = manifest.get("table_order") or ["firms"]
            for t in order:
                f = content / "tables" / f"{t}.jsonl"
                lines = (
                    [x for x in f.read_text(encoding="utf-8").splitlines() if x.strip()] if f.exists() else []
                )
                counts[t] = await _insert_rows(session, t, lines)
            firm = await session.get(Firm, target_firm_id)
            if firm is None:
                raise BackupError("bad_archive", "The firm row is missing from the backup.")
            data_key = base64.b64decode(manifest["data_key"])
            firm.data_key_wrapped = deps.master.wrap(data_key, str(firm.id))
            firm.settings = {k: v for k, v in (firm.settings or {}).items() if k != "restore_pending"}
    files = 0
    root = content / "files"
    if root.exists():
        for p in root.rglob("*"):
            if p.is_file():
                await deps.storage.put(p.relative_to(root).as_posix(), p.read_bytes())
                files += 1
    return {"tables": counts, "files": files}


async def restore_platform(deps: Deps, content: Path) -> dict[str, Any]:
    """A platform backup on a fresh installation: platform row, settings, domains, bots, super admin,
    platform audit; firms from the directory as placeholders waiting for their own backups."""
    manifest = json.loads((content / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("kind") != "platform":
        raise BackupError("wrong_kind", "This is not a platform backup.")
    counts: dict[str, int] = {}
    async with deps.sessionmaker() as session:
        async with session.begin():
            await set_platform_bypass(session)
            from app.platform import super_admin_exists

            if await super_admin_exists(session):
                raise BackupError("not_fresh", "This installation is already set up.")
            await session.execute(text("DELETE FROM firms WHERE kind = 'platform'"))
            await session.execute(text("DELETE FROM platform_state"))
            for t in manifest.get("table_order") or []:
                f = content / "tables" / f"{t}.jsonl"
                lines = (
                    [x for x in f.read_text(encoding="utf-8").splitlines() if x.strip()] if f.exists() else []
                )
                counts[t] = await _insert_rows(session, t, lines)
            platform = await session.get(Firm, uuid.UUID(manifest["owner_id"]))
            assert platform is not None
            platform.data_key_wrapped = deps.master.wrap(
                base64.b64decode(manifest["data_key"]), str(platform.id)
            )
            placeholders = 0
            for entry in manifest.get("firm_directory") or []:
                fid = uuid.UUID(entry["id"])
                if await session.get(Firm, fid) is not None:
                    continue
                ph = Firm(
                    id=fid,
                    name=entry["name"],
                    country=entry.get("country") or "DE",
                    kind="firm",
                    is_active=False,
                    settings={
                        "restore_pending": True,
                        "owners": entry.get("owners", []),
                        "domain": entry.get("domain"),
                    },
                )
                ph.data_key_wrapped = deps.master.wrap(deps.master.new_data_key(), str(fid))
                ph.instance_id, ph.instance_fp, ph.instance_fp_source = (
                    entry["instance_id"],
                    "00000000",
                    "restore",
                )
                session.add(ph)
                placeholders += 1
            counts["placeholders"] = placeholders
    return {"tables": counts}


async def run_restore(deps: Deps, restore_id: uuid.UUID) -> dict[str, Any]:
    async with deps.sessionmaker() as session:
        async with session.begin():
            await set_platform_bypass(session)
            r = await session.get(Restore, restore_id)
            if r is None or r.status != "queued":
                return {"status": "skipped"}
            r.status = "running"
            owner = await session.get(Firm, r.owner_id)
            b = await session.get(Backup, r.backup_id) if r.backup_id else None
            pre = await session.get(Backup, r.pre_backup_id) if r.pre_backup_id else None
            from app.platform import get_platform

            secret_holder = owner if owner is not None else await get_platform(session)
            password = None
            if r.secret_enc and secret_holder is not None:
                password = FirmSecrets(deps.master, secret_holder).open(f"restore:{r.id}", r.secret_enc)
            target = (
                await target_for(deps.store, deps.storage, owner)
                if owner is not None
                else Target(deps.storage, None)
            )
    work = Path(tempfile.mkdtemp(prefix="ivf-restore-"))
    try:
        if pre is not None and pre.status in ("queued", "failed"):
            # the current state is saved first; a failed safety backup stops the restore
            if (await run_backup(deps, pre.id)).get("status") != "done":
                raise BackupError("pre_backup_failed", "The safety backup before the restore failed.")
        enc = work / "in.age"
        if r.upload_key:
            enc.write_bytes(await deps.storage.get(r.upload_key))
        elif b is not None:
            target.get(b.object_key, enc)
        else:
            raise BackupError("no_source", "Nothing to restore from.")
        secret = json.loads(password) if password else {}
        content = open_archive(
            enc,
            work,
            password=secret.get("password"),
            backup_id=secret.get("backup_id") or (str(b.id) if b is not None else None),
            recovery_key=secret.get("recovery_key"),
        )
        result = await restore_firm(deps, content, target_firm_id=r.owner_id, replace=r.mode == "replace")
        async with deps.sessionmaker() as session:
            async with session.begin():
                await set_platform_bypass(session)
                row = await session.get(Restore, restore_id)
                if row is not None:
                    row.status, row.finished_at, row.counts, row.secret_enc = (
                        "done",
                        datetime.now(UTC),
                        result,
                        None,
                    )
                from app.audit.service import record_audit

                await record_audit(
                    session,
                    firm_id=r.owner_id,
                    actor=None,
                    action="firm.restore_done",
                    target_type="firm",
                    target_id=str(r.owner_id),
                    after={
                        "restore_id": str(restore_id),
                        "backup_id": str(r.backup_id) if r.backup_id else None,
                        "reason": r.reason,
                        **result,
                    },
                )
        if r.upload_key:
            await deps.storage.delete(r.upload_key)
        return {"status": "done", **result}
    except Exception as e:
        log.warning("restore %s failed: %s %s", restore_id, type(e).__name__, str(e)[:400])
        async with deps.sessionmaker() as session:
            async with session.begin():
                await set_platform_bypass(session)
                row = await session.get(Restore, restore_id)
                if row is not None:
                    row.status, row.finished_at, row.secret_enc = "failed", datetime.now(UTC), None
                    row.error = str(getattr(e, "code", type(e).__name__)) + ": " + str(e)[:300]
        return {"status": "failed", "error": getattr(e, "code", type(e).__name__), "message": str(e)[:300]}
    finally:
        shutil.rmtree(work, ignore_errors=True)


# ---- schedule and retention -------------------------------------------------------------------------------
def keep_set(
    finished: list[tuple[uuid.UUID, datetime]], daily: int, weekly: int, monthly: int
) -> set[uuid.UUID]:
    """Grandfather-father-son: the newest backup of each of the last ``daily`` days, ``weekly`` ISO
    weeks and ``monthly`` months."""
    keep: set[uuid.UUID] = set()
    seen_d: dict[date, uuid.UUID] = {}
    seen_w: dict[tuple[int, int], uuid.UUID] = {}
    seen_m: dict[tuple[int, int], uuid.UUID] = {}
    for bid, at in sorted(finished, key=lambda x: x[1], reverse=True):
        d = at.date()
        seen_d.setdefault(d, bid)
        iso = d.isocalendar()
        seen_w.setdefault((iso[0], iso[1]), bid)
        seen_m.setdefault((d.year, d.month), bid)
    keep.update(list(seen_d.values())[:daily])
    keep.update(list(seen_w.values())[:weekly])
    keep.update(list(seen_m.values())[:monthly])
    return keep


async def schedule_due(deps: Deps, now: datetime | None = None) -> list[uuid.UUID]:
    """Hourly: queue the scheduled backups that are due; prune by retention. Returns new backup ids."""
    now = now or datetime.now(UTC)
    created: list[uuid.UUID] = []
    async with deps.sessionmaker() as session:
        async with session.begin():
            await set_platform_bypass(session)
            owners = (
                (
                    await session.execute(
                        select(Firm).where(Firm.deleted_at.is_(None), Firm.is_active.is_(True))
                    )
                )
                .scalars()
                .all()
            )
            for owner in owners:
                if (owner.settings or {}).get("restore_pending"):
                    continue
                section = await deps.store.effective("backup", None if owner.is_platform else owner.id)
                schedule = str(section.get("schedule", "daily"))
                if schedule == "off" or now.hour != int(section.get("hour_utc", 2) or 0):
                    continue
                since = now - (timedelta(days=7) if schedule == "weekly" else timedelta(hours=20))
                recent = (
                    await session.execute(
                        select(Backup.id)
                        .where(
                            Backup.owner_id == owner.id,
                            Backup.trigger == "schedule",
                            Backup.created_at >= since,
                        )
                        .limit(1)
                    )
                ).first()
                if recent:
                    continue
                b = Backup(
                    owner_id=owner.id, kind="platform" if owner.is_platform else "firm", trigger="schedule"
                )
                session.add(b)
                await session.flush()
                created.append(b.id)
                # retention
                done = (
                    await session.execute(
                        select(Backup.id, Backup.finished_at).where(
                            Backup.owner_id == owner.id, Backup.status == "done", Backup.deleted_at.is_(None)
                        )
                    )
                ).all()
                keep = keep_set(
                    [(i, at) for i, at in done if at is not None],
                    int(section.get("keep_daily", 7) or 7),
                    int(section.get("keep_weekly", 4) or 4),
                    int(section.get("keep_monthly", 12) or 12),
                )
                target = await target_for(deps.store, deps.storage, owner)
                for i, _at in done:
                    if i in keep:
                        continue
                    old = await session.get(Backup, i)
                    if old is not None:
                        target.delete(old.object_key)
                        old.deleted_at = now
    return created
