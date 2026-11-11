"""
Phase 2 tests: invoice ingestion & secure storage.

Covers file validation, secure storage, hashing, duplicate detection,
transaction/rollback safety, the upload API, and response schema/status
codes -- all against the real PostgreSQL test database (see conftest.py),
never SQLite.
"""
import io
from pathlib import Path

import pytest

from app.models.invoice import Invoice, InvoiceFileType, InvoiceProcessingStatus
from app.services import storage
from app.services.file_validation import InvoiceValidationError, ValidationErrorCode, validate_upload
from app.services.invoice_ingestion import IngestionError, ingest_invoice

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _read(name: str) -> bytes:
    return (FIXTURES_DIR / name).read_bytes()


# ---------------------------------------------------------------------------
# File validation service (unit-level, no DB/API involved)
# ---------------------------------------------------------------------------

def test_valid_pdf_passes_validation():
    content = _read("valid_invoice.pdf")
    result = validate_upload("invoice.pdf", content)
    assert result.file_type == InvoiceFileType.PDF
    assert result.mime_type == "application/pdf"
    assert result.size == len(content)


def test_valid_png_passes_validation():
    content = _read("valid_image.png")
    result = validate_upload("invoice.png", content)
    assert result.file_type == InvoiceFileType.PNG
    assert result.mime_type == "image/png"


def test_valid_jpeg_passes_validation():
    content = _read("valid_image.jpg")
    result = validate_upload("invoice.jpg", content)
    assert result.file_type == InvoiceFileType.JPG
    assert result.mime_type == "image/jpeg"


def test_unsupported_extension_is_rejected():
    with pytest.raises(InvoiceValidationError) as exc_info:
        validate_upload("invoice.txt", _read("fake_invoice.txt"))
    assert exc_info.value.code == ValidationErrorCode.UNSUPPORTED_EXTENSION


def test_executable_disguised_with_txt_extension_is_rejected():
    with pytest.raises(InvoiceValidationError) as exc_info:
        validate_upload("invoice.exe", _read("fake_invoice.exe"))
    assert exc_info.value.code == ValidationErrorCode.UNSUPPORTED_EXTENSION


def test_spoofed_extension_with_mismatched_content_is_rejected():
    """A file claiming to be .pdf but whose actual magic bytes are not a PDF."""
    with pytest.raises(InvoiceValidationError) as exc_info:
        validate_upload("invoice.pdf", _read("spoofed_invoice.pdf"))
    assert exc_info.value.code == ValidationErrorCode.MIME_MISMATCH


def test_empty_file_is_rejected():
    with pytest.raises(InvoiceValidationError) as exc_info:
        validate_upload("invoice.pdf", _read("empty_invoice.pdf"))
    assert exc_info.value.code == ValidationErrorCode.EMPTY_FILE


def test_oversized_file_is_rejected(monkeypatch):
    from app.core import config as config_module

    config_module.get_settings.cache_clear()

    # Temporarily shrink the limit so our small fixture file exceeds it.
    monkeypatch.setenv("MAX_UPLOAD_SIZE_MB", "0")
    config_module.get_settings.cache_clear()
    try:
        with pytest.raises(InvoiceValidationError) as exc_info:
            validate_upload("invoice.pdf", _read("valid_invoice.pdf"))
        assert exc_info.value.code == ValidationErrorCode.FILE_TOO_LARGE
    finally:
        config_module.get_settings.cache_clear()


def test_missing_filename_is_rejected():
    with pytest.raises(InvoiceValidationError) as exc_info:
        validate_upload(None, _read("valid_invoice.pdf"))
    assert exc_info.value.code == ValidationErrorCode.NO_FILENAME


def test_sha256_is_deterministic_for_same_content():
    content = _read("valid_invoice.pdf")
    assert storage.compute_sha256(content) == storage.compute_sha256(content)


def test_sha256_differs_for_different_content():
    a = storage.compute_sha256(_read("valid_invoice.pdf"))
    b = storage.compute_sha256(_read("valid_image.png"))
    assert a != b


# ---------------------------------------------------------------------------
# Secure storage service
# ---------------------------------------------------------------------------

def test_storage_never_uses_original_filename_as_path(tmp_path, monkeypatch):
    from app.core import config as config_module

    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path))
    config_module.get_settings.cache_clear()
    try:
        import uuid

        invoice_id = uuid.uuid4()
        relative_path = storage.save_file(b"content", InvoiceFileType.PDF, invoice_id)

        # The generated path must be derived from the UUID, never from any
        # user-supplied filename (none was even passed to save_file).
        assert str(invoice_id) in relative_path
        assert relative_path.endswith(".pdf")

        absolute = storage.resolve_absolute_path(relative_path)
        assert absolute.exists()
        assert absolute.read_bytes() == b"content"
    finally:
        config_module.get_settings.cache_clear()


