"""Backups and restores. Firm owners back up, download and restore their own
firm; the super admin backs up the platform, restores any firm (also from an uploaded archive) and
restores the platform on a fresh installation. Passwords are revealed at most 5 times, each time after
a fresh 2FA code; the audit log records who revealed one, never the password."""

from __future__ import annotations

import json
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.background import BackgroundTask

from app.api.deps import FirmDep, SessionDep, SettingsDep, require
from app.api.v1.users import require_fresh_totp
from app.audit.service import record_audit
from app.backup import crypto
from app.backup.service import (
    MAX_REVEALS,
    BackupError,
    Deps,
    open_archive,
    restore_platform,
    target_for,
)
from app.db.rls import set_platform_bypass, set_tenant
from app.licensing.store import FirmSecrets
from app.models.backup import Backup, RecoveryKey, Restore
from app.models.firm import Firm
from app.platform import check_setup_code, complete_setup, get_platform, super_admin_exists
from app.tenancy.context import Actor

router = APIRouter(tags=["backups"])
MAX_UPLOAD = 4 * 1024**3


def _deps(request: Request) -> Deps:
    st = request.app.state
    return Deps(_sessionmaker(), st.storage, st.settings_store, st.master_key, st.settings)


def _sessionmaker() -> Any:
    from app.db.session import get_sessionmaker

    return get_sessionmaker()


def backup_out(b: Backup) -> dict[str, Any]:
    return {
        "id": str(b.id),
        "kind": b.kind,
        "status": b.status,
        "trigger": b.trigger,
        "target": b.target,
        "size_bytes": b.size_bytes,
        "sha256": b.sha256,
        "schema_revision": b.schema_revision,
        "app_version": b.app_version,
        "counts": b.counts,
        "reveals_used": b.reveals_used,
        "reveals_left": max(0, MAX_REVEALS - b.reveals_used),
        "recovery_key": b.recovery_key_id is not None,
        "error": b.error,
        "created_at": b.created_at.isoformat() if b.created_at else None,
        "finished_at": b.finished_at.isoformat() if b.finished_at else None,
        "deleted_at": b.deleted_at.isoformat() if b.deleted_at else None,
    }


async def _overview(session: AsyncSession, owner_id: uuid.UUID) -> dict[str, Any]:
    rows = (
        (
            await session.execute(
                select(Backup)
                .where(Backup.owner_id == owner_id)
                .order_by(Backup.created_at.desc())
                .limit(200)
            )
        )
        .scalars()
        .all()
    )
    rk = (
        await session.execute(
            select(RecoveryKey).where(RecoveryKey.owner_id == owner_id, RecoveryKey.active.is_(True))
        )
    ).scalar_one_or_none()
    restores = (
        (
            await session.execute(
                select(Restore)
                .where(Restore.owner_id == owner_id)
                .order_by(Restore.created_at.desc())
                .limit(20)
            )
        )
        .scalars()
        .all()
    )
    done = [b for b in rows if b.status == "done" and b.deleted_at is None]
    saved = (rk is not None and rk.confirmed_at is not None) or any(b.reveals_used > 0 for b in done)
    return {
        "backups": [backup_out(b) for b in rows],
        "recovery_key": {
            "set": rk is not None,
            "confirmed": rk is not None and rk.confirmed_at is not None,
            "public_key": rk.public_key if rk else None,
            "created_at": rk.created_at.isoformat() if rk and rk.created_at else None,
        },
        "password_not_saved": bool(done) and not saved,
        "restores": [
            {
                "id": str(r.id),
                "status": r.status,
                "mode": r.mode,
                "reason": r.reason,
                "error": r.error,
                "counts": r.counts,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "finished_at": r.finished_at.isoformat() if r.finished_at else None,
            }
            for r in restores
        ],
    }


async def _commit(session: AsyncSession, firm_id: uuid.UUID | None) -> None:
    """The job runs in its own transactions (or in the backup container): make the rows visible."""
    await session.commit()
    await session.begin()
    if firm_id is not None:
        await set_tenant(session, firm_id)
    else:
        await set_platform_bypass(session)


