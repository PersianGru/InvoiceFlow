"""Build the runner and its dependencies from settings (shared by the API and the worker).

Engine tiers: local OCR (Tesseract or the built-in RapidOCR), optional cloud OCR
(Mistral), local LLM (Ollama, the built-in llama.cpp model, or an OpenAI-compatible local server),
cloud LLM (Claude, OpenAI, Mistral, or any OpenAI-compatible endpoint). The runner enforces when
cloud engines may be used; this module only decides what exists.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.licensing.service import LicenseService
from app.pipeline.extract.llm import (
    ClaudeProvider,
    LlamaCppProvider,
    OllamaProvider,
    OpenAICompatibleProvider,
)
from app.pipeline.extract.router import ExtractionRouter
from app.pipeline.extract.rules import RulesProvider
from app.pipeline.models import MODELS, model_ready, resolve_model
from app.pipeline.ocr_engines import MistralOcrEngine, RapidOcrEngine
from app.pipeline.render import render_pdf_pages, renderer_available
from app.pipeline.runner import ProcessingRunner, RunnerDeps
from app.pipeline.security import ClamdScanner, NoScanner, VirusScanner
from app.pipeline.text import NoOcrEngine, OcrEngine, TesseractEngine
from app.pipeline.types import ExtractionProvider
from app.pipeline.validation.currency import EcbRates, RatesProvider
from app.pipeline.validation.vies import CachedVies, HttpViesClient, ViesClient
from app.storage import Storage

log = logging.getLogger("invoiceflow.pipeline.factory")

DEFAULT_LANGUAGES = "eng+deu+fra+nld+spa+ita+pol"


def build_scanner(settings: Settings) -> VirusScanner:
    if settings.clamav_host:
        return ClamdScanner(settings.clamav_host, settings.clamav_port)
    if settings.virus_scan_required_effective:
        raise RuntimeError("virus scan required but CLAMAV_HOST is not set")
    log.warning("virus scanning is OFF (no CLAMAV_HOST); acceptable outside production only")
    return NoScanner()


def ocr_candidates(settings: Settings) -> list[OcrEngine]:
    langs = settings.ocr_languages or DEFAULT_LANGUAGES
    out: list[OcrEngine] = []
    if settings.ocr_engine in ("auto", "tesseract"):
        out.append(TesseractEngine(langs))
    if settings.ocr_engine in ("auto", "rapidocr"):
        out.append(RapidOcrEngine(langs, settings.models_dir))
    return out


def build_ocr(settings: Settings) -> OcrEngine:
    for engine in ocr_candidates(settings):
        if engine.available():
            return engine
    if settings.ocr_engine != "none":
        log.warning(
            "no local OCR engine: install tesseract or the [cpu] extra (rapidocr); "
            "images and scanned PDFs cannot be read on this host"
        )
    return NoOcrEngine()


def build_cloud_ocr(settings: Settings) -> OcrEngine | None:
    if settings.ocr_cloud == "mistral" and settings.mistral_api_key:
        return MistralOcrEngine(settings.mistral_api_key, settings.mistral_ocr_model)
    return None


def build_local_llm(settings: Settings, *, download_models: bool = False) -> ExtractionProvider | None:
    choice = settings.local_llm
    if choice in ("auto", "ollama") and settings.ollama_url:
        return OllamaProvider(settings.ollama_url, settings.ollama_model)
    if choice in ("auto", "openai_compat") and settings.local_openai_base_url:
        return OpenAICompatibleProvider(
            settings.local_openai_base_url,
            settings.local_openai_api_key or "",
            settings.local_openai_model,
            external=False,
            vision=settings.local_openai_vision,
            thinking=settings.local_openai_thinking,
        )
    if choice in ("auto", "llamacpp"):
        provider = LlamaCppProvider(
            settings.llamacpp_model,
            settings.models_dir,
            threads=settings.llamacpp_threads,
            n_ctx=settings.llamacpp_context,
        )
        if provider.installed():
            if (
                download_models
                and settings.llamacpp_auto_download
                and not model_ready(settings.llamacpp_model, settings.models_dir)
            ):
                from app.pipeline.models import ensure_model_in_background

                ensure_model_in_background(settings.llamacpp_model, settings.models_dir)
            return provider
        if choice == "llamacpp":
            log.warning("LOCAL_LLM=llamacpp but llama-cpp-python is not installed (pip install .[cpu])")
    return None


def build_cloud_llm(settings: Settings) -> ExtractionProvider | None:
    choice = settings.cloud_provider
    if choice in ("auto", "anthropic") and settings.anthropic_api_key:
        return ClaudeProvider(settings.anthropic_api_key, settings.cloud_model)
    if choice in ("auto", "openai") and settings.openai_api_key:
        return OpenAICompatibleProvider(
            settings.openai_base_url,
            settings.openai_api_key,
            settings.openai_model,
            external=True,
            vision=settings.cloud_vision,
            label="openai",
        )
    if choice in ("auto", "mistral") and settings.mistral_api_key:
        return OpenAICompatibleProvider(
            "https://api.mistral.ai/v1",
            settings.mistral_api_key,
            settings.mistral_model,
            external=True,
            vision=settings.cloud_vision,
            label="mistral",
        )
    if choice in ("auto", "openai_compat") and settings.cloud_openai_compat_base_url:
        return OpenAICompatibleProvider(
            settings.cloud_openai_compat_base_url,
            settings.cloud_openai_compat_api_key or "",
            settings.cloud_openai_compat_model,
            external=True,
            vision=settings.cloud_vision,
            label="openai_compat",
        )
    return None


def build_router(settings: Settings, *, download_models: bool = False) -> ExtractionRouter:
    return ExtractionRouter(
        RulesProvider(), build_local_llm(settings, download_models=download_models), build_cloud_llm(settings)
    )


def build_runner(
    settings: Settings,
    storage: Storage,
    license_service: LicenseService,
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    scanner: VirusScanner | None = None,
    ocr: OcrEngine | None = None,
    cloud_ocr: OcrEngine | None = None,
    router: ExtractionRouter | None = None,
    vies: ViesClient | None = None,
    rates: RatesProvider | None = None,
    download_models: bool = False,
) -> ProcessingRunner:
    router = router or build_router(settings, download_models=download_models)
    vision = any(getattr(p, "vision", False) for p in (router.local_llm, router.cloud) if p is not None)
    deps = RunnerDeps(
        storage=storage,
        scanner=scanner or build_scanner(settings),
        ocr=ocr or build_ocr(settings),
        router=router,
        license_service=license_service,
        vies=(
            vies
            if vies is not None
            else (CachedVies(HttpViesClient(), sessionmaker) if settings.vies_enabled else None)
        ),
        rates=(
            rates if rates is not None else (EcbRates(sessionmaker) if settings.ecb_rates_enabled else None)
        ),
        ocr_languages=settings.ocr_languages,
        pdf_renderer=render_pdf_pages if renderer_available() else None,
        cloud_ocr=cloud_ocr if cloud_ocr is not None else build_cloud_ocr(settings),
        attach_page_images=vision,
    )
    return ProcessingRunner(deps)


def describe_engines(settings: Settings, deps: RunnerDeps) -> dict[str, Any]:
    """The engine catalogue for Settings → Processing: what is configured, installed and active."""

    def ocr_row(engine: OcrEngine, active: bool) -> dict[str, Any]:
        return {
            "key": engine.name,
            "available": engine.available(),
            "active": active,
            "external": bool(getattr(engine, "external", False)),
        }

    local_ocr = [ocr_row(e, e.name == deps.ocr.name) for e in ocr_candidates(settings)]
    if settings.ocr_engine == "none":
        local_ocr = []
    cloud_ocr = [ocr_row(deps.cloud_ocr, True)] if deps.cloud_ocr is not None else []
    spec, path = resolve_model(settings.llamacpp_model, settings.models_dir)
    local_llm: dict[str, Any] | None = None
    if deps.router.local_llm is not None:
        p = deps.router.local_llm
        local_llm = {
            "key": getattr(p, "label", p.name),
            "model": getattr(p, "model", None),
            "vision": bool(getattr(p, "vision", False)),
        }
    cloud_llm: dict[str, Any] | None = None
    if deps.router.cloud is not None:
        p = deps.router.cloud
        cloud_llm = {
            "key": getattr(p, "label", "anthropic"),
            "model": getattr(p, "model", None),
            "vision": bool(getattr(p, "vision", False)),
        }
    return {
        "local_ocr": local_ocr,
        "cloud_ocr": cloud_ocr,
        "local_llm": local_llm,
        "cloud_llm": cloud_llm,
        "settings": {
            "ocr_engine": settings.ocr_engine,
            "ocr_cloud": settings.ocr_cloud,
            "local_llm": settings.local_llm,
            "cloud_provider": settings.cloud_provider,
            "ocr_languages": settings.ocr_languages or DEFAULT_LANGUAGES,
        },
        "builtin_model": {
            "key": settings.llamacpp_model,
            "file": path.name,
            "ready": model_ready(settings.llamacpp_model, settings.models_dir),
            "auto_download": settings.llamacpp_auto_download,
            "size_mb": (spec.size_bytes // 1_000_000) if spec else None,
            "ram_hint": spec.ram_hint if spec else None,
            "choices": [
                {"key": k, "size_mb": m.size_bytes // 1_000_000, "ram_hint": m.ram_hint}
                for k, m in MODELS.items()
            ],
        },
        "renderer": deps.pdf_renderer is not None,
    }
