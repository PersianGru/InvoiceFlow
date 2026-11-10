"""Text acquisition: PDF text layer when present, otherwise OCR on page images.

OCR engines are pluggable behind ``OcrEngine``; Tesseract (``pytesseract``) is the default when the
binary is installed. PDFs without a text layer need page rendering (through an
optional renderer; images are OCR'd directly).
"""

from __future__ import annotations

import asyncio
import io
import logging
import re
import shutil
from collections.abc import Callable
from typing import Protocol

from PIL import Image, ImageOps
from pypdf import PdfReader

from app.pipeline.preprocess import preprocess_image
from app.pipeline.types import DocumentText, PageText, Word

log = logging.getLogger("invoiceflow.pipeline.text")

_WORD_RE = re.compile(r"\S+")
_LANG_HINTS = {
    "de": (
        "rechnung",
        "rechnungsnummer",
        "umsatzsteuer",
        "gesamtbetrag",
        "betrag",
        "mwst",
        "ust-idnr",
        "zahlbar",
        "lieferdatum",
    ),
    "fr": ("facture", "montant", "tva", "total ttc", "total ht", "échéance", "siret", "règlement"),
    "nl": ("factuur", "factuurnummer", "btw", "totaal", "bedrag", "vervaldatum", "kvk"),
    "es": ("factura", "importe", "iva", "total", "vencimiento", "nif", "cif", "base imponible"),
    "it": ("fattura", "importo", "iva", "totale", "scadenza", "partita iva", "imponibile", "codice fiscale"),
    "pl": (
        "faktura",
        "kwota",
        "vat",
        "razem",
        "termin płatności",
        "nip",
        "sprzedawca",
        "nabywca",
        "brutto",
        "netto",
    ),
    "lt": ("pvm", "kvitas", "sąskaita", "faktūra", "mokėti", "nuolaida", "grąža", "suma", "įmonės kodas"),
    "en": ("invoice", "amount", "vat", "total", "due date", "invoice number", "subtotal", "bill to"),
}


class OcrEngine(Protocol):
    name: str

    def available(self) -> bool: ...

    def recognise(self, image: Image.Image, page: int, languages: str) -> PageText: ...


class TesseractEngine:
    name = "tesseract"

    def __init__(self, languages: str = "eng+deu+fra+nld+spa+ita+pol") -> None:
        self.languages = languages

    def available(self) -> bool:
        return shutil.which("tesseract") is not None

    def _langs(self) -> str:
        try:
            import pytesseract

            installed = set(pytesseract.get_languages(config=""))
        except Exception:  # noqa: BLE001
            return "eng"
        wanted = [lang for lang in self.languages.split("+") if lang in installed]
        return "+".join(wanted) or "eng"

    def recognise(self, image: Image.Image, page: int, languages: str) -> PageText:
        import pytesseract

        langs = languages or self._langs()
        data = pytesseract.image_to_data(
            image, lang=langs, output_type=pytesseract.Output.DICT, config="--psm 6"
        )
        w, h = image.size
        words: list[Word] = []
        lines: dict[tuple[int, int, int], list[str]] = {}
        for i, txt in enumerate(data["text"]):
            txt = (txt or "").strip()
            if not txt:
                continue
            conf = float(data["conf"][i])
            conf = max(0.0, min(1.0, conf / 100.0)) if conf >= 0 else 0.0
            x, y, ww, hh = data["left"][i], data["top"][i], data["width"][i], data["height"][i]
            words.append(Word(txt, page, x / w, y / h, (x + ww) / w, (y + hh) / h, conf))
            key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
            lines.setdefault(key, []).append(txt)
        text = "\n".join(" ".join(v) for _, v in sorted(lines.items()))
        return PageText(index=page, width=w, height=h, words=words, text=text)


class NoOcrEngine:
    name = "none"

    def available(self) -> bool:
        return False

    def recognise(self, image: Image.Image, page: int, languages: str) -> PageText:
        raise RuntimeError("no OCR engine is available")


def pdf_text_layer(pdf: bytes) -> DocumentText | None:
    """Words with boxes from the PDF's own text layer; None when the PDF carries no usable text."""
    try:
        reader = PdfReader(io.BytesIO(pdf), strict=False)
    except Exception:  # noqa: BLE001
        return None
    pages: list[PageText] = []
    total_chars = 0
    for idx, page in enumerate(reader.pages, start=1):
        box = page.mediabox
        width, height = float(box.width) or 595.0, float(box.height) or 842.0
        words: list[Word] = []
        lines: list[str] = []

        def visitor(
            text: str,
            cm: list[float],
            tm: list[float],
            _font: object,
            _size: object,
            *,
            _page: int = idx,
            width: float = width,
            height: float = height,
            words: list[Word] = words,
            lines: list[str] = lines,
        ) -> None:
            t = text.strip()
            if not t:
                return
            x, y = tm[4] + cm[4], tm[5] + cm[5]
            rx, ry = max(0.0, min(1.0, x / width)), max(0.0, min(1.0, 1 - y / height))
            for m in _WORD_RE.finditer(text):
                token = m.group(0)
                approx_w = min(0.95, len(token) * 0.0085)
                offset = min(0.95, m.start() * 0.0085)
                words.append(
                    Word(
                        token,
                        _page,
                        min(1.0, rx + offset),
                        max(0.0, ry - 0.012),
                        min(1.0, rx + offset + approx_w),
                        ry,
                        0.99,
                    )
                )
            lines.append(t)

        try:
            page.extract_text(visitor_text=visitor)
        except Exception as e:  # noqa: BLE001
            log.warning("page %d: text extraction failed (%s)", idx, type(e).__name__)
            continue
        text = "\n".join(lines)
        total_chars += len(text)
        pages.append(PageText(index=idx, width=int(width), height=int(height), words=words, text=text))
    if not pages or total_chars < 20:
        return None
    return DocumentText(
        pages=pages, source="text_layer", language=guess_language("\n".join(p.text for p in pages))
    )


def load_image(data: bytes) -> Image.Image:
    img = Image.open(io.BytesIO(data))
    img.load()
    return ImageOps.exif_transpose(img) or img


PdfRenderer = Callable[[bytes], list[Image.Image]]


async def ocr_images(images: list[Image.Image], engine: OcrEngine, languages: str = "") -> DocumentText:
    pages: list[PageText] = []
    for idx, img in enumerate(images, start=1):
        prepared = preprocess_image(img)
        pages.append(await asyncio.to_thread(engine.recognise, prepared, idx, languages))
    return DocumentText(
        pages=pages, source=f"ocr:{engine.name}", language=guess_language("\n".join(p.text for p in pages))
    )


def guess_language(text: str) -> str | None:
    low = text.lower()
    best, best_score = None, 0
    for lang, hints in _LANG_HINTS.items():
        score = sum(low.count(h) for h in hints)
        if score > best_score:
            best, best_score = lang, score
    return best if best_score >= 2 else None
