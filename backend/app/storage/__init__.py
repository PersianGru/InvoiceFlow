from app.config import Settings
from app.storage.base import Storage, StorageError
from app.storage.local import LocalStorage
from app.storage.s3 import S3Storage

__all__ = ["LocalStorage", "S3Storage", "Storage", "StorageError", "build_storage"]


def build_storage(settings: Settings) -> Storage:
    """Pick the backend from settings (``storage_backend``: ``local`` | ``s3``)."""
    if settings.storage_backend == "s3":
        s3 = S3Storage(
            settings.s3_bucket,
            endpoint_url=settings.s3_endpoint,
            region=settings.s3_region,
            access_key=settings.s3_access_key,
            secret_key=settings.s3_secret_key,
            object_lock=settings.s3_object_lock,
        )
        s3.ensure_bucket()
        return s3
    return LocalStorage(settings.storage_local_path)