async def _new_backup(
    request: Request, session: AsyncSession, owner: Firm, actor: Actor, trigger: str = "manual"
) -> Backup:
    b = Backup(
        owner_id=owner.id,
        kind="platform" if owner.is_platform else "firm",
        trigger=trigger,
        created_by_user_id=actor.user_id,
    )
    session.add(b)
    await session.flush()
    await record_audit(
        session,
        firm_id=None if owner.is_platform else owner.id,
        actor=actor,
        action="backup.create",
        target_type="backup",
        target_id=str(b.id),
        after={"trigger": trigger},
    )
    return b


async def _reveal(
    request: Request, session: AsyncSession, owner: Firm, backup_id: uuid.UUID, code: str, actor: Actor
) -> dict[str, Any]:
    await require_fresh_totp(request, session, request.state.user, code)
    if owner.is_platform:
        await set_platform_bypass(session)
    b = await session.get(Backup, backup_id)
    if b is None or b.owner_id != owner.id or b.status != "done" or not b.password_enc:
        raise HTTPException(404, {"error": "not_found", "message": "Backup not found."})
    if b.reveals_used >= MAX_REVEALS:
        raise HTTPException(
            409, {"error": "reveals_used_up", "message": "This password was shown 5 times already."}
        )
    password = FirmSecrets(request.app.state.master_key, owner).open(f"backup:{b.id}", b.password_enc)
    if password is None:
        raise HTTPException(
            409, {"error": "password_unavailable", "message": "The password cannot be opened."}
        )
    b.reveals_used += 1
    await record_audit(  # who and when, never the password
        session,
        firm_id=None if owner.is_platform else owner.id,
        actor=actor,
        action="backup.password_revealed",
        target_type="backup",
        target_id=str(b.id),
        after={"reveal": b.reveals_used, "of": MAX_REVEALS},
    )
    return {"password": password, "reveals_left": MAX_REVEALS - b.reveals_used}


async def _download(
    request: Request, session: AsyncSession, owner: Firm, backup_id: uuid.UUID, actor: Actor
) -> FileResponse:
    b = await session.get(Backup, backup_id)
    if b is None or b.owner_id != owner.id or b.status != "done" or b.deleted_at is not None:
        raise HTTPException(404, {"error": "not_found", "message": "Backup not found."})
    target = await target_for(request.app.state.settings_store, request.app.state.storage, owner)
    tmp = Path(tempfile.mkdtemp(prefix="ivf-dl-")) / "backup.tar.gz.age"
    target.get(b.object_key, tmp)
    await record_audit(
        session,
        firm_id=None if owner.is_platform else owner.id,
        actor=actor,
        action="backup.download",
        target_type="backup",
        target_id=str(b.id),
    )
    name = f"invoiceflow-{b.kind}-{(b.finished_at or datetime.now(UTC)):%Y%m%d-%H%M}.tar.gz.age"
    import shutil

    return FileResponse(
        tmp,
        media_type="application/octet-stream",
        filename=name,
        background=BackgroundTask(shutil.rmtree, tmp.parent, True),
    )


async def _recovery_key(session: AsyncSession, owner: Firm, actor: Actor) -> dict[str, str]:
    for old in (await session.execute(select(RecoveryKey).where(RecoveryKey.owner_id == owner.id))).scalars():
        old.active = False
    private, public = crypto.new_recovery_key()
    session.add(RecoveryKey(owner_id=owner.id, public_key=public, created_by_user_id=actor.user_id))
    await record_audit(
        session,
        firm_id=None if owner.is_platform else owner.id,
        actor=actor,
        action="backup.recovery_key_created",
        target_type="recovery_key",
        target_id=public[:24],
    )
    return {"private_key": private, "public_key": public}


