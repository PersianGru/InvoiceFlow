"""Security intake: virus scan, file type by magic bytes, size limits, PDF sanitising.

The original bytes are archived untouched (tamper-evident storage); processing uses the
sanitised copy. Production requires a ClamAV daemon (fail closed); development and tests may run
without one (``VIRUS_SCAN_REQUIRED=false``), which is logged.
"""

from __future__ import annotations

import asyncio
import io
import logging
import struct
from dataclasses import dataclass
from typing import Protocol

from pypdf import PasswordType, PdfReader, PdfWriter
from pypdf.generic import DictionaryObject, NameObject

log = logging.getLogger("invoiceflow.pipeline.security")

MAX_UPLOAD_BYTES = 50 * 1024 * 1024
MAX_PDF_PAGES = 300
MAX_ZIP_ENTRIES = 200


class IntakeRejectedError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(code)
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class ScanResult:
    clean: bool
    engine: str
    signature: str | None = None


class VirusScanner(Protocol):
    async def scan(self, data: bytes) -> ScanResult: ...


class ClamdScanner:
    """clamd INSTREAM over TCP (the official protocol; no third-party client needed)."""

    def __init__(self, host: str, port: int = 3310, timeout: float = 60.0, chunk: int = 64 * 1024) -> None:
        self.host, self.port, self.timeout, self.chunk = host, port, timeout, chunk

    async def scan(self, data: bytes) -> ScanResult:
        reader, writer = await asyncio.wait_for(asyncio.open_connection(self.host, self.port), self.timeout)
        try:
            writer.write(b"zINSTREAM\0")
            for i in range(0, len(data), self.chunk):
                part = data[i : i + self.chunk]
                writer.write(struct.pack(">I", len(part)) + part)
            writer.write(struct.pack(">I", 0))
            await writer.drain()
            answer = (
                (await asyncio.wait_for(reader.read(1024), self.timeout))
                .decode(errors="replace")
                .strip("\0\n ")
            )
        finally:
            writer.close()
        if answer.endswith("OK"):
            return ScanResult(True, "clamav")
        if answer.endswith("FOUND"):
            sig = answer.split(":", 1)[1].rsplit(" FOUND", 1)[0].strip() if ":" in answer else answer
            return ScanResult(False, "clamav", sig)
        raise RuntimeError(f"clamd answered: {answer[:80]}")


class NoScanner:
    """No scanning (development/tests only; refused in production by the settings check)."""

    async def scan(self, data: bytes) -> ScanResult:
        return ScanResult(True, "none")


class EicarTestScanner(NoScanner):
    """Tests: flags the EICAR test string, passes everything else."""

    async def scan(self, data: bytes) -> ScanResult:
        if b"EICAR-STANDARD-ANTIVIRUS-TEST-FILE" in data:
            return ScanResult(False, "test", "Eicar-Test-Signature")
        return ScanResult(True, "test")


# ---- file type by magic bytes ---------------------------------------------------------------------
_MAGIC: tuple[tuple[bytes, str, str], ...] = (
    (b"%PDF-", "application/pdf", "pdf"),
    (b"\xff\xd8\xff", "image/jpeg", "jpg"),
    (b"\x89PNG\r\n\x1a\n", "image/png", "png"),
    (b"II*\x00", "image/tiff", "tiff"),
    (b"MM\x00*", "image/tiff", "tiff"),
    (b"PK\x03\x04", "application/zip", "zip"),
)

ALLOWED_MIME = frozenset(
    {
        "application/pdf",
        "image/jpeg",
        "image/png",
        "image/tiff",
        "image/webp",
        "image/heic",
        "application/xml",
        "application/zip",
    }
)


