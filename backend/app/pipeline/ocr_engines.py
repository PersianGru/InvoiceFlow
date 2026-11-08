"""Additional OCR engines behind ``OcrEngine`` (engine tiers).

* ``RapidOcrEngine``: built-in, CPU only, pure pip (``rapidocr`` with ONNX Runtime; PP-OCR models per
  language, downloaded once into the models directory). No system package, no GPU.
* ``MistralOcrEngine``: a cloud OCR API (Mistral ``/v1/ocr``). ``external = True``: the runner uses it
  only when the firm's license carries ``cloud_ai``, the firm opted in, the routing policy allows
  cloud, and the monthly cap is not reached; every call is logged (purpose ``ocr``).

Both return the same ``PageText`` shape as Tesseract (words with relative boxes, line text).
"""

from __future__ import annotations

import base64
import io
import logging
import os
import re
import threading
from typing import Any

import httpx
from PIL import Image

from app.pipeline.types import PageText, Word

log = logging.getLogger("invoiceflow.pipeline.ocr")

# Tesseract-style language codes (OCR_LANGUAGES) → rapidocr recognition models.
_RAPID_LANG = {
    "eng": "en",
    "deu": "de",
    "fra": "fr",
    "nld": "nl",
    "spa": "es",
    "ita": "it",
    "pol": "pl",
    "por": "pt",
    "ces": "cs",
    "dan": "da",
    "swe": "sv",
    "fin": "fi",
    "hun": "hu",
    "ron": "ro",
}


def rapid_language(languages: str, default: str = "en") -> str:
    """The first configured language a rapidocr model exists for (``"eng+deu"`` → ``en``)."""
    for code in (languages or "").split("+"):
        code = code.strip().lower()
        if code in _RAPID_LANG:
            return _RAPID_LANG[code]
        if len(code) == 2 and code in _RAPID_LANG.values():
            return code
    return default


def _lines_from_boxes(
    items: list[tuple[list[list[float]], str, float]], width: int, height: int, page: int = 1
) -> PageText:
    """Words with relative boxes and line text grouped by vertical position (reading order)."""
    words: list[Word] = []
    rows: list[tuple[float, float, str]] = []  # (y centre, x0, text)
    for quad, txt, score in items:
        txt = (txt or "").strip()
        if not txt or not quad:
            continue
        xs = [float(p[0]) for p in quad]
        ys = [float(p[1]) for p in quad]
        x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
        conf = max(0.0, min(1.0, float(score)))
        # one recogniser box is a text line: split it into words sharing the box horizontally
        parts = txt.split()
        step = (x1 - x0) / max(1, len(txt))
        cursor = x0
        for part in parts:
            px1 = cursor + step * len(part)
            words.append(
                Word(part, page, cursor / width, y0 / height, min(px1, x1) / width, y1 / height, conf)
            )
            cursor = px1 + step
        rows.append(((y0 + y1) / 2, x0, txt))
    rows.sort(key=lambda r: (round(r[0] / max(1.0, height * 0.012)), r[1]))
    lines: list[list[str]] = []
    last_y: float | None = None
    for yc, _x0, txt in rows:
        if last_y is not None and abs(yc - last_y) <= max(4.0, height * 0.008):
            lines[-1].append(txt)
        else:
            lines.append([txt])
        last_y = yc
    return PageText(
        index=page, width=width, height=height, words=words, text="\n".join(" ".join(ln) for ln in lines)
    )


