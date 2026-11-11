"""Document-level extraction: embedded PDF text first, OCR fallback for scanned pages/images."""
import time

import fitz  # PyMuPDF

from app.core.logging_config import get_logger
from app.models.invoice import InvoiceFileType
from app.ocr.base import (
    ExtractionSource,
    OCRDependencyError,
    OCREngine,
    OCRProcessingError,
    OCRResult,
    OCRStatus,
    PageResult,
)

logger = get_logger(__name__)

_MIN_MEANINGFUL_TEXT_CHARS = 10
_RENDER_ZOOM = 2.0


class UnsupportedDocumentError(Exception):
    """Raised when a document cannot be opened/parsed at all (e.g. corrupted)."""


def _render_pdf_page_to_png_bytes(page) -> bytes:
    pixmap = page.get_pixmap(matrix=fitz.Matrix(_RENDER_ZOOM, _RENDER_ZOOM))
    return pixmap.tobytes("png")


def extract_document_text(
    file_bytes: bytes,
    file_type: InvoiceFileType,
    ocr_engine: OCREngine,
) -> OCRResult:
    start = time.monotonic()

    if file_type == InvoiceFileType.PDF:
        pages, warnings, dependency_missing = _extract_pdf(file_bytes, ocr_engine)
    else:
        pages, warnings, dependency_missing = _extract_image(file_bytes, ocr_engine)

    duration_ms = (time.monotonic() - start) * 1000

    usable_pages = [
        page for page in pages
        if page.text and page.text.strip()
    ]

    if not usable_pages:
        status = OCRStatus.FAILED
    elif len(usable_pages) < len(pages) or any(p.warning for p in pages) or warnings:
        status = OCRStatus.PARTIAL
    else:
        status = OCRStatus.SUCCESS

    return OCRResult(
        status=status,
        engine=ocr_engine.name,
        pages=pages,
        processing_duration_ms=duration_ms,
        warnings=warnings,
        dependency_missing=dependency_missing,
    )


def _extract_pdf(file_bytes: bytes, ocr_engine: OCREngine):
    try:
        doc = fitz.open(stream=file_bytes, filetype="pdf")
    except Exception as exc:  # noqa: BLE001
        raise UnsupportedDocumentError(f"Could not open PDF document: {exc}") from exc

    if doc.page_count == 0:
        doc.close()
        raise UnsupportedDocumentError("PDF document contains no pages.")

    pages, warnings, dependency_missing = [], [], False

    try:
        for index in range(doc.page_count):
            page_number = index + 1
            page = doc.load_page(index)
            embedded_text = (page.get_text() or "").strip()

            if len(embedded_text) >= _MIN_MEANINGFUL_TEXT_CHARS:
                pages.append(
                    PageResult(
                        page_number,
                        embedded_text,
                        ExtractionSource.EMBEDDED_TEXT,
                    )
                )
                continue

            if dependency_missing:
                pages.append(
                    PageResult(
                        page_number,
                        "",
                        ExtractionSource.OCR,
                        warning=(
                            "OCR engine unavailable; page appears scanned "
                            "with no embedded text."
                        ),
                    )
                )
                continue

            try:
                png_bytes = _render_pdf_page_to_png_bytes(page)
                ocr_data = ocr_engine.extract_structured_text_from_image_bytes(
                    png_bytes
                )
                warning = (
                    None
                    if ocr_data.text.strip()
                    else "OCR completed but produced no text for this page."
                )
                pages.append(
                    PageResult(
                        page_number,
                        ocr_data.text,
                        ExtractionSource.OCR,
                        confidence=ocr_data.confidence,
                        warning=warning,
                        words=ocr_data.words,
                    )
                )
            except OCRDependencyError as exc:
                dependency_missing = True
                warnings.append(str(exc))
                pages.append(
                    PageResult(
                        page_number,
                        "",
                        ExtractionSource.OCR,
                        warning=str(exc),
                    )
                )
            except OCRProcessingError as exc:
                pages.append(
                    PageResult(
                        page_number,
                        "",
                        ExtractionSource.OCR,
                        warning=str(exc),
                    )
                )
    finally:
        doc.close()

    return pages, warnings, dependency_missing


def _extract_image(file_bytes: bytes, ocr_engine: OCREngine):
    try:
        ocr_data = ocr_engine.extract_structured_text_from_image_bytes(file_bytes)
    except OCRDependencyError as exc:
        return [PageResult(1, "", ExtractionSource.OCR, warning=str(exc))], [str(exc)], True
    except OCRProcessingError as exc:
        return [PageResult(1, "", ExtractionSource.OCR, warning=str(exc))], [], False

    warning = None if ocr_data.text.strip() else "OCR completed but produced no text."
    return [
        PageResult(
            1,
            ocr_data.text,
            ExtractionSource.OCR,
            confidence=ocr_data.confidence,
            warning=warning,
            words=ocr_data.words,
        )
    ], [], False
