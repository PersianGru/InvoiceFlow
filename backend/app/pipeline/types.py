"""Shared pipeline types."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from app.schemas.invoice import Invoice, SourceLocation, ValueSource


@dataclass(frozen=True, slots=True)
class Word:
    """One recognised word with its relative bounding box (0..1) on its page."""

    text: str
    page: int
    x0: float
    y0: float
    x1: float
    y1: float
    confidence: float = 1.0

    @property
    def location(self) -> SourceLocation:
        return SourceLocation(page=self.page, x0=self.x0, y0=self.y0, x1=self.x1, y1=self.y1)


@dataclass(slots=True)
class PageText:
    index: int  # 1-based
    width: int
    height: int
    words: list[Word] = field(default_factory=list)
    text: str = ""  # line-oriented text of the page

    @property
    def mean_confidence(self) -> float:
        return sum(w.confidence for w in self.words) / len(self.words) if self.words else 0.0


@dataclass(slots=True)
class DocumentText:
    pages: list[PageText]
    source: str  # "text_layer" | "ocr:<engine>" | "structured"
    language: str | None = None
    page_images: list[bytes] = field(default_factory=list)  # downscaled PNGs for vision providers (bounded)

    @property
    def full_text(self) -> str:
        return "\n\f\n".join(p.text for p in self.pages)

    @property
    def words(self) -> list[Word]:
        return [w for p in self.pages for w in p.words]

    @property
    def mean_confidence(self) -> float:
        ws = self.words
        return sum(w.confidence for w in ws) / len(ws) if ws else 0.0


@dataclass(slots=True)
class ExtractionContext:
    firm_id: str
    document_id: str
    client_company_id: str | None
    buyer_vat_ids: list[str] = field(default_factory=list)  # the client's own VAT ids (direction detection)
    supplier_hints: dict[str, Any] = field(default_factory=dict)  # supplier profile field rules / examples
    language_hint: str | None = None


@dataclass(slots=True)
class ExtractionResult:
    invoice: Invoice
    provider: str
    model: str | None = None
    prompt_version: str | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    duration_ms: int | None = None


class ExtractionProvider(Protocol):
    name: str
    external: bool  # True when data leaves the deployment (cloud)

    async def available(self) -> bool: ...

    async def extract(self, text: DocumentText, ctx: ExtractionContext) -> ExtractionResult: ...


def field_meta(
    confidence: float, source: ValueSource, location: SourceLocation | None = None, **extra: Any
) -> dict[str, Any]:
    return {
        "confidence": round(confidence, 3),
        "source": source.value,
        "location": location.model_dump() if location else None,
        "ai_generated": source not in (ValueSource.structured, ValueSource.human),
        **extra,
    }
