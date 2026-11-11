"""
Invoice ingestion model (Phase 2).

This model only covers what Phase 2 needs: safely registering an uploaded
document. Fields for extracted invoice data (vendor, amounts, GST fields,
etc.) belong to Phase 3+ and are intentionally NOT here yet -- adding them
now would be exactly the kind of "placeholder module marked complete"
Phase 2 rules prohibit.
"""
import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import BigInteger, DateTime, Enum, Index, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class InvoiceFileType(str, enum.Enum):
    PDF = "pdf"
    PNG = "png"
    JPG = "jpg"
    JPEG = "jpeg"


class InvoiceProcessingStatus(str, enum.Enum):
    """
    Extensible processing-state model.

    Phase 2 only ever produces UPLOADED -> VALIDATING -> READY_FOR_OCR
    (or FAILED on a validation problem after the record already exists,
    e.g. a rollback scenario). PROCESSING / COMPLETED are reserved for
    Phase 3 (OCR) and are not set by any Phase 2 code path.
    """

    UPLOADED = "UPLOADED"
    VALIDATING = "VALIDATING"
    READY_FOR_OCR = "READY_FOR_OCR"
    PROCESSING = "PROCESSING"          # reserved for Phase 3
    COMPLETED = "COMPLETED"            # reserved for Phase 3
    FAILED = "FAILED"


class Invoice(Base):
    """A single uploaded invoice document and its ingestion metadata."""

    __tablename__ = "invoices"
    __table_args__ = (
        Index("ix_invoices_file_hash", "file_hash", unique=True),
        Index("ix_invoices_processing_status", "processing_status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # --- Original file identity (metadata only -- never used as a storage path) ---
    original_filename: Mapped[str] = mapped_column(String(512), nullable=False)

    # --- Safe internal storage identity ---
    stored_filename: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    storage_path: Mapped[str] = mapped_column(Text, nullable=False)

    # --- File properties ---
    file_type: Mapped[InvoiceFileType] = mapped_column(
        Enum(InvoiceFileType, name="invoice_file_type", native_enum=True), nullable=False
    )
    mime_type: Mapped[str] = mapped_column(String(128), nullable=False)
    file_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    file_hash: Mapped[str] = mapped_column(String(64), nullable=False)  # SHA-256 hex digest

    # --- Processing state ---
    processing_status: Mapped[InvoiceProcessingStatus] = mapped_column(
        Enum(InvoiceProcessingStatus, name="invoice_processing_status", native_enum=True),
        default=InvoiceProcessingStatus.UPLOADED,
        nullable=False,
    )
    processing_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    # --- Timestamps ---
    upload_timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
