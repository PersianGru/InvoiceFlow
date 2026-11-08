"""Structured e-invoice parsers: XML → canonical Invoice, no AI, every field at confidence 1.0."""

from __future__ import annotations

from app.pipeline.detect import StructuredXml
from app.pipeline.formats import cii, fa3, fatturapa, ubl
from app.pipeline.formats.common import ParsedInvoice

PARSERS = {"ubl": ubl.parse, "cii": cii.parse, "fatturapa": fatturapa.parse, "fa3": fa3.parse}


def parse_structured(det: StructuredXml) -> ParsedInvoice:
    parsed = PARSERS[det.syntax](det.xml)
    parsed.structured_format = det.format
    return parsed


__all__ = ["PARSERS", "ParsedInvoice", "parse_structured"]
