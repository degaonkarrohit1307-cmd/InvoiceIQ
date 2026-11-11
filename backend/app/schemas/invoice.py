"""Pydantic schemas for invoice ingestion (Phase 2)."""
import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.invoice import InvoiceFileType, InvoiceProcessingStatus


class InvoiceUploadResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    invoice_id: uuid.UUID
    original_filename: str
    file_type: InvoiceFileType
    file_size: int
    file_hash: str
    processing_status: InvoiceProcessingStatus
    upload_timestamp: datetime
    duplicate_of_existing: bool = False


class InvoiceValidationErrorResponse(BaseModel):
    error_id: str
    message: str
    code: str
