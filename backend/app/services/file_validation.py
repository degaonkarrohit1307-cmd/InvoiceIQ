"""
Upload validation.

Validates BOTH the file extension the client claims AND the actual content
via magic-byte MIME sniffing (libmagic), then cross-checks the two agree.
This defends against a malicious file being uploaded with a spoofed
extension (e.g. an executable renamed to invoice.pdf).
"""
from dataclasses import dataclass

import magic

from app.core.config import get_settings
from app.models.invoice import InvoiceFileType

# Extension -> allowed real MIME type(s) as sniffed from file content.
_ALLOWED_MIME_BY_EXTENSION: dict[str, tuple[str, ...]] = {
    "pdf": ("application/pdf",),
    "png": ("image/png",),
    "jpg": ("image/jpeg",),
    "jpeg": ("image/jpeg",),
}

_EXTENSION_TO_FILE_TYPE = {
    "pdf": InvoiceFileType.PDF,
    "png": InvoiceFileType.PNG,
    "jpg": InvoiceFileType.JPG,
    "jpeg": InvoiceFileType.JPEG,
}

_mime_detector = magic.Magic(mime=True)


class ValidationErrorCode:
    UNSUPPORTED_EXTENSION = "UNSUPPORTED_EXTENSION"
    MIME_MISMATCH = "MIME_MISMATCH"
    EMPTY_FILE = "EMPTY_FILE"
    FILE_TOO_LARGE = "FILE_TOO_LARGE"
    NO_FILENAME = "NO_FILENAME"


class InvoiceValidationError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


@dataclass
class ValidatedFile:
    file_type: InvoiceFileType
    mime_type: str
    size: int
    content: bytes


def _extract_extension(filename: str) -> str:
    # Only used to look up the *claimed* type -- never used to build a path.
    if "." not in filename:
        return ""
    return filename.rsplit(".", 1)[-1].strip().lower()


def validate_upload(filename: str | None, content: bytes) -> ValidatedFile:
    settings = get_settings()

    if not filename or not filename.strip():
        raise InvoiceValidationError(ValidationErrorCode.NO_FILENAME, "No filename was provided.")

    extension = _extract_extension(filename)
    if extension not in _ALLOWED_MIME_BY_EXTENSION:
        raise InvoiceValidationError(
            ValidationErrorCode.UNSUPPORTED_EXTENSION,
            f"Unsupported file extension '.{extension}'. Supported formats: PDF, PNG, JPG, JPEG.",
        )

    size = len(content)
    if size == 0:
        raise InvoiceValidationError(ValidationErrorCode.EMPTY_FILE, "The uploaded file is empty.")

    max_bytes = settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024
    if size > max_bytes:
        raise InvoiceValidationError(
            ValidationErrorCode.FILE_TOO_LARGE,
            f"File exceeds the maximum allowed size of {settings.MAX_UPLOAD_SIZE_MB} MB.",
        )

    # Sniff actual content type from the file's magic bytes -- the
    # extension alone is never trusted.
    sniffed_mime = _mime_detector.from_buffer(content)
    allowed_mimes = _ALLOWED_MIME_BY_EXTENSION[extension]
    if sniffed_mime not in allowed_mimes:
        raise InvoiceValidationError(
            ValidationErrorCode.MIME_MISMATCH,
            f"File content does not match its extension. Claimed '.{extension}' but detected "
            f"content type '{sniffed_mime}'.",
        )

    return ValidatedFile(
        file_type=_EXTENSION_TO_FILE_TYPE[extension],
        mime_type=sniffed_mime,
        size=size,
        content=content,
    )