async def _confirm_key(session: AsyncSession, owner: Firm, actor: Actor) -> dict[str, bool]:
    rk = (
        await session.execute(
            select(RecoveryKey).where(RecoveryKey.owner_id == owner.id, RecoveryKey.active.is_(True))
        )
    ).scalar_one_or_none()
    if rk is None:
        raise HTTPException(404, {"error": "not_found", "message": "Create a recovery key first."})
    rk.confirmed_at = datetime.now(UTC)
    await record_audit(
        session,
        firm_id=None if owner.is_platform else owner.id,
        actor=actor,
        action="backup.recovery_key_confirmed",
        target_type="recovery_key",
        target_id=rk.public_key[:24],
    )
    return {"confirmed": True}


class CodeIn(BaseModel):
    code: str = Field(min_length=6, max_length=8)


class RestoreIn(BaseModel):
    code: str = Field(min_length=6, max_length=8)
    reason: str = Field(min_length=3, max_length=2000)


# ---- firm ---------------------------------------------------------------------------------------------
@router.get("/backups")
async def firm_backups(
    session: SessionDep, firm: FirmDep, actor: Actor = Depends(require("backups.manage"))
) -> dict[str, Any]:
    return await _overview(session, firm.id)


@router.post("/backups", status_code=202)
async def create_firm_backup(
    request: Request, session: SessionDep, firm: FirmDep, actor: Actor = Depends(require("backups.manage"))
) -> dict[str, Any]:
    b = await _new_backup(request, session, firm, actor)
    await _commit(session, firm.id)
    await request.app.state.backup_queue.enqueue_backup(b.id)
    await session.refresh(b)
    return backup_out(b)


@router.post("/backups/{backup_id}/reveal")
async def reveal_firm_password(
    backup_id: uuid.UUID,
    body: CodeIn,
    request: Request,
    session: SessionDep,
    firm: FirmDep,
    actor: Actor = Depends(require("backups.manage")),
) -> dict[str, Any]:
    return await _reveal(request, session, firm, backup_id, body.code, actor)


@router.get("/backups/{backup_id}/download")
async def download_firm_backup(
    backup_id: uuid.UUID,
    request: Request,
    session: SessionDep,
    firm: FirmDep,
    actor: Actor = Depends(require("backups.manage")),
) -> FileResponse:
    return await _download(request, session, firm, backup_id, actor)


@router.post("/backups/recovery-key")
async def firm_recovery_key(
    session: SessionDep, firm: FirmDep, actor: Actor = Depends(require("backups.manage"))
) -> dict[str, str]:
    return await _recovery_key(session, firm, actor)


@router.post("/backups/recovery-key/confirm")
async def confirm_firm_recovery_key(
    session: SessionDep, firm: FirmDep, actor: Actor = Depends(require("backups.manage"))
) -> dict[str, bool]:
    return await _confirm_key(session, firm, actor)


@router.post("/backups/{backup_id}/restore", status_code=202)
async def restore_firm_backup(
    backup_id: uuid.UUID,
    body: RestoreIn,
    request: Request,
    session: SessionDep,
    firm: FirmDep,
    actor: Actor = Depends(require("backups.restore")),
) -> dict[str, Any]:
    """Restore over the firm on this installation: a fresh backup of the current state is taken first."""
    await require_fresh_totp(request, session, request.state.user, body.code)
    await set_tenant(session, firm.id)
    b = await session.get(Backup, backup_id)
    if b is None or b.owner_id != firm.id or b.status != "done" or b.kind != "firm" or not b.password_enc:
        raise HTTPException(404, {"error": "not_found", "message": "Backup not found."})
    password = FirmSecrets(request.app.state.master_key, firm).open(f"backup:{b.id}", b.password_enc)
    pre = await _new_backup(request, session, firm, actor, trigger="pre_restore")
    r = Restore(
        owner_id=firm.id,
        kind="firm",
        backup_id=b.id,
        mode="replace",
        reason=body.reason,
        pre_backup_id=pre.id,
        requested_by_user_id=actor.user_id,
    )
    session.add(r)
    await session.flush()
    r.secret_enc = FirmSecrets(request.app.state.master_key, firm).seal(
        f"restore:{r.id}", json.dumps({"password": password})
    )
    await record_audit(
        session,
        firm_id=firm.id,
        actor=actor,
        action="firm.restore_requested",
        target_type="backup",
        target_id=str(b.id),
        after={"reason": body.reason, "pre_backup": str(pre.id)},
    )
    await _commit(session, firm.id)
    await request.app.state.backup_queue.enqueue_backup(pre.id)
    await request.app.state.backup_queue.enqueue_restore(r.id)
    await session.refresh(r)
    return {"restore_id": str(r.id), "status": r.status, "pre_backup_id": str(pre.id)}


