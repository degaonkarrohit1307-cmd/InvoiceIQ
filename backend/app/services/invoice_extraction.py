"""
Extraction orchestration: safe file retrieval -> OCR -> field parsing ->
math verification -> persistence. Idempotency: each call creates a NEW
versioned InvoiceExtraction row (auditability), not an overwrite.
"""
import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import func
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.logging_config import get_logger
from app.models.extraction import ExtractionStatus, InvoiceExtraction, InvoiceLineItem
from app.models.invoice import Invoice, InvoiceProcessingStatus
from app.ocr.base import OCREngine, OCRStatus
from app.ocr.document_processor import UnsupportedDocumentError, extract_document_text
from app.ocr.tesseract_engine import TesseractOCREngine
from app.services import storage
from app.services.field_extraction import extract_invoice_fields
from app.services.math_verification import run_math_verification
from app.services.normalization import normalize_gstin

logger = get_logger(__name__)

# Updated to distinguish structured OCR extraction from the previous version.
EXTRACTOR_VERSION = "phase3-regex-structured-ocr-v2"


class ExtractionErrorCode:
    INVOICE_NOT_FOUND = "INVOICE_NOT_FOUND"
    STORED_FILE_MISSING = "STORED_FILE_MISSING"
    UNSUPPORTED_DOCUMENT = "UNSUPPORTED_DOCUMENT"
    OCR_DEPENDENCY_UNAVAILABLE = "OCR_DEPENDENCY_UNAVAILABLE"
    DATABASE_FAILURE = "DATABASE_FAILURE"


class ExtractionError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


def _default_ocr_engine() -> OCREngine:
    return TesseractOCREngine()


def _parse_iso_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def run_extraction(
    db: Session,
    invoice: Invoice,
    ocr_engine: OCREngine | None = None,
) -> InvoiceExtraction:
    engine = ocr_engine or _default_ocr_engine()

    absolute_path = storage.resolve_absolute_path(invoice.storage_path)
    if not absolute_path.exists():
        raise ExtractionError(
            ExtractionErrorCode.STORED_FILE_MISSING,
            "The stored document for this invoice could not be found.",
        )

    file_bytes = absolute_path.read_bytes()

    invoice.processing_status = InvoiceProcessingStatus.PROCESSING
    invoice.processing_error = None
    db.add(invoice)
    db.commit()

    try:
        ocr_result = extract_document_text(
            file_bytes,
            invoice.file_type,
            engine,
        )
    except UnsupportedDocumentError as exc:
        invoice.processing_status = InvoiceProcessingStatus.FAILED
        invoice.processing_error = str(exc)
        db.add(invoice)
        db.commit()

        raise ExtractionError(
            ExtractionErrorCode.UNSUPPORTED_DOCUMENT,
            str(exc),
        ) from exc

    if ocr_result.dependency_missing and not ocr_result.full_text.strip():
        invoice.processing_status = InvoiceProcessingStatus.FAILED
        invoice.processing_error = "OCR engine unavailable."
        db.add(invoice)
        db.commit()

        raise ExtractionError(
            ExtractionErrorCode.OCR_DEPENDENCY_UNAVAILABLE,
            "This document requires OCR, but the Tesseract engine is not "
            "available on this server. See README 'OCR setup'.",
        )

    # Pass both the original OCR text and structured page-level OCR data.
    # The field extractor can use word positions and confidence where available,
    # while retaining its existing text-based extraction fallback.
    fields = extract_invoice_fields(
        ocr_result.full_text,
        ocr_pages=ocr_result.pages,
    )

    # Existing mathematical verification remains unchanged.
    math_result = run_math_verification(fields)

    if ocr_result.status == OCRStatus.FAILED:
        extraction_status = ExtractionStatus.FAILED
    elif ocr_result.status == OCRStatus.PARTIAL or fields.missing_critical_fields:
        extraction_status = ExtractionStatus.PARTIAL
    else:
        extraction_status = ExtractionStatus.SUCCESS

    ocr_engine_used = (
        engine.name
        if any(p.source.value == "ocr" for p in ocr_result.pages)
        else None
    )

    try:
        next_version = (
            db.query(
                func.coalesce(
                    func.max(InvoiceExtraction.extraction_version),
                    0,
                )
            )
            .filter(InvoiceExtraction.invoice_id == invoice.id)
            .scalar()
            + 1
        )

        extraction = InvoiceExtraction(
            id=uuid.uuid4(),
            invoice_id=invoice.id,
            extraction_version=next_version,
            status=extraction_status,
            ocr_engine=ocr_engine_used,
            extractor_version=EXTRACTOR_VERSION,
            page_count=ocr_result.page_count,
            average_ocr_confidence=ocr_result.average_confidence,
            processing_duration_ms=ocr_result.processing_duration_ms,
            warnings=list(ocr_result.warnings) + list(fields.warnings),
            missing_critical_fields=fields.missing_critical_fields,
            raw_text=ocr_result.full_text or None,
            invoice_number=fields.invoice_number,
            invoice_number_normalized=(
                (fields.invoice_number or "").strip().upper() or None
            ),
            invoice_date=_parse_iso_date(fields.invoice_date),
            due_date=_parse_iso_date(fields.due_date),
            supplier_name=fields.supplier_name,
            supplier_gstin=normalize_gstin(fields.supplier_gstin),
            buyer_name=fields.buyer_name,
            buyer_gstin=normalize_gstin(fields.buyer_gstin),
            subtotal=fields.subtotal,
            cgst=fields.cgst,
            sgst=fields.sgst,
            igst=fields.igst,
            total_tax=fields.total_tax,
            total_amount=fields.total_amount,
            currency=fields.currency,
            math_verification_status=math_result.overall.value,
            math_verification_details=[
                {
                    "rule": c.rule,
                    "result": c.result.value,
                    "expected": c.expected,
                    "actual": c.actual,
                    "difference": c.difference,
                    "tolerance": c.tolerance,
                    "detail": c.detail,
                }
                for c in math_result.checks
            ],
        )

        db.add(extraction)

        for line_number, item in enumerate(fields.line_items, start=1):
            db.add(
                InvoiceLineItem(
                    id=uuid.uuid4(),
                    extraction_id=extraction.id,
                    line_number=line_number,
                    description=item.description,
                    quantity=item.quantity,
                    unit_price=item.unit_price,
                    taxable_amount=item.taxable_amount,
                    tax_rate=item.tax_rate,
                    line_total=item.line_total,
                )
            )

        invoice.processing_status = (
            InvoiceProcessingStatus.COMPLETED
            if extraction_status != ExtractionStatus.FAILED
            else InvoiceProcessingStatus.FAILED
        )

        db.add(invoice)
        db.commit()
        db.refresh(extraction)

        return extraction

    except SQLAlchemyError as exc:
        db.rollback()

        logger.error(
            "Database failure persisting extraction for invoice %s: %s",
            invoice.id,
            exc,
        )

        raise ExtractionError(
            ExtractionErrorCode.DATABASE_FAILURE,
            "Failed to persist the extraction result.",
        ) from exc


def get_latest_extraction(
    db: Session,
    invoice_id: uuid.UUID,
) -> InvoiceExtraction | None:
    return (
        db.query(InvoiceExtraction)
        .filter(InvoiceExtraction.invoice_id == invoice_id)
        .order_by(InvoiceExtraction.created_at.desc())
        .first()
    )