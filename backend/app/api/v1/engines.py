"""Engine catalogue for Settings → Processing: what is configured, installed and active,
and a quick self-test of the installed engines. Cloud engines are only *described* here; a test
never sends a document to a cloud provider."""

from __future__ import annotations

import asyncio
import time
from typing import Any

from fastapi import APIRouter, Depends, Request
from PIL import Image, ImageDraw

from app.api.deps import SettingsDep, require
from app.pipeline.factory import describe_engines
from app.pipeline.runner import ProcessingRunner
from app.tenancy.context import Actor

router = APIRouter(prefix="/engines", tags=["engines"])


def _runner(request: Request) -> ProcessingRunner:
    return request.app.state.runner  # type: ignore[no-any-return]


@router.get("")
async def engines(
    request: Request, settings: SettingsDep, actor: Actor = Depends(require("firm.manage"))
) -> dict[str, Any]:
    return describe_engines(settings, _runner(request).d)


def _sample_image() -> Image.Image:
    img = Image.new("RGB", (640, 200), "white")
    d = ImageDraw.Draw(img)
    d.text((20, 30), "INVOICE 2026-00042", fill="black")
    d.text((20, 70), "Total 123.45 EUR", fill="black")
    d.text((20, 110), "Muster GmbH", fill="black")
    return img


@router.post("/test")
async def test_engines(
    request: Request, settings: SettingsDep, actor: Actor = Depends(require("firm.manage"))
) -> dict[str, Any]:
    """Runs the local OCR engine on a generated sample and checks the LLM providers' availability."""
    deps = _runner(request).d
    out: dict[str, Any] = {}
    ocr = deps.ocr
    if ocr.available():
        t0 = time.monotonic()
        try:
            page = await asyncio.to_thread(ocr.recognise, _sample_image(), 1, settings.ocr_languages)
            found = "2026" in page.text and "123" in page.text
            out["local_ocr"] = {
                "key": ocr.name,
                "ok": True,
                "recognised": found,
                "words": len(page.words),
                "ms": int((time.monotonic() - t0) * 1000),
            }
        except Exception as e:  # noqa: BLE001 - a failing engine is a result, not an error
            out["local_ocr"] = {"key": ocr.name, "ok": False, "error": type(e).__name__}
    else:
        out["local_ocr"] = {"key": ocr.name, "ok": False, "error": "not available"}
    for label, provider in (("local_llm", deps.router.local_llm), ("cloud_llm", deps.router.cloud)):
        if provider is None:
            out[label] = {"ok": False, "error": "not configured"}
            continue
        t0 = time.monotonic()
        ok = await provider.available()
        out[label] = {
            "key": getattr(provider, "label", provider.name),
            "model": getattr(provider, "model", None),
            "ok": ok,
            "ms": int((time.monotonic() - t0) * 1000),
        }
    out["cloud_ocr"] = (
        {"key": deps.cloud_ocr.name, "ok": deps.cloud_ocr.available()}
        if deps.cloud_ocr is not None
        else {"ok": False, "error": "not configured"}
    )
    return out