def test_storage_rejects_path_traversal_style_relative_path_on_delete(tmp_path, monkeypatch):
    from app.core import config as config_module

    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path))
    config_module.get_settings.cache_clear()
    try:
        # A file OUTSIDE the storage root that a traversal attempt might target.
        outside_file = tmp_path.parent / "should_not_be_deleted.txt"
        outside_file.write_text("do not delete me")

        storage.delete_file("../should_not_be_deleted.txt")

        assert outside_file.exists(), "delete_file must refuse to delete outside the storage root"
    finally:
        config_module.get_settings.cache_clear()


# ---------------------------------------------------------------------------
# Ingestion service (storage + DB orchestration, transaction safety)
# ---------------------------------------------------------------------------

def test_ingest_new_invoice_creates_file_and_db_record(db_session, tmp_path, monkeypatch):
    from app.core import config as config_module

    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path))
    config_module.get_settings.cache_clear()
    try:
        content = _read("valid_invoice.pdf")
        validated = validate_upload("invoice.pdf", content)

        invoice, was_duplicate = ingest_invoice(db_session, "invoice.pdf", validated)

        assert was_duplicate is False
        assert invoice.processing_status == InvoiceProcessingStatus.READY_FOR_OCR
        assert invoice.file_hash == storage.compute_sha256(content)
        assert invoice.original_filename == "invoice.pdf"

        stored_path = storage.resolve_absolute_path(invoice.storage_path)
        assert stored_path.exists()
        assert stored_path.read_bytes() == content

        db_row = db_session.query(Invoice).filter_by(id=invoice.id).one()
        assert db_row.file_hash == invoice.file_hash
    finally:
        config_module.get_settings.cache_clear()


def test_ingest_exact_duplicate_does_not_create_second_file_or_row(db_session, tmp_path, monkeypatch):
    from app.core import config as config_module

    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path))
    config_module.get_settings.cache_clear()
    try:
        content = _read("valid_invoice.pdf")
        validated = validate_upload("invoice.pdf", content)

        first, first_was_duplicate = ingest_invoice(db_session, "invoice.pdf", validated)
        assert first_was_duplicate is False

        second, second_was_duplicate = ingest_invoice(db_session, "invoice_copy.pdf", validated)
        assert second_was_duplicate is True
        assert second.id == first.id  # same underlying invoice returned

        total_rows = db_session.query(Invoice).filter_by(file_hash=first.file_hash).count()
        assert total_rows == 1

        stored_files = list((tmp_path).rglob(f"{first.id}.*"))
        assert len(stored_files) == 1
    finally:
        config_module.get_settings.cache_clear()


def test_ingest_rolls_back_stored_file_when_db_insert_fails(db_session, tmp_path, monkeypatch):
    """
    Simulates a DB failure after the file has already been written to disk,
    and verifies the file is cleaned up (no orphaned file left behind).
    """
    from app.core import config as config_module

    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path))
    config_module.get_settings.cache_clear()
    try:
        content = _read("valid_invoice.pdf")
        validated = validate_upload("invoice.pdf", content)

        # Force the DB commit to fail by closing the session's connection
        # first, simulating a DB-layer failure at exactly the commit step.
        original_commit = db_session.commit

        def failing_commit():
            raise __import__("sqlalchemy").exc.OperationalError("simulated failure", {}, None)

        monkeypatch.setattr(db_session, "commit", failing_commit)

        with pytest.raises(IngestionError):
            ingest_invoice(db_session, "invoice.pdf", validated)

        # No file should remain anywhere under the storage root.
        remaining_files = [p for p in tmp_path.rglob("*") if p.is_file()]
        assert remaining_files == [], f"Expected rollback to remove stored file, found: {remaining_files}"

        db_session.commit = original_commit
    finally:
        config_module.get_settings.cache_clear()


# ---------------------------------------------------------------------------
# Upload API (end-to-end: HTTP -> validation -> storage -> real Postgres)
# ---------------------------------------------------------------------------

def test_upload_valid_pdf_returns_201_and_ready_for_ocr(client, tmp_path, monkeypatch):
    from app.core import config as config_module

    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path))
    config_module.get_settings.cache_clear()
    try:
        content = _read("valid_invoice.pdf")
        response = client.post(
            "/api/v1/invoices/upload",
            files={"file": ("invoice.pdf", io.BytesIO(content), "application/pdf")},
        )
        assert response.status_code == 201
        body = response.json()
        assert body["processing_status"] == "READY_FOR_OCR"
        assert body["file_type"] == "pdf"
        assert body["duplicate_of_existing"] is False
        assert "invoice_id" in body
        assert body["file_hash"] == storage.compute_sha256(content)
    finally:
        config_module.get_settings.cache_clear()


def test_upload_valid_png_returns_201(client, tmp_path, monkeypatch):
    from app.core import config as config_module

    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path))
    config_module.get_settings.cache_clear()
    try:
        content = _read("valid_image.png")
        response = client.post(
            "/api/v1/invoices/upload",
            files={"file": ("invoice.png", io.BytesIO(content), "image/png")},
        )
        assert response.status_code == 201
        assert response.json()["file_type"] == "png"
    finally:
        config_module.get_settings.cache_clear()


