"""Format detection: structured e-invoices first, always.

* XML uploads: UBL 2.1 (Peppol BIS 3.0, XRechnung UBL), CII (XRechnung CII, Factur-X/ZUGFeRD),
  FatturaPA, Poland FA(3)/FA(2).
* PDFs: embedded XML attachments (Factur-X ``factur-x.xml``, ZUGFeRD ``zugferd-invoice.xml``,
  XRechnung ``xrechnung.xml``, any ``*.xml`` that parses as UBL/CII).
* ZIP: entries are listed for the caller (one document per entry; zip-bomb guard).
"""

from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass

from defusedxml import ElementTree as SafeET
from pypdf import PdfReader

from app.pipeline.security import MAX_ZIP_ENTRIES, IntakeRejectedError

NS_UBL_INVOICE = "urn:oasis:names:specification:ubl:schema:xsd:Invoice-2"
NS_UBL_CREDIT = "urn:oasis:names:specification:ubl:schema:xsd:CreditNote-2"
NS_CII = "urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100"
NS_FATTURAPA = "http://ivaservizi.agenziaentrate.gov.it/docs/xsd/fatture/v1.2"
NS_FA_PL_PREFIX = "http://crd.gov.pl/wzor/"

STRUCTURED_FORMATS = ("ubl", "cii", "xrechnung", "facturx", "zugferd", "fatturapa", "fa3")
_EMBEDDED_NAMES = (
    "factur-x.xml",
    "zugferd-invoice.xml",
    "zugferd-invoice.xml",
    "xrechnung.xml",
    "order-x.xml",
)


@dataclass(frozen=True, slots=True)
class StructuredXml:
    format: str  # one of STRUCTURED_FORMATS
    syntax: str  # "ubl" | "cii" | "fatturapa" | "fa3"
    root_tag: str
    xml: bytes
    origin: str  # "upload" | "pdf:<attachment name>"


def _localname(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _namespace(tag: str) -> str:
    return tag[1:].split("}", 1)[0] if tag.startswith("{") else ""


def _find_text(root: SafeET.Element, *local_names: str) -> str | None:
    """Depth-first search for the first element whose local name is in ``local_names``."""
    for el in root.iter():
        if _localname(el.tag) in local_names and el.text and el.text.strip():
            return str(el.text).strip()
    return None


def detect_structured_xml(xml: bytes, origin: str = "upload") -> StructuredXml | None:
    """Returns the detected structured format or None when the XML is not an e-invoice we parse."""
    if len(xml) > 20 * 1024 * 1024:
        return None
    try:
        root = SafeET.fromstring(xml)
    except Exception:  # noqa: BLE001 - not XML or malicious XML (defusedxml refuses entities)
        return None
    ns, local = _namespace(root.tag), _localname(root.tag)
    if ns in (NS_UBL_INVOICE, NS_UBL_CREDIT) and local in ("Invoice", "CreditNote"):
        customization = (_find_text(root, "CustomizationID") or "").lower()
        fmt = "xrechnung" if "xrechnung" in customization else "ubl"
        return StructuredXml(fmt, "ubl", local, xml, origin)
    if ns == NS_CII and local == "CrossIndustryInvoice":
        guideline = (_find_text(root, "ID") or "").lower()
        # the first ID in document order is the guideline parameter id in CII
        for el in root.iter():
            if _localname(el.tag) == "GuidelineSpecifiedDocumentContextParameter":
                guideline = (_find_text(el, "ID") or "").lower()
                break
        if "xrechnung" in guideline:
            fmt = "xrechnung"
        elif "factur-x" in guideline:
            fmt = "facturx"
        elif "zugferd" in guideline:
            fmt = "zugferd"
        else:
            fmt = "cii"
        return StructuredXml(fmt, "cii", local, xml, origin)
    if ns.startswith(NS_FATTURAPA) or local in ("FatturaElettronica",):
        return StructuredXml("fatturapa", "fatturapa", local, xml, origin)
    if ns.startswith(NS_FA_PL_PREFIX) and local == "Faktura":
        return StructuredXml("fa3", "fa3", local, xml, origin)
    return None


def embedded_xml_in_pdf(pdf: bytes) -> list[StructuredXml]:
    """Factur-X / ZUGFeRD / XRechnung attachments inside a PDF (PDF/A-3)."""
    found: list[StructuredXml] = []
    try:
        reader = PdfReader(io.BytesIO(pdf), strict=False)
        attachments = reader.attachments
    except Exception:  # noqa: BLE001
        return found
    for name, contents in attachments.items():
        lname = name.lower()
        if not lname.endswith(".xml"):
            continue
        for blob in contents if isinstance(contents, list) else [contents]:
            det = detect_structured_xml(bytes(blob), origin=f"pdf:{name}")
            if det is not None:
                if det.syntax == "cii" and det.format == "cii" and lname in _EMBEDDED_NAMES:
                    det = StructuredXml(
                        "facturx" if "factur" in lname else "zugferd",
                        "cii",
                        det.root_tag,
                        det.xml,
                        det.origin,
                    )
                found.append(det)
    return found


@dataclass(frozen=True, slots=True)
class ZipEntry:
    name: str
    data: bytes


def unpack_zip(
    data: bytes, *, max_entries: int = MAX_ZIP_ENTRIES, max_total: int = 200 * 1024 * 1024
) -> list[ZipEntry]:
    """List the files of a ZIP upload (one document each). Guards against zip bombs and traversal."""
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as e:
        raise IntakeRejectedError("bad_zip", "The ZIP file could not be read.") from e
    entries: list[ZipEntry] = []
    total = 0
    for info in zf.infolist():
        if info.is_dir() or info.filename.startswith("__MACOSX/") or info.filename.endswith("/"):
            continue
        if len(entries) >= max_entries:
            raise IntakeRejectedError("zip_too_many", f"The ZIP has more than {max_entries} files.")
        if info.file_size > 50 * 1024 * 1024 or (
            info.compress_size and info.file_size / max(info.compress_size, 1) > 200
        ):
            raise IntakeRejectedError(
                "zip_suspicious", "The ZIP contains a file that is too large or too highly compressed."
            )
        total += info.file_size
        if total > max_total:
            raise IntakeRejectedError("zip_too_large", "The ZIP unpacks to more than 200 MB.")
        name = info.filename.replace("\\", "/").split("/")[-1]
        if not name or name.startswith("."):
            continue
        entries.append(ZipEntry(name, zf.read(info)))
    if not entries:
        raise IntakeRejectedError("zip_empty", "The ZIP contains no files.")
    return entries
