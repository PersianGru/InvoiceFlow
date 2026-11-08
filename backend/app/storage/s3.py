"""S3-compatible object storage (MinIO for self-hosting, an EU-region bucket otherwise).

Server-side encryption is requested on every put (SSE-S3; MinIO honours it when KMS/SSE is on).
Object lock / WORM for the archive is configured on the bucket (bucket configuration, not code).
"""

from __future__ import annotations

import asyncio
import re
from datetime import datetime
from typing import Any, cast

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from app.storage.base import StorageError

_SEG = re.compile(r"^[A-Za-z0-9._-]+$")


def _check_key(key: str) -> str:
    parts = key.split("/")
    if not parts or any(not p or not _SEG.match(p) or p in (".", "..") for p in parts):
        raise StorageError("invalid storage key")
    return key


class S3Storage:
    def __init__(
        self,
        bucket: str,
        *,
        endpoint_url: str | None = None,
        region: str = "eu-central-1",
        access_key: str | None = None,
        secret_key: str | None = None,
        sse: str | None = "AES256",
        client: Any | None = None,
        object_lock: str = "",
    ) -> None:
        self.bucket = bucket
        self.region = region
        self._sse = sse
        self._object_lock = object_lock.upper() if object_lock else ""
        self._client = client or boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            region_name=region,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            config=Config(signature_version="s3v4", retries={"max_attempts": 3, "mode": "standard"}),
        )

    def ensure_bucket(self) -> None:
        try:
            self._client.head_bucket(Bucket=self.bucket)
        except ClientError:
            if self.region and self.region != "us-east-1":
                self._client.create_bucket(
                    Bucket=self.bucket,
                    CreateBucketConfiguration=cast(Any, {"LocationConstraint": self.region}),
                )
            else:
                self._client.create_bucket(Bucket=self.bucket)

    async def put(self, key: str, data: bytes, *, retain_until: datetime | None = None) -> None:
        key = _check_key(key)
        extra: dict[str, Any] = {"ServerSideEncryption": self._sse} if self._sse else {}
        if (
            self._object_lock and retain_until is not None
        ):  # WORM: the archive cannot be changed or deleted early
            extra["ObjectLockMode"] = self._object_lock
            extra["ObjectLockRetainUntilDate"] = retain_until

        def _do() -> None:
            self._client.put_object(Bucket=self.bucket, Key=key, Body=data, **extra)

        await asyncio.to_thread(_do)

    async def get(self, key: str) -> bytes:
        key = _check_key(key)

        def _do() -> bytes:
            try:
                return bytes(self._client.get_object(Bucket=self.bucket, Key=key)["Body"].read())
            except ClientError as e:
                raise StorageError("object not found") from e

        return await asyncio.to_thread(_do)

    async def delete(self, key: str) -> None:
        key = _check_key(key)
        await asyncio.to_thread(lambda: self._client.delete_object(Bucket=self.bucket, Key=key))

    async def exists(self, key: str) -> bool:
        key = _check_key(key)

        def _do() -> bool:
            try:
                self._client.head_object(Bucket=self.bucket, Key=key)
                return True
            except ClientError:
                return False

        return await asyncio.to_thread(_do)