def test_upload_valid_jpeg_returns_201(client, tmp_path, monkeypatch):
    from app.core import config as config_module

    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path))
    config_module.get_settings.cache_clear()
    try:
        content = _read("valid_image.jpg")
        response = client.post(
            "/api/v1/invoices/upload",
            files={"file": ("invoice.jpg", io.BytesIO(content), "image/jpeg")},
        )
        assert response.status_code == 201
        assert response.json()["file_type"] == "jpg"
    finally:
        config_module.get_settings.cache_clear()


def test_upload_unsupported_extension_returns_415(client):
    response = client.post(
        "/api/v1/invoices/upload",
        files={"file": ("invoice.txt", io.BytesIO(_read("fake_invoice.txt")), "text/plain")},
    )
    assert response.status_code == 415
    assert response.json()["detail"]["code"] == ValidationErrorCode.UNSUPPORTED_EXTENSION


def test_upload_spoofed_mime_returns_415(client):
    response = client.post(
        "/api/v1/invoices/upload",
        files={"file": ("invoice.pdf", io.BytesIO(_read("spoofed_invoice.pdf")), "application/pdf")},
    )
    assert response.status_code == 415
    assert response.json()["detail"]["code"] == ValidationErrorCode.MIME_MISMATCH


def test_upload_empty_file_returns_400(client):
    response = client.post(
        "/api/v1/invoices/upload",
        files={"file": ("invoice.pdf", io.BytesIO(b""), "application/pdf")},
    )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == ValidationErrorCode.EMPTY_FILE


def test_upload_oversized_file_returns_413(client, monkeypatch):
    from app.core import config as config_module

    monkeypatch.setenv("MAX_UPLOAD_SIZE_MB", "0")
    config_module.get_settings.cache_clear()
    try:
        response = client.post(
            "/api/v1/invoices/upload",
            files={"file": ("invoice.pdf", io.BytesIO(_read("valid_invoice.pdf")), "application/pdf")},
        )
        assert response.status_code == 413
    finally:
        config_module.get_settings.cache_clear()


def test_upload_duplicate_returns_200_not_201(client, tmp_path, monkeypatch):
    from app.core import config as config_module

    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path))
    config_module.get_settings.cache_clear()
    try:
        content = _read("valid_invoice.pdf")

        first = client.post(
            "/api/v1/invoices/upload",
            files={"file": ("invoice.pdf", io.BytesIO(content), "application/pdf")},
        )
        assert first.status_code == 201
        first_id = first.json()["invoice_id"]

        second = client.post(
            "/api/v1/invoices/upload",
            files={"file": ("invoice_renamed.pdf", io.BytesIO(content), "application/pdf")},
        )
        assert second.status_code == 200
        body = second.json()
        assert body["duplicate_of_existing"] is True
        assert body["invoice_id"] == first_id
    finally:
        config_module.get_settings.cache_clear()


def test_upload_malicious_path_traversal_filename_is_handled_safely(client, tmp_path, monkeypatch):
    """
    A filename like '../../evil.pdf' must never be used to build a
    filesystem path. The upload should either succeed safely (stored under
    a generated UUID path) or be rejected -- but it must never write
    outside the configured storage root.
    """
    from app.core import config as config_module

    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path))
    config_module.get_settings.cache_clear()
    try:
        content = _read("valid_invoice.pdf")
        response = client.post(
            "/api/v1/invoices/upload",
            files={"file": ("../../../../etc/evil.pdf", io.BytesIO(content), "application/pdf")},
        )
        assert response.status_code == 201

        # Every file written must be inside tmp_path (the configured storage root).
        for path in tmp_path.rglob("*"):
            if path.is_file():
                assert path.is_relative_to(tmp_path)

        # And nothing was written outside it either.
        assert not (tmp_path.parent / "etc").exists()
    finally:
        config_module.get_settings.cache_clear()


def test_upload_response_matches_schema_field_types(client, tmp_path, monkeypatch):
    from app.core import config as config_module

    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path))
    config_module.get_settings.cache_clear()
    try:
        content = _read("valid_invoice.pdf")
        response = client.post(
            "/api/v1/invoices/upload",
            files={"file": ("invoice.pdf", io.BytesIO(content), "application/pdf")},
        )
        body = response.json()
        expected_keys = {
            "invoice_id", "original_filename", "file_type", "file_size",
            "file_hash", "processing_status", "upload_timestamp", "duplicate_of_existing",
        }
        assert expected_keys.issubset(body.keys())
        assert isinstance(body["file_size"], int)
        assert isinstance(body["duplicate_of_existing"], bool)
        assert len(body["file_hash"]) == 64  # SHA-256 hex digest length
    finally:
        config_module.get_settings.cache_clear()