# ---- platform -----------------------------------------------------------------------------------------
async def _platform(session: AsyncSession) -> Firm:
    await set_platform_bypass(session)
    platform = await get_platform(session)
    if platform is None:
        raise HTTPException(409, {"error": "no_platform", "message": "The platform is not set up yet."})
    return platform


@router.get("/platform/backups")
async def platform_backups(
    session: SessionDep, actor: Actor = Depends(require("platform.admin"))
) -> dict[str, Any]:
    platform = await _platform(session)
    return await _overview(session, platform.id)


@router.post("/platform/backups", status_code=202)
async def create_platform_backup(
    request: Request, session: SessionDep, actor: Actor = Depends(require("platform.admin"))
) -> dict[str, Any]:
    platform = await _platform(session)
    b = await _new_backup(request, session, platform, actor)
    await _commit(session, None)
    await request.app.state.backup_queue.enqueue_backup(b.id)
    await session.refresh(b)
    return backup_out(b)


@router.post("/platform/firms/{firm_id}/backup", status_code=202)
async def create_backup_of_firm(
    firm_id: uuid.UUID,
    request: Request,
    session: SessionDep,
    actor: Actor = Depends(require("platform.admin")),
) -> dict[str, Any]:
    await set_platform_bypass(session)
    firm = await session.get(Firm, firm_id)
    if firm is None or firm.kind != "firm":
        raise HTTPException(404, {"error": "not_found", "message": "Firm not found."})
    b = await _new_backup(request, session, firm, actor)
    await _commit(session, None)
    await request.app.state.backup_queue.enqueue_backup(b.id)
    await session.refresh(b)
    return backup_out(b)


@router.post("/platform/backups/{backup_id}/reveal")
async def reveal_platform_password(
    backup_id: uuid.UUID,
    body: CodeIn,
    request: Request,
    session: SessionDep,
    actor: Actor = Depends(require("platform.admin")),
) -> dict[str, Any]:
    platform = await _platform(session)
    return await _reveal(request, session, platform, backup_id, body.code, actor)


@router.get("/platform/backups/{backup_id}/download")
async def download_platform_backup(
    backup_id: uuid.UUID,
    request: Request,
    session: SessionDep,
    actor: Actor = Depends(require("platform.admin")),
) -> FileResponse:
    platform = await _platform(session)
    return await _download(request, session, platform, backup_id, actor)


@router.post("/platform/recovery-key")
async def platform_recovery_key(
    session: SessionDep, actor: Actor = Depends(require("platform.admin"))
) -> dict[str, str]:
    return await _recovery_key(session, await _platform(session), actor)


@router.post("/platform/recovery-key/confirm")
async def confirm_platform_recovery_key(
    session: SessionDep, actor: Actor = Depends(require("platform.admin"))
) -> dict[str, bool]:
    return await _confirm_key(session, await _platform(session), actor)


async def _store_upload(request: Request, file: UploadFile) -> str:
    key = f"uploads/restore/{uuid.uuid4().hex}.age"
    data = await file.read(MAX_UPLOAD + 1)
    if len(data) > MAX_UPLOAD or not data:
        raise HTTPException(413, {"error": "too_large", "message": "The archive is empty or too large."})
    await request.app.state.storage.put(key, data)
    return key


