"""Pydantic schemas for extraction API responses (Phase 3)."""
import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from app.models.extraction import ExtractionStatus
from app.models.invoice import InvoiceFileType, InvoiceProcessingStatus


class LineItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    line_number: int
    description: str | None
    quantity: Decimal | None
    unit_price: Decimal | None
    taxable_amount: Decimal | None
    tax_rate: Decimal | None
    line_total: Decimal | None


class MathCheckResponse(BaseModel):
    rule: str
    result: str
    expected: float | None = None
    actual: float | None = None
    difference: float | None = None
    tolerance: float | None = None
    detail: str = ""


class ExtractionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    extraction_id: uuid.UUID
    invoice_id: uuid.UUID
    extraction_version: int
    status: ExtractionStatus
    ocr_engine: str | None
    extractor_version: str
    page_count: int
    average_ocr_confidence: Decimal | None
    processing_duration_ms: Decimal
    warnings: list[str]
    missing_critical_fields: list[str]

    invoice_number: str | None
    invoice_date: date | None
    due_date: date | None
    supplier_name: str | None
    supplier_gstin: str | None
    buyer_name: str | None
    buyer_gstin: str | None
    subtotal: Decimal | None
    cgst: Decimal | None
    sgst: Decimal | None
    igst: Decimal | None
    total_tax: Decimal | None
    total_amount: Decimal | None
    currency: str | None

    math_verification_status: str | None
    math_verification_details: list[MathCheckResponse]

    line_items: list[LineItemResponse]
    created_at: datetime

    @classmethod
    def from_model(cls, extraction) -> "ExtractionResponse":
        return cls(
            extraction_id=extraction.id, invoice_id=extraction.invoice_id,
            extraction_version=extraction.extraction_version, status=extraction.status,
            ocr_engine=extraction.ocr_engine, extractor_version=extraction.extractor_version,
            page_count=extraction.page_count, average_ocr_confidence=extraction.average_ocr_confidence,
            processing_duration_ms=extraction.processing_duration_ms,
            warnings=extraction.warnings or [], missing_critical_fields=extraction.missing_critical_fields or [],
            invoice_number=extraction.invoice_number, invoice_date=extraction.invoice_date,
            due_date=extraction.due_date, supplier_name=extraction.supplier_name,
            supplier_gstin=extraction.supplier_gstin, buyer_name=extraction.buyer_name,
            buyer_gstin=extraction.buyer_gstin, subtotal=extraction.subtotal, cgst=extraction.cgst,
            sgst=extraction.sgst, igst=extraction.igst, total_tax=extraction.total_tax,
            total_amount=extraction.total_amount, currency=extraction.currency,
            math_verification_status=extraction.math_verification_status,
            math_verification_details=[MathCheckResponse(**d) for d in (extraction.math_verification_details or [])],
            line_items=[
                LineItemResponse(
                    line_number=li.line_number, description=li.description, quantity=li.quantity,
                    unit_price=li.unit_price, taxable_amount=li.taxable_amount, tax_rate=li.tax_rate,
                    line_total=li.line_total,
                ) for li in extraction.line_items
            ],
            created_at=extraction.created_at,
        )


class InvoiceDetailResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    invoice_id: uuid.UUID
    original_filename: str
    file_type: InvoiceFileType
    file_size: int
    file_hash: str
    processing_status: InvoiceProcessingStatus
    processing_error: str | None
    upload_timestamp: datetime
    latest_extraction: ExtractionResponse | None


class InvoiceListSummary(BaseModel):
    total: int
    completed: int
    processing: int
    failed: int


class InvoiceListResponse(BaseModel):
    items: list[InvoiceDetailResponse]
    total: int
    limit: int
    offset: int
    summary: InvoiceListSummary