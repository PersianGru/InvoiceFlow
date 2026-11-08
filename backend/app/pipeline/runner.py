"""Orchestration: one document through security → detection → structured parse or text+extraction →
classification → supplier profile → validation → review decision. Every step is timed and recorded
in the document timeline. Never runs for an unlicensed firm (the gate stays server-side)."""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.licensing.service import LicenseService
from app.models.client_company import LegalEntity
from app.models.document import Document, DocumentStatus
from app.models.firm import Firm
from app.models.pipeline import AiCallLog, DocumentEvent, Extraction
from app.pipeline import detect, security
from app.pipeline.classify import apply_classification, classify
from app.pipeline.extract.merge import min_header_confidence
from app.pipeline.extract.router import ExtractionRouter
from app.pipeline.formats import ParsedInvoice, parse_structured
from app.pipeline.preprocess import hamming, perceptual_hash
from app.pipeline.supplier import find_profile, history_of, upsert_profile
from app.pipeline.text import OcrEngine, PdfRenderer, load_image, ocr_images, pdf_text_layer
from app.pipeline.types import DocumentText, ExtractionContext, ExtractionResult, PageText
from app.pipeline.validation import ValidationContext, run_validations, worst_level
from app.pipeline.validation.currency import RatesProvider
from app.pipeline.validation.vies import ViesClient
from app.schemas.invoice import Invoice, ReviewStatus, SourceInfo, ValidationLevel, ValidationResult
from app.storage.base import Storage
from app.webhooks import document_payload, emit_event

log = logging.getLogger("invoiceflow.pipeline.runner")


@dataclass(slots=True)
class RunnerDeps:
    storage: Storage
    scanner: security.VirusScanner
    ocr: OcrEngine
    router: ExtractionRouter
    license_service: LicenseService
    vies: ViesClient | None = None
    rates: RatesProvider | None = None
    pdf_renderer: PdfRenderer | None = None
    ocr_languages: str = ""
    cloud_ocr: OcrEngine | None = None  # external engine, used only inside the cloud allowance
    attach_page_images: bool = False  # keep downscaled page PNGs for vision-capable providers


@dataclass(slots=True)
class Outcome:
    status: DocumentStatus
    review_status: ReviewStatus
    invoice: Invoice | None
    results: list[ValidationResult]
    error: str | None = None