@router.post("/platform/firms/{firm_id}/restore", status_code=202)
async def restore_firm_upload(
    firm_id: uuid.UUID,
    request: Request,
    session: SessionDep,
    file: UploadFile = File(...),
    reason: str = Form(min_length=3, max_length=2000),
    password: str | None = Form(default=None, max_length=200),
    recovery_key: str | None = Form(default=None, max_length=200),
    actor: Actor = Depends(require("platform.admin")),
) -> dict[str, Any]:
    """The super admin restores a firm from an uploaded archive (another server, or a placeholder after a
    platform restore). An existing firm gets an automatic backup first."""
    if not password and not recovery_key:
        raise HTTPException(
            422, {"error": "invalid_request", "message": "Enter the backup password or the recovery key."}
        )
    await set_platform_bypass(session)
    firm = await session.get(Firm, firm_id)
    platform = await _platform(session)
    pre = None
    if firm is not None and not (firm.settings or {}).get("restore_pending"):
        pre = await _new_backup(request, session, firm, actor, trigger="pre_restore")
    key = await _store_upload(request, file)
    r = Restore(
        owner_id=firm_id,
        kind="firm",
        upload_key=key,
        mode="replace" if firm is not None else "new",
        reason=reason,
        pre_backup_id=pre.id if pre else None,
        requested_by_user_id=actor.user_id,
    )
    session.add(r)
    await session.flush()
    holder = firm if firm is not None else platform
    if firm is None:  # the job opens the secret with the platform key when the firm does not exist yet
        r.owner_id = firm_id
    r.secret_enc = FirmSecrets(request.app.state.master_key, holder).seal(
        f"restore:{r.id}", json.dumps({"password": password, "recovery_key": recovery_key})
    )
    await record_audit(
        session,
        firm_id=firm_id if firm is not None else None,
        actor=actor,
        action="firm.restore_requested",
        target_type="firm",
        target_id=str(firm_id),
        after={"reason": reason, "upload": True, "pre_backup": str(pre.id) if pre else None},
    )
    await _commit(session, None)
    if pre is not None:
        await request.app.state.backup_queue.enqueue_backup(pre.id)
    await request.app.state.backup_queue.enqueue_restore(r.id)
    await session.refresh(r)
    return {"restore_id": str(r.id), "status": r.status, "error": r.error, "counts": r.counts}


@router.post("/setup/restore", status_code=201)
async def setup_from_backup(
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    setup_code: str = Form(min_length=12, max_length=20),
    file: UploadFile = File(...),
    password: str | None = Form(default=None, max_length=200),
    recovery_key: str | None = Form(default=None, max_length=200),
) -> dict[str, Any]:
    """First run on a fresh server: restore the platform from its backup instead of a new setup. The
    super admin then signs in with the restored account; firms appear as "missing data"."""
    if await super_admin_exists(session):
        raise HTTPException(409, {"error": "setup_done", "message": "This installation is already set up."})
    if not await check_setup_code(session, settings, setup_code):
        raise HTTPException(403, {"error": "setup_code_invalid", "message": "The setup code is wrong."})
    data = await file.read(MAX_UPLOAD + 1)
    work = Path(tempfile.mkdtemp(prefix="ivf-setup-restore-"))
    try:
        enc = work / "platform.age"
        enc.write_bytes(data)
        try:
            content = open_archive(enc, work, password=password, backup_id=None, recovery_key=recovery_key)
            await _commit(session, None)
            result = await restore_platform(_deps(request), content)
        except BackupError as e:
            raise HTTPException(422, {"error": e.code, "message": str(e)}) from e
    finally:
        import shutil

        shutil.rmtree(work, ignore_errors=True)
    await set_platform_bypass(session)
    await complete_setup(session)
    request.app.state.settings_store.invalidate()
    await record_audit(
        session, firm_id=None, actor=None, action="platform.restored", target_type="platform", after=result
    )
    return {"restored": True, **result}
