"""PDF page rendering for scanned PDFs (no text layer) through pdfium (``pypdfium2``, a wheel with
the binary included: no poppler needed). Pages become images for the OCR engine."""

from __future__ import annotations

import logging

from PIL import Image

log = logging.getLogger("invoiceflow.pipeline.render")

MAX_PAGES = 50


def render_pdf_pages(pdf: bytes, *, scale: float = 2.0, max_pages: int = MAX_PAGES) -> list[Image.Image]:
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(pdf)
    images: list[Image.Image] = []
    try:
        for i in range(min(len(doc), max_pages)):
            page = doc[i]
            bitmap = page.render(scale=scale)
            images.append(bitmap.to_pil().convert("L"))
            page.close()
    finally:
        doc.close()
    return images


def renderer_available() -> bool:
    try:
        import pypdfium2  # noqa: F401
    except ImportError:
        return False
    return True
