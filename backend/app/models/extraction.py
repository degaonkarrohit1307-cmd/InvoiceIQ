"""Extraction persistence models (Phase 3). Separate, versioned, auditable; not a JSON blob on Invoice."""
import enum
import uuid
from datetime import date as date_type
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import Date, DateTime, Enum, ForeignKey, Index, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class ExtractionStatus(str, enum.Enum):
    SUCCESS = "SUCCESS"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"


class InvoiceExtraction(Base):
    __tablename__ = "invoice_extractions"
    __table_args__ = (
        Index("ix_invoice_extractions_invoice_id", "invoice_id"),
        Index("ix_invoice_extractions_invoice_id_created_at", "invoice_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    invoice_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("invoices.id", ondelete="CASCADE"), nullable=False
    )
    extraction_version: Mapped[int] = mapped_column(Integer, nullable=False)

    status: Mapped[ExtractionStatus] = mapped_column(
        Enum(ExtractionStatus, name="extraction_status", native_enum=True), nullable=False
    )
    ocr_engine: Mapped[str | None] = mapped_column(String(64), nullable=True)
    extractor_version: Mapped[str] = mapped_column(String(32), nullable=False, default="phase3-regex-v1")
    page_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    average_ocr_confidence: Mapped[float | None] = mapped_column(Numeric(5, 2), nullable=True)
    processing_duration_ms: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False, default=0)
    warnings: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    missing_critical_fields: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)

    raw_text: Mapped[str | None] = mapped_column(Text, nullable=True)

    invoice_number: Mapped[str | None] = mapped_column(String(128), nullable=True)
    invoice_number_normalized: Mapped[str | None] = mapped_column(String(128), nullable=True)
    invoice_date: Mapped[date_type | None] = mapped_column(Date, nullable=True)
    due_date: Mapped[date_type | None] = mapped_column(Date, nullable=True)

    supplier_name: Mapped[str | None] = mapped_column(String(512), nullable=True)
    supplier_gstin: Mapped[str | None] = mapped_column(String(15), nullable=True)
    buyer_name: Mapped[str | None] = mapped_column(String(512), nullable=True)
    buyer_gstin: Mapped[str | None] = mapped_column(String(15), nullable=True)

    subtotal: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    cgst: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    sgst: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    igst: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    total_tax: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    total_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    currency: Mapped[str | None] = mapped_column(String(3), nullable=True)

    # --- Phase 3 rule-based mathematical verification (deterministic, not ML) ---
    math_verification_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    math_verification_details: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )

    line_items: Mapped[list["InvoiceLineItem"]] = relationship(
        "InvoiceLineItem", back_populates="extraction", cascade="all, delete-orphan",
        order_by="InvoiceLineItem.line_number",
    )


class InvoiceLineItem(Base):
    __tablename__ = "invoice_line_items"
    __table_args__ = (Index("ix_invoice_line_items_extraction_id", "extraction_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    extraction_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("invoice_extractions.id", ondelete="CASCADE"), nullable=False
    )
    line_number: Mapped[int] = mapped_column(Integer, nullable=False)

    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    quantity: Mapped[Decimal | None] = mapped_column(Numeric(14, 3), nullable=True)
    unit_price: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    taxable_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    tax_rate: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    line_total: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)

    extraction: Mapped["InvoiceExtraction"] = relationship("InvoiceExtraction", back_populates="line_items")
