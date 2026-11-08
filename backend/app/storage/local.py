"""Local filesystem object store (development and tests). Keys are sanitised; no path traversal."""

from __future__ import annotations

import asyncio
import os
import re
import stat
from datetime import datetime
from pathlib import Path

from app.storage.base import StorageError

_SEG = re.compile(r"^[A-Za-z0-9._-]+$")


class LocalStorage:
    def __init__(self, root: str) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        parts = key.split("/")
        if not parts or any(not p or not _SEG.match(p) or p in (".", "..") for p in parts):
            raise StorageError("invalid storage key")
        p = self.root.joinpath(*parts)
        if self.root not in p.parents:
            raise StorageError("invalid storage key")
        return p

    async def put(self, key: str, data: bytes, *, retain_until: datetime | None = None) -> None:
        p = self._path(key)

        def _write() -> None:
            p.parent.mkdir(parents=True, exist_ok=True)
            if p.exists():
                os.chmod(p, stat.S_IWRITE | stat.S_IREAD)
            tmp = p.with_suffix(p.suffix + ".tmp")
            tmp.write_bytes(data)
            os.replace(tmp, p)
            if retain_until is not None:
                os.chmod(p, stat.S_IREAD)  # archive originals are read-only on disk (tamper evidence)

        await asyncio.to_thread(_write)

    async def get(self, key: str) -> bytes:
        p = self._path(key)
        try:
            return await asyncio.to_thread(p.read_bytes)
        except FileNotFoundError as e:
            raise StorageError("object not found") from e

    async def delete(self, key: str) -> None:
        p = self._path(key)

        def _rm() -> None:
            if p.exists():
                os.chmod(p, stat.S_IWRITE | stat.S_IREAD)
            p.unlink(missing_ok=True)

        await asyncio.to_thread(_rm)

    async def exists(self, key: str) -> bool:
        return await asyncio.to_thread(self._path(key).exists)
