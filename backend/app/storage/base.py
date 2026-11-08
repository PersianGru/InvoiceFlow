from __future__ import annotations

from datetime import datetime
from typing import Protocol


class StorageError(Exception):
    pass


class Storage(Protocol):
    async def put(self, key: str, data: bytes, *, retain_until: datetime | None = None) -> None: ...

    async def get(self, key: str) -> bytes: ...

    async def delete(self, key: str) -> None: ...

    async def exists(self, key: str) -> bool: ...
