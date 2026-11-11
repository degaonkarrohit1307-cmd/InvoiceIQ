"""
Invoice ingestion API (Phase 2).

Only the upload endpoint is implemented here. OCR, extraction, verification,
fraud, compliance, vendor risk, and dashboard endpoints belong to later
phases and are intentionally not present.
"""
import json
import uuid

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import JSONResponse

from sqlalchemy.orm import Session
from sqlalchemy import func

from app.core.database import get_db
from app.core.logging_config import get_logger
from app.models.invoice import Invoice, InvoiceProcessingStatus
from app.models.analysis import InvoiceAnalysis
from app.schemas.analysis import InvoiceAnalysisResponse
from app.schemas.extraction import (
    ExtractionResponse,
    InvoiceDetailResponse,
    InvoiceListResponse,
    InvoiceListSummary,
)
from app.schemas.invoice import InvoiceUploadResponse
from app.services.file_validation import InvoiceValidationError, ValidationErrorCode, validate_upload
from app.services.invoice_extraction import ExtractionError, get_latest_extraction, run_extraction
from app.services.invoice_ingestion import IngestionError, ingest_invoice
from app.services.invoice_analysis import run_invoice_analysis

router = APIRouter(prefix="/invoices", tags=["invoices"])
logger = get_logger(__name__)

_VALIDATION_STATUS_CODES = {
    ValidationErrorCode.UNSUPPORTED_EXTENSION: status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
    ValidationErrorCode.MIME_MISMATCH: status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
    ValidationErrorCode.EMPTY_FILE: status.HTTP_400_BAD_REQUEST,
    ValidationErrorCode.FILE_TOO_LARGE: status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
    ValidationErrorCode.NO_FILENAME: status.HTTP_400_BAD_REQUEST,
}

_EXTRACTION_STATUS_CODES = {
    "STORED_FILE_MISSING": status.HTTP_500_INTERNAL_SERVER_ERROR,
    "UNSUPPORTED_DOCUMENT": status.HTTP_422_UNPROCESSABLE_ENTITY,
    "OCR_DEPENDENCY_UNAVAILABLE": status.HTTP_503_SERVICE_UNAVAILABLE,
    "DATABASE_FAILURE": status.HTTP_500_INTERNAL_SERVER_ERROR,
}


@router.post("/upload", response_model=None)
async def upload_invoice(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
) -> JSONResponse:
    logger.info("Upload attempt received: filename=%s content_type=%s", file.filename, file.content_type)

    content = await file.read()

    try:
        validated = validate_upload(file.filename, content)
    except InvoiceValidationError as exc:
        logger.warning("Upload rejected: code=%s message=%s", exc.code, exc.message)
        http_status = _VALIDATION_STATUS_CODES.get(exc.code, status.HTTP_400_BAD_REQUEST)
        raise HTTPException(status_code=http_status, detail={"code": exc.code, "message": exc.message}) from exc

    try:
        invoice, was_duplicate = ingest_invoice(db, file.filename, validated)
    except IngestionError as exc:
        logger.error("Ingestion failed for %s: %s", file.filename, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "INGESTION_FAILED", "message": "Failed to store and register the invoice."},
        ) from exc

    body = InvoiceUploadResponse(
        invoice_id=invoice.id,
        original_filename=invoice.original_filename,
        file_type=invoice.file_type,
        file_size=invoice.file_size,
        file_hash=invoice.file_hash,
        processing_status=invoice.processing_status,
        upload_timestamp=invoice.upload_timestamp,
        duplicate_of_existing=was_duplicate,
    )

    # 200 OK for an exact duplicate (nothing new was created); 201 Created
    # for a genuinely new invoice record + stored file.
    response_status = status.HTTP_200_OK if was_duplicate else status.HTTP_201_CREATED
    return JSONResponse(status_code=response_status, content=json.loads(body.model_dump_json()))