class RapidOcrEngine:
    """Built-in CPU OCR (rapidocr, ONNX Runtime). Models live under ``models_dir/rapidocr``."""

    name = "rapidocr"
    external = False

    def __init__(self, languages: str = "eng+deu", models_dir: str | None = None) -> None:
        self.languages = languages
        self.models_dir = models_dir
        self._engines: dict[str, Any] = {}
        self._lock = threading.Lock()

    _importable: bool | None = None

    def available(self) -> bool:
        if self._importable is None:
            try:
                import onnxruntime  # noqa: F401  (rapidocr does not depend on it; the engine needs it)
                from rapidocr import RapidOCR  # noqa: F401  (pulls in OpenCV)

                self._importable = True
            except Exception as e:  # noqa: BLE001 - optional dependency or a missing native library
                log.warning("rapidocr not usable: %s: %s", type(e).__name__, str(e)[:160])
                self._importable = False
        return self._importable

    def _engine(self, lang: str) -> Any:
        with self._lock:
            if lang not in self._engines:
                from rapidocr import RapidOCR

                if self.models_dir:
                    os.makedirs(self.models_dir, exist_ok=True)
                    os.environ.setdefault("RAPIDOCR_HOME", self.models_dir)
                params: dict[str, Any] = {"Rec.lang_type": lang, "Global.log_level": "error"}
                try:
                    self._engines[lang] = RapidOCR(params=params)
                except Exception as e:  # noqa: BLE001 - unknown language model: fall back to English
                    log.warning("rapidocr: no model for %s (%s); using en", lang, type(e).__name__)
                    self._engines[lang] = self._engines.get("en") or RapidOCR(
                        params={"Rec.lang_type": "en", "Global.log_level": "error"}
                    )
            return self._engines[lang]

    def recognise(self, image: Image.Image, page: int, languages: str) -> PageText:
        import numpy as np

        lang = rapid_language(languages or self.languages)
        engine = self._engine(lang)
        arr = np.array(image.convert("RGB"))[:, :, ::-1]  # BGR as the models expect
        out = engine(arr)
        items: list[tuple[list[list[float]], str, float]] = []
        if out is not None and getattr(out, "txts", None):
            boxes = out.boxes if out.boxes is not None else []
            scores = out.scores or []
            for i, txt in enumerate(out.txts):
                quad = boxes[i].tolist() if i < len(boxes) else []
                items.append((quad, str(txt), float(scores[i]) if i < len(scores) else 0.0))
        w, h = image.size
        return _lines_from_boxes(items, w, h, page)


_MD_STRIP = re.compile(r"[*_`#>|]+")


class MistralOcrEngine:
    """Mistral document OCR (``POST /v1/ocr``): markdown per page, no word boxes."""

    name = "mistral_ocr"
    external = True

    def __init__(
        self,
        api_key: str,
        model: str = "mistral-ocr-latest",
        *,
        base_url: str = "https://api.mistral.ai",
        http: httpx.Client | None = None,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self._http = http or httpx.Client(timeout=httpx.Timeout(120.0))

    def available(self) -> bool:
        return bool(self.api_key)

    def recognise(self, image: Image.Image, page: int, languages: str) -> PageText:
        buf = io.BytesIO()
        image.convert("RGB").save(buf, format="PNG", optimize=True)
        data_url = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
        r = self._http.post(
            f"{self.base_url}/v1/ocr",
            headers={"authorization": f"Bearer {self.api_key}", "content-type": "application/json"},
            json={"model": self.model, "document": {"type": "image_url", "image_url": data_url}},
        )
        r.raise_for_status()
        body = r.json()
        pages = body.get("pages") or []
        markdown = "\n".join(str(p.get("markdown", "")) for p in pages if isinstance(p, dict))
        text = "\n".join(_MD_STRIP.sub("", ln).strip() for ln in markdown.splitlines())
        w, h = image.size
        words = [Word(tok, page, 0.0, 0.0, 0.0, 0.0, 0.9) for tok in text.split()]  # no boxes from the API
        return PageText(index=page, width=w, height=h, words=words, text=text.strip())


def page_images_png(images: list[Image.Image], *, max_pages: int = 4, max_width: int = 1200) -> list[bytes]:
    """Downscaled PNG pages for vision-capable providers (bounded in count and size)."""
    out: list[bytes] = []
    for img in images[:max_pages]:
        im = img.convert("RGB")
        if im.width > max_width:
            im = im.resize((max_width, int(im.height * max_width / im.width)))
        buf = io.BytesIO()
        im.save(buf, format="PNG", optimize=True)
        out.append(buf.getvalue())
    return out