def detect_mime(data: bytes) -> tuple[str, str]:
    """(mime, extension) from the bytes, never from the client's claim."""
    head = data[:16]
    for magic, mime, ext in _MAGIC:
        if head.startswith(magic):
            return mime, ext
    if head[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp", "webp"
    if data[4:12] in (b"ftypheic", b"ftypheix", b"ftypmif1", b"ftypheif"):
        return "image/heic", "heic"
    stripped = data.lstrip(b"\xef\xbb\xbf \t\r\n")
    if stripped.startswith(b"<?xml") or (stripped.startswith(b"<") and b">" in stripped[:4096]):
        return "application/xml", "xml"
    return "application/octet-stream", "bin"


def check_size(data: bytes, limit: int = MAX_UPLOAD_BYTES) -> None:
    if not data:
        raise IntakeRejectedError("empty_file", "The file is empty.")
    if len(data) > limit:
        raise IntakeRejectedError("too_large", f"The file is larger than {limit // (1024 * 1024)} MB.")


def check_type(data: bytes) -> tuple[str, str]:
    mime, ext = detect_mime(data)
    if mime not in ALLOWED_MIME:
        raise IntakeRejectedError(
            "unsupported_type", "Only PDF, JPEG, PNG, TIFF, WebP, HEIC, XML and ZIP files are accepted."
        )
    return mime, ext


# ---- PDF sanitising ----------------------------------------------------------------------------------
_DANGEROUS_KEYS = (
    "/OpenAction",
    "/AA",
    "/JavaScript",
    "/JS",
    "/Launch",
    "/URI",
    "/SubmitForm",
    "/ImportData",
    "/GoToR",
)


def sanitize_pdf(data: bytes) -> tuple[bytes, list[str], int]:
    """Remove scripts and actions (keeps embedded files: ZUGFeRD/Factur-X XML lives there).

    Returns (sanitised bytes, removed keys, page count). Raises ``IntakeRejectedError`` for unreadable PDFs.
    """
    try:
        reader = PdfReader(io.BytesIO(data), strict=False)
        if reader.is_encrypted:
            try:
                result = reader.decrypt("")
            except Exception as e:  # noqa: BLE001
                raise IntakeRejectedError("encrypted_pdf", "The PDF is password protected.") from e
            if result == PasswordType.NOT_DECRYPTED:
                raise IntakeRejectedError("encrypted_pdf", "The PDF is password protected.")
        pages = len(reader.pages)
    except IntakeRejectedError:
        raise
    except Exception as e:  # noqa: BLE001
        raise IntakeRejectedError("unreadable_pdf", "The PDF could not be read.") from e
    if pages == 0:
        raise IntakeRejectedError("empty_pdf", "The PDF has no pages.")
    if pages > MAX_PDF_PAGES:
        raise IntakeRejectedError("too_many_pages", f"The PDF has more than {MAX_PDF_PAGES} pages.")
    removed: list[str] = []
    writer = PdfWriter(clone_from=reader)
    root = writer._root_object  # noqa: SLF001 - pypdf exposes no public accessor for the catalog
    for key in ("/OpenAction", "/AA", "/AcroForm"):
        if key in root:
            if key == "/AcroForm":
                # keep form fields but drop their actions
                _strip_actions(root[key].get_object())
                continue
            del root[NameObject(key)]
            removed.append(key)
    names = root.get("/Names")
    if names is not None:
        names_obj = names.get_object()
        if isinstance(names_obj, DictionaryObject) and "/JavaScript" in names_obj:
            del names_obj[NameObject("/JavaScript")]
            removed.append("/Names/JavaScript")
    for page in writer.pages:
        if "/AA" in page:
            del page[NameObject("/AA")]
            removed.append("/Page/AA")
        annots = page.get("/Annots")
        if annots is not None:
            kept = []
            for a in annots.get_object():
                obj = a.get_object()
                if not isinstance(obj, DictionaryObject):
                    continue
                if obj.get("/Subtype") in ("/Link", "/Widget", "/FileAttachment") and _has_dangerous(obj):
                    removed.append(f"/Annot{obj.get('/Subtype')}")
                    continue
                kept.append(a)
            if len(kept) != len(annots.get_object()):
                page[NameObject("/Annots")] = type(annots.get_object())(kept)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue(), sorted(set(removed)), pages


def _has_dangerous(obj: DictionaryObject) -> bool:
    action = obj.get("/A")
    if action is None:
        return "/AA" in obj
    act = action.get_object()
    return isinstance(act, DictionaryObject) and act.get("/S") in (
        "/JavaScript",
        "/Launch",
        "/SubmitForm",
        "/ImportData",
        "/GoToR",
    )


def _strip_actions(form: object) -> None:
    if not isinstance(form, DictionaryObject):
        return
    for key in ("/XFA", "/AA"):
        if key in form:
            del form[NameObject(key)]
    fields = form.get("/Fields")
    if fields is None:
        return
    for f in fields.get_object():
        fo = f.get_object()
        if isinstance(fo, DictionaryObject):
            for key in ("/AA", "/A"):
                if key in fo:
                    del fo[NameObject(key)]


@dataclass(frozen=True, slots=True)
class IntakeCheck:
    mime: str
    ext: str
    size: int
    scan: ScanResult
    processed_bytes: bytes  # sanitised copy for processing (original is archived unchanged)
    sanitised_removed: tuple[str, ...]
    page_count: int | None


async def run_intake_checks(
    data: bytes, scanner: VirusScanner, *, size_limit: int = MAX_UPLOAD_BYTES
) -> IntakeCheck:
    check_size(data, size_limit)
    mime, ext = check_type(data)
    scan = await scanner.scan(data)
    if not scan.clean:
        log.warning("virus scan flagged an upload (%s)", scan.signature)
        raise IntakeRejectedError("malware", "The file was rejected by the virus scanner.")
    processed: bytes = data
    removed: tuple[str, ...] = ()
    pages: int | None = None
    if mime == "application/pdf":
        processed, removed_list, pages = sanitize_pdf(data)
        removed = tuple(removed_list)
        if removed:
            log.info("pdf sanitised: removed %s", ",".join(removed))
    return IntakeCheck(mime, ext, len(data), scan, processed, removed, pages)