@router.get("", response_model=InvoiceListResponse)
def list_invoices(
    db: Session = Depends(get_db),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> InvoiceListResponse:
    """Retrieve paginated invoice history with latest extraction results."""

    total = db.query(func.count(Invoice.id)).scalar() or 0

    completed = db.query(func.count(Invoice.id)).filter(
        Invoice.processing_status == InvoiceProcessingStatus.COMPLETED
    ).scalar() or 0

    failed = db.query(func.count(Invoice.id)).filter(
        Invoice.processing_status == InvoiceProcessingStatus.FAILED
    ).scalar() or 0

    processing = db.query(func.count(Invoice.id)).filter(
        Invoice.processing_status.notin_([
            InvoiceProcessingStatus.COMPLETED,
            InvoiceProcessingStatus.FAILED,
        ])
    ).scalar() or 0

    invoices = (
        db.query(Invoice)
        .order_by(Invoice.upload_timestamp.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )

    items = []

    for invoice in invoices:
        latest = get_latest_extraction(db, invoice.id)

        items.append(
            InvoiceDetailResponse(
                invoice_id=invoice.id,
                original_filename=invoice.original_filename,
                file_type=invoice.file_type,
                file_size=invoice.file_size,
                file_hash=invoice.file_hash,
                processing_status=invoice.processing_status,
                processing_error=invoice.processing_error,
                upload_timestamp=invoice.upload_timestamp,
                latest_extraction=(
                    ExtractionResponse.from_model(latest)
                    if latest else None
                ),
            )
        )

    return InvoiceListResponse(
        items=items,
        total=total,
        limit=limit,
        offset=offset,
        summary=InvoiceListSummary(
            total=total,
            completed=completed,
            processing=processing,
            failed=failed,
        ),
    )

def _get_invoice_or_404(db: Session, invoice_id: uuid.UUID) -> Invoice:
    invoice = db.query(Invoice).filter(Invoice.id == invoice_id).first()
    if invoice is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "INVOICE_NOT_FOUND", "message": "No invoice exists with this ID."},
        )
    return invoice


@router.get("/{invoice_id}", response_model=InvoiceDetailResponse)
def get_invoice(invoice_id: uuid.UUID, db: Session = Depends(get_db)) -> InvoiceDetailResponse:
    invoice = _get_invoice_or_404(db, invoice_id)
    latest = get_latest_extraction(db, invoice.id)
    return InvoiceDetailResponse(
        invoice_id=invoice.id, original_filename=invoice.original_filename, file_type=invoice.file_type,
        file_size=invoice.file_size, file_hash=invoice.file_hash, processing_status=invoice.processing_status,
        processing_error=invoice.processing_error, upload_timestamp=invoice.upload_timestamp,
        latest_extraction=ExtractionResponse.from_model(latest) if latest else None,
    )

@router.get(
    "/{invoice_id}/analysis",
    response_model=InvoiceAnalysisResponse,
)
def get_invoice_analysis(
    invoice_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> InvoiceAnalysisResponse:
    """Retrieve the latest saved analysis for an invoice."""

    invoice = _get_invoice_or_404(db, invoice_id)

    latest_extraction = get_latest_extraction(db, invoice.id)

    if latest_extraction is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "EXTRACTION_NOT_FOUND",
                "message": "No extraction exists for this invoice.",
            },
        )

    analysis = (
        db.query(InvoiceAnalysis)
        .filter(InvoiceAnalysis.extraction_id == latest_extraction.id)
        .order_by(InvoiceAnalysis.analysis_version.desc())
        .first()
    )

    if analysis is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "ANALYSIS_NOT_FOUND",
                "message": "No analysis results exist for the latest extraction.",
            },
        )

    return InvoiceAnalysisResponse.from_model(analysis)

@router.post(
    "/{invoice_id}/extract",
    response_model=ExtractionResponse,
    status_code=status.HTTP_201_CREATED,
)
def extract_invoice(
    invoice_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
) -> ExtractionResponse:
    """Extract an invoice and run the complete analysis workflow."""

    invoice = _get_invoice_or_404(db, invoice_id)

    logger.info("Extraction requested for invoice %s", invoice.id)

    try:
        # Step 1: OCR, field extraction and mathematical verification.
        extraction = run_extraction(db, invoice)

        # Step 2: Run LangGraph, fraud detection and GST compliance.
        # Step 3: Generate SHAP explanation and persist analysis results.
        analysis = run_invoice_analysis(
            db=db,
            extraction=extraction,
            verification_graph=request.app.state.invoice_verification_graph,
            pipeline=request.app.state.fraud_pipeline,
        )

        logger.info(
            "Invoice analysis completed: invoice=%s, extraction=%s, analysis=%s",
            invoice.id,
            extraction.id,
            analysis.id,
        )

    except ExtractionError as exc:
        logger.warning(
            "Extraction failed for invoice %s: code=%s",
            invoice.id,
            exc.code,
        )
        http_status = _EXTRACTION_STATUS_CODES.get(
            exc.code,
            status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
        raise HTTPException(
            status_code=http_status,
            detail={"code": exc.code, "message": exc.message},
        ) from exc

    except Exception as exc:
        logger.exception(
            "Invoice analysis failed for invoice %s",
            invoice.id,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "code": "INVOICE_ANALYSIS_FAILED",
                "message": (
                    "Invoice extraction was saved, but analysis results "
                    "could not be completed or persisted."
                ),
            },
        ) from exc

    return ExtractionResponse.from_model(extraction)