class ProcessingRunner:
    def __init__(self, deps: RunnerDeps) -> None:
        self.d = deps

    # ---- helpers -----------------------------------------------------------------------------
    async def _event(
        self,
        session: AsyncSession,
        doc: Document,
        step: str,
        status: str,
        t0: float | None = None,
        **detail: Any,
    ) -> None:
        session.add(
            DocumentEvent(
                firm_id=doc.firm_id,
                document_id=doc.id,
                step=step,
                status=status,
                detail=detail or None,
                duration_ms=int((time.monotonic() - t0) * 1000) if t0 else None,
            )
        )

    async def _own_vat_ids(self, session: AsyncSession, doc: Document) -> list[str]:
        if doc.client_company_id is None:
            return []
        rows = (
            (
                await session.execute(
                    select(LegalEntity.vat_id).where(LegalEntity.client_company_id == doc.client_company_id)
                )
            )
            .scalars()
            .all()
        )
        return [v for v in rows if v]

    def _source(self, doc: Document, structured: str | None) -> SourceInfo:
        return SourceInfo(
            channel=doc.channel.value if hasattr(doc.channel, "value") else str(doc.channel),
            sender_identity=doc.sender_identity or None,
            received_at=doc.received_at or datetime.now(UTC),
            original_file_id=str(doc.id),
            original_file_sha256=doc.sha256,
            original_filename=doc.original_filename or None,
            mime_type=doc.mime_type,
            page_count=doc.page_count,
            structured_format=structured,
        )

    def _invoice_from_parsed(self, doc: Document, parsed: ParsedInvoice) -> Invoice:
        return Invoice.model_validate(
            {
                "id": str(doc.id),
                "firm_id": str(doc.firm_id),
                "client_company_id": str(doc.client_company_id) if doc.client_company_id else None,
                "source": self._source(doc, parsed.structured_format).model_dump(),
                "meta": parsed.meta(),
                **parsed.fields,
            }
        )

    async def _duplicate_finder(self, session: AsyncSession, doc: Document):  # type: ignore[no-untyped-def]
        async def find(inv: Invoice, phash: str | None) -> list[dict[str, Any]]:
            out: list[dict[str, Any]] = []
            q = select(Document).where(
                Document.firm_id == doc.firm_id, Document.id != doc.id, Document.deleted_at.is_(None)
            )
            rows = (
                (await session.execute(q.where(Document.status != DocumentStatus.rejected).limit(2000)))
                .scalars()
                .all()
            )
            number = (inv.number or "").strip().lower()
            vat = (inv.supplier.vat_id or "").upper()
            gross = inv.totals.gross_total
            for r in rows:
                r_num = (r.invoice_number or "").strip().lower()
                same_amount = (
                    gross is not None
                    and r.gross_total is not None
                    and abs(r.gross_total - gross) <= max(Decimal("0.01"), abs(gross) * Decimal("0.01"))
                )
                if (
                    number
                    and r_num == number
                    and (not vat or (r.supplier_vat_id or "").upper() == vat)
                    and same_amount
                ):
                    out.append({"id": r.id, "kind": "exact"})
                elif number and r_num == number and same_amount:
                    out.append({"id": r.id, "kind": "fuzzy"})
                elif (
                    vat
                    and (r.supplier_vat_id or "").upper() == vat
                    and same_amount
                    and inv.issue_date
                    and r.issue_date == inv.issue_date
                ):
                    out.append({"id": r.id, "kind": "fuzzy"})
                elif phash and r.phash and hamming(phash, r.phash) <= 6:
                    out.append({"id": r.id, "kind": "image"})
            return out

        return find

    # ---- main -------------------------------------------------------------------------------
    async def process(self, session: AsyncSession, doc: Document, firm: Firm) -> Outcome:
        if doc.processing_restricted:
            await self._event(session, doc, "restricted", "skipped")
            return Outcome(doc.status, ReviewStatus.pending, None, [], "processing_restricted")
        snap = self.d.license_service.snapshot(firm)
        if not snap.core_on:
            await self._event(session, doc, "license", "skipped", state=snap.state)
            doc.error = "license_required"
            return Outcome(doc.status, ReviewStatus.pending, None, [], "license_required")
        doc.status = DocumentStatus.processing
        doc.error = None
        t_all = time.monotonic()
        try:
            data = await self.d.storage.get(doc.storage_key)
        except Exception as e:  # noqa: BLE001
            await self._event(session, doc, "load", "error", error=type(e).__name__)
            doc.status, doc.error = DocumentStatus.failed, "storage_error"
            return Outcome(doc.status, ReviewStatus.pending, None, [], "storage_error")

        # 1. security intake
        t0 = time.monotonic()
        try:
            check = await security.run_intake_checks(data, self.d.scanner)
        except security.IntakeRejectedError as e:
            await self._event(session, doc, "security", "error", t0, code=e.code)
            doc.status = DocumentStatus.quarantined if e.code == "malware" else DocumentStatus.failed
            doc.error = e.code
            return Outcome(doc.status, ReviewStatus.pending, None, [], e.code)
        doc.mime_type = check.mime
        doc.page_count = check.page_count
        await self._event(
            session,
            doc,
            "security",
            "ok",
            t0,
            mime=check.mime,
            scan=check.scan.engine,
            sanitised=list(check.sanitised_removed),
        )

        # 2./3. structured e-invoice fast path
        t0 = time.monotonic()
        structured = None
        if check.mime == "application/xml":
            structured = detect.detect_structured_xml(check.processed_bytes)
        elif check.mime == "application/pdf":
            found = detect.embedded_xml_in_pdf(check.processed_bytes)
            structured = found[0] if found else None
        invoice: Invoice | None = None
        extraction: ExtractionResult | None = None
        text: DocumentText | None = None
        phash: str | None = None
        if structured is not None:
            try:
                parsed = parse_structured(structured)
                invoice = self._invoice_from_parsed(doc, parsed)
                doc.structured_format = structured.format
                await self._event(
                    session, doc, "structured", "ok", t0, format=structured.format, origin=structured.origin
                )
            except Exception as e:  # noqa: BLE001
                await self._event(
                    session,
                    doc,
                    "structured",
                    "warning",
                    t0,
                    format=structured.format,
                    error=type(e).__name__,
                )
                invoice = None
        else:
            await self._event(
                session,
                doc,
                "structured",
                "skipped",
                t0,
                reason="not an e-invoice" if check.mime != "application/xml" else "unknown xml",
            )
            if check.mime == "application/xml":
                doc.status, doc.error = DocumentStatus.failed, "unknown_xml"
                return Outcome(doc.status, ReviewStatus.pending, None, [], "unknown_xml")

        # 4./5. text + extraction
        if invoice is None:
            cloud_allowed, cap_hit = await self._cloud_allowance(session, firm, snap)
            t0 = time.monotonic()
            text, phash, text_error, cloud_ocr_meta = await self._acquire_text(
                check, cloud_allowed and firm.ai_routing_policy != "local_only"
            )
            if text is None:
                await self._event(session, doc, "text", "error", t0, error=text_error)
                doc.status, doc.error, doc.phash = DocumentStatus.needs_review, text_error, phash
                doc.review_status = ReviewStatus.needs_review.value
                return Outcome(doc.status, ReviewStatus.needs_review, None, [], text_error)
            doc.language = text.language
            await self._event(
                session,
                doc,
                "text",
                "ok",
                t0,
                source=text.source,
                pages=len(text.pages),
                words=len(text.words),
                language=text.language,
                cloud_ocr=cloud_ocr_meta["provider"] if cloud_ocr_meta else None,
            )
            if cloud_ocr_meta:
                session.add(
                    AiCallLog(
                        firm_id=doc.firm_id,
                        document_id=doc.id,
                        provider=cloud_ocr_meta["provider"],
                        model=cloud_ocr_meta["model"],
                        purpose="ocr",
                        data_categories=["page_images"],
                        external=True,
                        duration_ms=cloud_ocr_meta["duration_ms"],
                    )
                )
            t0 = time.monotonic()
            own = await self._own_vat_ids(session, doc)
            ctx = ExtractionContext(
                str(doc.firm_id),
                str(doc.id),
                str(doc.client_company_id) if doc.client_company_id else None,
                own,
                language_hint=text.language,
            )
            if cloud_ocr_meta and firm.cloud_ai_monthly_cap > 0:
                cloud_allowed, cap_hit = await self._cloud_allowance(
                    session, firm, snap
                )  # the OCR call counts
            extraction, decision = await self.d.router.run(
                text,
                ctx,
                policy=firm.ai_routing_policy,
                cloud_allowed=cloud_allowed,
                threshold=float(firm.confidence_threshold),
            )
            invoice = extraction.invoice.model_copy(
                update={
                    "source": self._source(doc, None),
                    "language": extraction.invoice.language or text.language,
                }
            )
            await self._event(
                session,
                doc,
                "extract",
                "ok",
                t0,
                providers=decision.used,
                skipped=decision.skipped,
                policy=decision.policy,
                min_confidence=round(min_header_confidence(invoice), 3),
                cloud_fallback=(
                    "used: local result below the confidence threshold"
                    if decision.policy == "local_first_cloud_fallback" and "llm_cloud" in decision.used
                    else ("skipped: monthly cloud cap reached" if cap_hit else None)
                ),
            )
            for label in decision.used:
                if label.startswith("llm"):
                    session.add(
                        AiCallLog(
                            firm_id=doc.firm_id,
                            document_id=doc.id,
                            provider=label,
                            model=extraction.model,
                            purpose=(
                                "extraction_fallback"
                                if label == "llm_cloud" and decision.policy == "local_first_cloud_fallback"
                                else "extraction"
                            ),
                            data_categories=["invoice_text", "supplier_identity", "amounts"],
                            external=label == "llm_cloud",
                            tokens_in=extraction.tokens_in,
                            tokens_out=extraction.tokens_out,
                            duration_ms=extraction.duration_ms,
                        )
                    )

        # 6./7. supplier profile + classification
        t0 = time.monotonic()
        own = await self._own_vat_ids(session, doc)
        profile = await find_profile(session, doc.firm_id, invoice)
        if profile is not None and invoice.supplier.name is None and profile.name:
            invoice = invoice.model_copy(
                update={"supplier": invoice.supplier.model_copy(update={"name": profile.name})}
            )
        invoice = apply_classification(invoice, classify(invoice, own, profile))
        await self._event(
            session,
            doc,
            "classify",
            "ok",
            t0,
            document_type=invoice.document_type.value,
            direction=invoice.direction.value if invoice.direction else None,
            supplier_profile=str(profile.id) if profile else None,
        )

        # 8. validation
        t0 = time.monotonic()
        vctx = ValidationContext(
            today=datetime.now(UTC).date(),
            firm_country=firm.country,
            base_currency=firm.base_currency,
            supplier_history=history_of(profile),
            vies=self.d.vies,
            rates=self.d.rates,
            find_duplicates=await self._duplicate_finder(session, doc),
            phash=phash,
        )
        invoice, results = await run_validations(invoice, vctx)
        invoice = invoice.model_copy(update={"validation": results})
        level = worst_level(results)
        await self._event(
            session,
            doc,
            "validate",
            "ok" if level == ValidationLevel.passed else level.value,
            t0,
            checks=len(results),
            errors=sum(r.level == ValidationLevel.error for r in results),
            warnings=sum(r.level == ValidationLevel.warning for r in results),
        )

        # 9. review decision
        min_conf = 1.0 if structured is not None else min_header_confidence(invoice)
        if structured is None and extraction is not None:
            type_meta = extraction.invoice.meta.get("document_type")
            min_conf = min(min_conf, type_meta.confidence if type_meta is not None else 1.0)
        auto_ok = (
            firm.auto_approve
            and level != ValidationLevel.error
            and min_conf >= float(firm.confidence_threshold)
        )
        review = ReviewStatus.auto_approved if auto_ok else ReviewStatus.needs_review
        invoice = invoice.model_copy(update={"review_status": review})
        doc.status = DocumentStatus.approved if auto_ok else DocumentStatus.needs_review
        doc.review_status = review.value

        # 10. persist
        await upsert_profile(session, doc.firm_id, doc.client_company_id, invoice, profile)
        version = (
            int(
                (
                    await session.execute(
                        select(func.coalesce(func.max(Extraction.version), 0)).where(
                            Extraction.document_id == doc.id
                        )
                    )
                ).scalar_one()
            )
            + 1
        )
        session.add(
            Extraction(
                firm_id=doc.firm_id,
                document_id=doc.id,
                version=version,
                provider=(
                    "structured"
                    if structured is not None
                    else (extraction.provider if extraction else "none")
                ),
                model=extraction.model if extraction else None,
                prompt_version=extraction.prompt_version if extraction else None,
                invoice=invoice.model_dump(mode="json"),
                raw_text=text.full_text[:200_000] if text else None,
            )
        )
        doc.extracted = invoice.model_dump(mode="json")
        doc.document_type = invoice.document_type.value
        doc.direction = invoice.direction.value if invoice.direction else None
        doc.invoice_number = (invoice.number or "")[:128] or None
        doc.supplier_name = (invoice.supplier.name or "")[:200] or None
        doc.supplier_vat_id = (invoice.supplier.vat_id or "")[:32] or None
        doc.issue_date = invoice.issue_date
        doc.currency = invoice.currency.upper() if invoice.currency else None
        doc.gross_total = invoice.totals.gross_total
        doc.min_confidence = Decimal(str(round(min_conf, 3)))
        doc.phash = phash
        doc.processed_at = datetime.now(UTC)
        await self._event(
            session,
            doc,
            "done",
            "ok",
            t_all,
            doc_status=doc.status.value,
            review=review.value,
            min_confidence=round(min_conf, 3),
            version=version,
        )
        payload = {
            "document": document_payload(doc),
            "invoice": invoice.model_dump(mode="json"),
            "version": version,
            "provider": (
                "structured" if structured is not None else (extraction.provider if extraction else "none")
            ),
        }
        await emit_event(session, doc.firm_id, snap, "invoice.extracted", payload)
        await emit_event(
            session,
            doc.firm_id,
            snap,
            "invoice.approved" if auto_ok else "invoice.needs_review",
            payload,
        )
        return Outcome(doc.status, review, invoice, results)

    async def _cloud_allowance(self, session: AsyncSession, firm: Firm, snap: Any) -> tuple[bool, bool]:
        """(cloud allowed, monthly cap hit): entitlement, the firm's opt-in and the monthly cap."""
        cloud_allowed = snap.feature_on("cloud_ai") and bool(firm.cloud_ai_opt_in)
        cap_hit = False
        if cloud_allowed and firm.cloud_ai_monthly_cap > 0:
            month_start = datetime.now(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            used_calls = int(
                (
                    await session.execute(
                        select(func.count())
                        .select_from(AiCallLog)
                        .where(
                            AiCallLog.firm_id == firm.id,
                            AiCallLog.external.is_(True),
                            AiCallLog.created_at >= month_start,
                        )
                    )
                ).scalar_one()
            )
            if used_calls >= firm.cloud_ai_monthly_cap:
                cloud_allowed, cap_hit = False, True
        return cloud_allowed, cap_hit

    async def _acquire_text(
        self, check: security.IntakeCheck, cloud_ocr_ok: bool = False
    ) -> tuple[DocumentText | None, str | None, str | None, dict[str, Any] | None]:
        """Text from the PDF layer, else OCR on images (rendered PDF pages when a renderer exists).

        Local OCR first; the cloud OCR engine only inside the cloud allowance, when there is no local
        engine or its result is weak (few words, low confidence). Returns the text, the perceptual
        hash, an error code, and the cloud OCR call to log (provider, model, duration) if one was made.
        """
        phash: str | None = None
        if check.mime == "application/pdf":
            layer = pdf_text_layer(check.processed_bytes)
            if layer is not None:
                return layer, None, None, None
            if self.d.pdf_renderer is None:
                return None, None, "scanned_pdf_needs_renderer", None
            images = self.d.pdf_renderer(check.processed_bytes)
        else:
            try:
                images = [load_image(check.processed_bytes)]
            except Exception:  # noqa: BLE001
                return None, None, "unreadable_image", None
        if images:
            phash = perceptual_hash(images[0])
        local = self.d.ocr if self.d.ocr.available() else None
        cloud = self.d.cloud_ocr if cloud_ocr_ok and self.d.cloud_ocr is not None else None
        if cloud is not None and not cloud.available():
            cloud = None
        if local is None and cloud is None:
            return None, phash, "ocr_unavailable", None
        text: DocumentText | None = None
        meta: dict[str, Any] | None = None
        if local is not None:
            try:
                text = await ocr_images(images, local, self.d.ocr_languages)
            except Exception as e:  # noqa: BLE001 - a broken engine is reported on the document
                log.warning("local OCR %s failed: %s", local.name, type(e).__name__)
                if cloud is None:
                    return None, phash, f"ocr_failed:{type(e).__name__}"[:120], None
        weak = text is None or not text.words or text.mean_confidence < 0.6
        if cloud is not None and weak:
            t0 = time.monotonic()
            try:
                cloud_text = await ocr_images(images, cloud, self.d.ocr_languages)
            except Exception as e:  # noqa: BLE001 - a cloud failure never fails the document
                log.warning("cloud OCR %s failed: %s", cloud.name, type(e).__name__)
                cloud_text = None
            if cloud_text is not None and (text is None or len(cloud_text.words) >= len(text.words)):
                text = cloud_text
            meta = {
                "provider": f"ocr_cloud:{cloud.name}"[:32],
                "model": getattr(cloud, "model", None),
                "duration_ms": int((time.monotonic() - t0) * 1000),
            }
        assert text is not None
        if self.d.attach_page_images:
            from app.pipeline.ocr_engines import page_images_png

            text.page_images = page_images_png(images)
        if not text.words:
            empty = DocumentText(pages=[PageText(index=1, width=0, height=0)], source=text.source)
            empty.page_images = text.page_images
            return empty, phash, None, meta
        return text, phash, None, meta


async def load_document_for_processing(
    session: AsyncSession, document_id: uuid.UUID
) -> tuple[Document, Firm] | None:
    doc = await session.get(Document, document_id)
    if doc is None or doc.deleted_at is not None:
        return None
    firm = await session.get(Firm, doc.firm_id)
    if firm is None:
        return None
    return doc, firm
