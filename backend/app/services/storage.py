"""
Secure local filesystem storage for uploaded invoice documents.

Security properties this module guarantees:
  - The original filename NEVER determines the storage path (defends
    against path traversal / `../` / absolute-path / malicious-filename
    attacks).
  - Storage filenames are generated from a UUID + a safe, allow-listed
    extension only.
  - Files are written under a YYYY/MM subdirectory of UPLOAD_DIR, which is
    itself resolved to an absolute path and never escaped.
"""
import hashlib
import uuid
from datetime import datetime, timezone
from pathlib import Path

from app.core.config import get_settings
from app.core.logging_config import get_logger
from app.models.invoice import InvoiceFileType

logger = get_logger(__name__)

# Only these extensions are ever used for on-disk filenames, regardless of
# what the client claims. This list is intentionally the same as the
# supported InvoiceFileType values.
_SAFE_EXTENSIONS = {
    InvoiceFileType.PDF: ".pdf",
    InvoiceFileType.PNG: ".png",
    InvoiceFileType.JPG: ".jpg",
    InvoiceFileType.JPEG: ".jpeg",
}


class StorageError(Exception):
    """Raised when a file cannot be safely written to or removed from storage."""


def compute_sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _base_storage_dir() -> Path:
    settings = get_settings()
    base = Path(settings.UPLOAD_DIR).resolve()
    base.mkdir(parents=True, exist_ok=True)
    return base


def extension_for(file_type: InvoiceFileType) -> str:
    return _SAFE_EXTENSIONS[file_type]


def build_storage_location(file_type: InvoiceFileType, invoice_id: uuid.UUID) -> tuple[str, Path]:
    """
    Returns (relative_storage_path, absolute_path) for a new invoice file.

    The relative path (stored in PostgreSQL) looks like:
        invoices/2026/08/<invoice-id>.pdf
    and is always safely re-derivable and contained within UPLOAD_DIR.
    """
    now = datetime.now(timezone.utc)
    extension = _SAFE_EXTENSIONS[file_type]
    stored_filename = f"{invoice_id}{extension}"

    relative_dir = Path("invoices") / f"{now.year:04d}" / f"{now.month:02d}"
    relative_path = relative_dir / stored_filename

    absolute_dir = _base_storage_dir() / relative_dir
    absolute_path = absolute_dir / stored_filename

    # Defense in depth: even though every path component above is
    # generated internally (never from user input), verify the resolved
    # path is still inside the storage root before ever writing to disk.
    # is_relative_to() (rather than a string prefix check) avoids both a
    # false positive against a sibling directory with a matching prefix
    # (e.g. "storage/uploads2") and Windows path-casing/separator quirks.
    absolute_dir_resolved = absolute_dir.resolve()
    if not absolute_dir_resolved.is_relative_to(_base_storage_dir()):
        raise StorageError("Resolved storage path escaped the configured upload directory.")

    return str(relative_path), absolute_path


def save_file(content: bytes, file_type: InvoiceFileType, invoice_id: uuid.UUID) -> str:
    """Writes `content` to a safe, generated path. Returns the relative storage path."""
    relative_path, absolute_path = build_storage_location(file_type, invoice_id)

    try:
        absolute_path.parent.mkdir(parents=True, exist_ok=True)
        absolute_path.write_bytes(content)
    except OSError as exc:
        logger.error("Failed to write invoice file to storage: %s", exc)
        raise StorageError(f"Could not write file to storage: {exc}") from exc

    return relative_path


def delete_file(relative_path: str) -> None:
    """
    Best-effort cleanup used for rollback when the DB write fails after the
    file was already stored. Never raises -- a failed cleanup should not
    mask the original error that triggered the rollback.
    """
    try:
        absolute_path = (_base_storage_dir() / relative_path).resolve()
        if not absolute_path.is_relative_to(_base_storage_dir()):
            logger.error("Refusing to delete a path outside the storage root: %s", relative_path)
            return
        if absolute_path.exists():
            absolute_path.unlink()
            logger.info("Rolled back (deleted) stored file at %s", relative_path)
    except OSError as exc:
        logger.error("Cleanup failed for %s: %s", relative_path, exc)


def resolve_absolute_path(relative_path: str) -> Path:
    return (_base_storage_dir() / relative_path).resolve()
