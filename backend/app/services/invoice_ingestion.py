"""
Invoice ingestion orchestration (Phase 2).

Chosen duplicate-file behavior (documented per Phase 2 spec section 11):
When an uploaded file's SHA-256 hash exactly matches an existing invoice's
hash, we do NOT store a second physical copy or create a second database
row. We return the existing invoice's record with `duplicate_of_existing =
True` in the response. This is ONLY exact binary duplicate detection; it is
not the semantic/near-duplicate invoice intelligence Phase 5 will add.

Transaction-safety behavior:
  - Validate first; nothing is written to disk or DB for an invalid file.
  - Write the file to disk, THEN create the DB row.
  - If the DB write fails after the file was stored, the stored file is
    deleted (best-effort rollback) so no orphaned file is left behind.
  - If, in the rare case, storage succeeds but the DB commit fails, the
    exception propagates as a 500 after rollback is attempted; no misleading
    "success" DB record is ever left in place because the row simply never
    gets created if commit fails.
"""
import uuid

from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.logging_config import get_logger
from app.models.invoice import Invoice, InvoiceProcessingStatus
from app.services import storage
from app.services.file_validation import ValidatedFile

logger = get_logger(__name__)


class IngestionError(Exception):
    """Raised when ingestion fails after validation (storage/DB layer)."""


def find_existing_by_hash(db: Session, file_hash: str) -> Invoice | None:
    return db.query(Invoice).filter(Invoice.file_hash == file_hash).first()


def ingest_invoice(db: Session, original_filename: str, validated: ValidatedFile) -> tuple[Invoice, bool]:
    """
    Returns (invoice, was_duplicate).

    was_duplicate=True means no new file or DB row was created; the
    pre-existing matching invoice was returned instead.
    """
    file_hash = storage.compute_sha256(validated.content)

    existing = find_existing_by_hash(db, file_hash)
    if existing is not None:
        logger.info("Exact duplicate upload detected (hash=%s). Returning existing invoice %s.", file_hash, existing.id)
        return existing, True

    invoice_id = uuid.uuid4()
    relative_path = None

    try:
        relative_path = storage.save_file(validated.content, validated.file_type, invoice_id)

        invoice = Invoice(
            id=invoice_id,
            original_filename=original_filename,
            stored_filename=f"{invoice_id}{storage.extension_for(validated.file_type)}",
            storage_path=relative_path,
            file_type=validated.file_type,
            mime_type=validated.mime_type,
            file_size=validated.size,
            file_hash=file_hash,
            processing_status=InvoiceProcessingStatus.READY_FOR_OCR,
        )
        db.add(invoice)
        db.commit()
        db.refresh(invoice)

        logger.info("Invoice %s stored and registered. status=READY_FOR_OCR", invoice.id)
        return invoice, False

    except IntegrityError as exc:
        # A concurrent request inserted a row with the same file_hash
        # between our pre-check and this commit (the DB's unique
        # constraint on file_hash is the authoritative guard against that
        # race -- the earlier find_existing_by_hash() is a fast-path
        # optimization, not the sole safeguard). Roll back, clean up the
        # file we just wrote, and fall back to returning the row that won
        # the race.
        db.rollback()
        logger.info(
            "Concurrent duplicate upload detected via unique constraint (hash=%s). Rolling back local write.",
            file_hash,
        )
        if relative_path is not None:
            storage.delete_file(relative_path)
        winner = find_existing_by_hash(db, file_hash)
        if winner is None:
            # Extremely unlikely (would mean the concurrent row was deleted
            # again in between), but don't silently swallow it.
            raise IngestionError("Duplicate detected but the existing record could not be re-fetched.") from exc
        return winner, True

    except SQLAlchemyError as exc:
        db.rollback()
        logger.error("Database write failed for invoice %s: %s. Rolling back stored file.", invoice_id, exc)
        if relative_path is not None:
            storage.delete_file(relative_path)
        raise IngestionError("Failed to register the invoice after storing the file.") from exc

    except storage.StorageError as exc:
        logger.error("Storage failed for invoice %s: %s", invoice_id, exc)
        raise IngestionError(str(exc)) from exc
