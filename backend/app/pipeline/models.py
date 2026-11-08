"""Built-in model files: pinned downloads into the models directory, verified by SHA-256.

The default local model is a small instruction-tuned GGUF that runs on a CPU without a GPU; a
smaller one exists for very small boxes. Nothing is downloaded unless ``LLAMACPP_AUTO_DOWNLOAD``
is on; the hash is checked before the file is used, and a file that does not match is deleted.
"""

from __future__ import annotations

import hashlib
import logging
import os
import secrets
import threading
from dataclasses import dataclass
from pathlib import Path

import httpx

log = logging.getLogger("invoiceflow.pipeline.models")


@dataclass(frozen=True, slots=True)
class ModelSpec:
    key: str
    repo: str
    filename: str
    sha256: str
    size_bytes: int
    ram_hint: str

    @property
    def url(self) -> str:
        return f"https://huggingface.co/{self.repo}/resolve/main/{self.filename}"


MODELS: dict[str, ModelSpec] = {
    "qwen2.5-1.5b": ModelSpec(
        "qwen2.5-1.5b",
        "Qwen/Qwen2.5-1.5B-Instruct-GGUF",
        "qwen2.5-1.5b-instruct-q4_k_m.gguf",
        "6a1a2eb6d15622bf3c96857206351ba97e1af16c30d7a74ee38970e434e9407e",
        1_117_320_736,
        "about 1.5 GB RAM, 2+ CPU cores",
    ),
    "qwen2.5-0.5b": ModelSpec(
        "qwen2.5-0.5b",
        "Qwen/Qwen2.5-0.5B-Instruct-GGUF",
        "qwen2.5-0.5b-instruct-q4_k_m.gguf",
        "74a4da8c9fdbcd15bd1f6d01d621410d31c6fc00986f5eb687824e7b93d7a9db",
        491_400_032,
        "about 0.8 GB RAM, 1+ CPU core (lower accuracy)",
    ),
}
DEFAULT_MODEL = "qwen2.5-1.5b"


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def resolve_model(spec_or_path: str, models_dir: str) -> tuple[ModelSpec | None, Path]:
    """A key from ``MODELS`` → its pinned file under ``models_dir``; anything else is a local path."""
    if spec_or_path in MODELS:
        spec = MODELS[spec_or_path]
        return spec, Path(models_dir) / spec.filename
    return None, Path(spec_or_path)


def model_ready(spec_or_path: str, models_dir: str) -> bool:
    spec, path = resolve_model(spec_or_path, models_dir)
    if not path.is_file():
        return False
    if spec is None:
        return True
    return path.stat().st_size == spec.size_bytes


def ensure_model(spec_or_path: str, models_dir: str, *, http: httpx.Client | None = None) -> Path | None:
    """Download and verify the pinned model when missing. Returns the path, or None when not usable."""
    spec, path = resolve_model(spec_or_path, models_dir)
    if spec is None:
        return path if path.is_file() else None
    if path.is_file() and path.stat().st_size == spec.size_bytes:
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    # unique per download: containers all run as PID 1 and may share the models volume
    tmp = path.with_suffix(f"{path.suffix}.part-{os.getpid()}-{secrets.token_hex(4)}")
    client = http or httpx.Client(timeout=httpx.Timeout(60.0, read=600.0), follow_redirects=True)
    log.info("downloading model %s (%d MB)", spec.filename, spec.size_bytes // 1_000_000)
    try:
        h = hashlib.sha256()
        with client.stream("GET", spec.url) as r, tmp.open("wb") as f:
            r.raise_for_status()
            for chunk in r.iter_bytes(1 << 20):
                f.write(chunk)
                h.update(chunk)
        if h.hexdigest() != spec.sha256:
            tmp.unlink(missing_ok=True)
            log.error("model %s: checksum mismatch, file discarded", spec.filename)
            return None
        os.replace(tmp, path)
        log.info("model %s ready", spec.filename)
        return path
    except (httpx.HTTPError, OSError) as e:
        tmp.unlink(missing_ok=True)
        if path.is_file() and path.stat().st_size == spec.size_bytes:
            return path  # another process finished the same download meanwhile
        log.warning("model download failed: %s: %s", type(e).__name__, str(e)[:200])
        return None
    finally:
        if http is None:
            client.close()


def ensure_model_in_background(spec_or_path: str, models_dir: str) -> threading.Thread | None:
    """Start the download in a daemon thread (worker start-up); None when nothing is to do."""
    if model_ready(spec_or_path, models_dir):
        return None
    t = threading.Thread(
        target=ensure_model, args=(spec_or_path, models_dir), name="model-download", daemon=True
    )
    t.start()
    return t
