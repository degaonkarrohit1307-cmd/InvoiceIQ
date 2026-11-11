"""
Phase 3 tests: OCR, field extraction, normalization, math verification.

Most tests here are DB-independent by design (pure functions / OCR calls),
so they run without PostgreSQL. A small set of persistence/API tests at the
bottom DO require the real Postgres test DB via conftest.py's `client`/
`db_session` fixtures (consistent with Phase 1/2 -- never SQLite); those
are grouped last and clearly marked.
"""
from datetime import date
from pathlib import Path

import pytest

from app.models.invoice import InvoiceFileType
from app.ocr.base import ExtractionSource, OCRDependencyError, OCREngine, OCRProcessingError, OCRStatus
from app.ocr.document_processor import UnsupportedDocumentError, extract_document_text
from app.ocr.tesseract_engine import TesseractOCREngine
from app.services import normalization
from app.services.field_extraction import extract_invoice_fields
from app.services.math_verification import CheckResult, run_math_verification
from app.ocr.base import OCRWord, PageResult

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _read(name: str) -> bytes:
    return (FIXTURES_DIR / name).read_bytes()


class _StubUnavailableEngine(OCREngine):
    name = "stub-unavailable"

    def is_available(self) -> bool:
        return False

    def extract_text_from_image_bytes(self, image_bytes: bytes):
        raise OCRDependencyError("Stub engine deliberately unavailable.")


class _StubFailingEngine(OCREngine):
    name = "stub-failing"

    def is_available(self) -> bool:
        return True

    def extract_text_from_image_bytes(self, image_bytes: bytes):
        raise OCRProcessingError("Stub engine deliberately fails.")


# --------------------------------------------------------------------
# OCR / document processing (DB-independent)
# --------------------------------------------------------------------

def test_text_based_pdf_uses_embedded_text_not_ocr():
    engine = TesseractOCREngine()
    result = extract_document_text(_read("text_invoice_full.pdf"), InvoiceFileType.PDF, engine)
    assert result.status == OCRStatus.SUCCESS
    assert result.pages[0].source == ExtractionSource.EMBEDDED_TEXT
    assert result.pages[0].confidence is None
    assert "INV-2026-0001" in result.full_text


def test_scanned_image_invoice_uses_ocr():
    engine = TesseractOCREngine()
    result = extract_document_text(_read("scanned_invoice.png"), InvoiceFileType.PNG, engine)
    assert result.pages[0].source == ExtractionSource.OCR
    assert result.pages[0].confidence is not None
    assert 0 <= result.pages[0].confidence <= 100


def test_jpg_image_routes_through_ocr():
    engine = TesseractOCREngine()
    result = extract_document_text(_read("valid_image.png"), InvoiceFileType.PNG, engine)
    assert result.pages[0].source == ExtractionSource.OCR


def test_malformed_pdf_raises_unsupported_document_error():
    with pytest.raises(UnsupportedDocumentError):
        extract_document_text(_read("malformed_invoice.pdf"), InvoiceFileType.PDF, TesseractOCREngine())


def test_missing_ocr_dependency_reported_not_swallowed():
    result = extract_document_text(_read("scanned_invoice.png"), InvoiceFileType.PNG, _StubUnavailableEngine())
    assert result.dependency_missing is True
    assert result.status == OCRStatus.FAILED
    assert result.full_text == ""


def test_ocr_processing_failure_distinct_from_dependency_missing():
    result = extract_document_text(_read("scanned_invoice.png"), InvoiceFileType.PNG, _StubFailingEngine())
    assert result.dependency_missing is False
    assert result.status == OCRStatus.FAILED


# --------------------------------------------------------------------
# Structured field extraction (DB-independent)
# --------------------------------------------------------------------

def test_full_invoice_extracts_all_fields():
    engine = TesseractOCREngine()
    ocr_result = extract_document_text(_read("text_invoice_full.pdf"), InvoiceFileType.PDF, engine)
    f = extract_invoice_fields(ocr_result.full_text)

    assert f.invoice_number == "INV-2026-0001"
    assert f.invoice_date == "2026-03-15"
    assert f.due_date == "2026-03-30"
    assert f.supplier_name == "Acme Trading Co"
    assert f.supplier_gstin == "27AAAAA0000A1Z5"
    assert f.buyer_name == "Widget Buyers Pvt Ltd"
    assert f.buyer_gstin == "07BBBBB1111B2Z6"
    assert f.subtotal == 525.0
    assert f.cgst == 23.63
    assert f.sgst == 23.63
    assert f.igst == 0.0
    assert f.total_tax == 47.25
    assert f.total_amount == 572.25
    assert f.currency == "INR"
    assert f.missing_critical_fields == []


def test_multiple_line_items_extracted():
    engine = TesseractOCREngine()
    ocr_result = extract_document_text(_read("text_invoice_full.pdf"), InvoiceFileType.PDF, engine)
    f = extract_invoice_fields(ocr_result.full_text)
    assert len(f.line_items) == 3
    assert f.line_items[0].description == "Widget A"
    assert f.line_items[0].quantity == 2.0
    assert f.line_items[0].unit_price == 100.0
    assert f.line_items[0].line_total == 200.0


def test_missing_fields_invoice_reports_missing_critical_fields():
    engine = TesseractOCREngine()
    ocr_result = extract_document_text(_read("text_invoice_missing_fields.pdf"), InvoiceFileType.PDF, engine)
    f = extract_invoice_fields(ocr_result.full_text)
    assert set(f.missing_critical_fields) == {"invoice_number", "invoice_date", "supplier_name", "total_amount"}
    assert len(f.line_items) == 1


def test_empty_text_reports_all_critical_fields_missing():
    f = extract_invoice_fields("")
    assert set(f.missing_critical_fields) == {"invoice_number", "invoice_date", "supplier_name", "total_amount"}
    assert f.warnings


def test_malformed_amount_not_guessed():
    assert extract_invoice_fields("Grand Total: not-a-number").total_amount is None


def test_rs_and_inr_and_symbol_amounts_all_parse():
    assert normalization.parse_amount("Rs.572.25") == 572.25
    assert normalization.parse_amount("INR 572.25") == 572.25
    assert normalization.parse_amount("₹572.25") == 572.25


# --------------------------------------------------------------------
# Structured OCR word-position tests (DB-independent)
# --------------------------------------------------------------------

def _make_ocr_word(text, left, top, width=40, height=20, confidence=95,
                   line_number=None):
    return OCRWord(
        text=text,
        confidence=confidence,
        left=left,
        top=top,
        width=width,
        height=height,
        block_number=1,
        paragraph_number=1,
        line_number=line_number,
    )


def test_structured_ocr_lines_group_words_by_tesseract_line():
    from app.services.field_extraction import _structured_ocr_lines

    words = [
        _make_ocr_word("Widget", 100, 100, line_number=1),
        _make_ocr_word("A", 150, 100, line_number=1),
        _make_ocr_word("200.00", 400, 100, line_number=1),
        _make_ocr_word("Widget", 100, 140, line_number=2),
        _make_ocr_word("B", 150, 140, line_number=2),
    ]

    page = PageResult(
        page_number=1,
        text="Widget A 200.00\nWidget B",
        source=ExtractionSource.OCR,
        confidence=95,
        words=words,
    )

    lines = _structured_ocr_lines([page])

    assert len(lines) == 2
    assert lines[0]["text"] == "Widget A 200.00"
    assert lines[1]["text"] == "Widget B"


def test_structured_ocr_lines_sort_words_by_horizontal_position():
    from app.services.field_extraction import _structured_ocr_lines

    words = [
        _make_ocr_word("200.00", 400, 100, line_number=1),
        _make_ocr_word("Widget", 100, 100, line_number=1),
        _make_ocr_word("A", 150, 100, line_number=1),
    ]

    page = PageResult(
        page_number=1,
        text="",
        source=ExtractionSource.OCR,
        words=words,
    )

    lines = _structured_ocr_lines([page])

    assert len(lines) == 1
    assert lines[0]["text"] == "Widget A 200.00"


def test_structured_ocr_lines_use_coordinate_fallback_without_line_ids():
    from app.services.field_extraction import _structured_ocr_lines

    words = [
        _make_ocr_word("Item", 100, 100, line_number=None),
        _make_ocr_word("100.00", 400, 102, line_number=None),
        _make_ocr_word("Second", 100, 150, line_number=None),
    ]

    page = PageResult(
        page_number=1,
        text="",
        source=ExtractionSource.OCR,
        words=words,
    )

    lines = _structured_ocr_lines([page])

    assert len(lines) == 2
    assert lines[0]["text"] == "Item 100.00"
    assert lines[1]["text"] == "Second"


def test_structured_ocr_lines_ignore_empty_words():
    from app.services.field_extraction import _structured_ocr_lines

    words = [
        _make_ocr_word("Widget", 100, 100, line_number=1),
        _make_ocr_word("", 150, 100, line_number=1),
        _make_ocr_word("200.00", 400, 100, line_number=1),
    ]

    page = PageResult(
        page_number=1,
        text="",
        source=ExtractionSource.OCR,
        words=words,
    )

    lines = _structured_ocr_lines([page])

    assert len(lines) == 1
    assert lines[0]["text"] == "Widget 200.00"


# --------------------------------------------------------------------
# Normalization (DB-independent)
# --------------------------------------------------------------------

def test_normalize_whitespace():
    assert normalization.normalize_whitespace("  Acme   Trading   Co  ") == "Acme Trading Co"
    assert normalization.normalize_whitespace(None) is None


def test_normalize_gstin():
    assert normalization.normalize_gstin(" 27aaaaa0000a1z5 ") == "27AAAAA0000A1Z5"


def test_gstin_format_validation():
    assert normalization.is_valid_gstin_format("27AAAAA0000A1Z5") is True
    assert normalization.is_valid_gstin_format("not-a-gstin") is False
    assert normalization.is_valid_gstin_format(None) is False


def test_parse_date_formats():
    assert normalization.parse_date("15/03/2026") == date(2026, 3, 15)
    assert normalization.parse_date("2026-03-15") == date(2026, 3, 15)
    assert normalization.parse_date("not a date") is None


def test_parse_amount_garbage_returns_none():
    assert normalization.parse_amount("not-a-number") is None


# --------------------------------------------------------------------
# Rule-based mathematical verification (DB-independent)
# --------------------------------------------------------------------

class _LI:
    def __init__(self, quantity=None, unit_price=None, line_total=None):
        self.quantity, self.unit_price, self.line_total = quantity, unit_price, line_total


class _Fields:
    def __init__(self, line_items=None, subtotal=None, cgst=None, sgst=None, igst=None,
                 total_tax=None, total_amount=None):
        self.line_items = line_items or []
        self.subtotal, self.cgst, self.sgst, self.igst = subtotal, cgst, sgst, igst
        self.total_tax, self.total_amount = total_tax, total_amount


def test_math_verification_full_pass():
    fields = _Fields(
        line_items=[_LI(2, 100.0, 200.0), _LI(5, 50.0, 250.0), _LI(1, 75.0, 75.0)],
        subtotal=525.0, cgst=23.63, sgst=23.63, igst=0.0, total_tax=47.25, total_amount=572.25,
    )
    result = run_math_verification(fields)
    assert result.overall == CheckResult.PASS
    # Phase 4A added a discount_consistency check; this fixture has no
    # discount, so that one specific check is correctly NOT_CHECKABLE
    # (never fabricated as PASS) while every other check passes.
    for c in result.checks:
        if c.rule == "discount_does_not_exceed_subtotal":
            assert c.result == CheckResult.NOT_CHECKABLE
        else:
            assert c.result == CheckResult.PASS


def test_math_verification_line_total_mismatch_fails():
    fields = _Fields(line_items=[_LI(2, 100.0, 999.0)], subtotal=999.0, total_tax=0, total_amount=999.0)
    result = run_math_verification(fields)
    line_check = next(c for c in result.checks if c.rule == "line_item_1_total")
    assert line_check.result == CheckResult.FAIL
    assert result.overall == CheckResult.FAIL


def test_math_verification_subtotal_mismatch_fails():
    fields = _Fields(line_items=[_LI(1, 100.0, 100.0)], subtotal=999.0, total_tax=0, total_amount=999.0)
    result = run_math_verification(fields)
    subtotal_check = next(c for c in result.checks if c.rule == "subtotal_matches_line_items")
    assert subtotal_check.result == CheckResult.FAIL


def test_math_verification_cgst_sgst_pass_and_fail():
    good = run_math_verification(_Fields(cgst=10.0, sgst=10.0, total_tax=20.0))
    check = next(c for c in good.checks if c.rule == "cgst_plus_sgst")
    assert check.result == CheckResult.PASS

    bad = run_math_verification(_Fields(cgst=10.0, sgst=10.0, total_tax=99.0))
    check2 = next(c for c in bad.checks if c.rule == "cgst_plus_sgst")
    assert check2.result == CheckResult.FAIL


def test_math_verification_igst_used_instead_of_cgst_sgst_when_present():
    fields = _Fields(igst=18.0, total_tax=18.0)
    result = run_math_verification(fields)
    rules = [c.rule for c in result.checks]
    assert "igst_matches_total_gst" in rules
    assert "cgst_plus_sgst" not in rules


def test_math_verification_grand_total_pass_and_fail():
    good = run_math_verification(_Fields(subtotal=100.0, total_tax=18.0, total_amount=118.0))
    assert next(c for c in good.checks if c.rule == "subtotal_plus_tax_equals_grand_total").result == CheckResult.PASS

    bad = run_math_verification(_Fields(subtotal=100.0, total_tax=18.0, total_amount=500.0))
    assert next(c for c in bad.checks if c.rule == "subtotal_plus_tax_equals_grand_total").result == CheckResult.FAIL


def test_math_verification_missing_values_are_not_checkable_not_fabricated():
    fields = _Fields()  # everything None
    result = run_math_verification(fields)
    assert result.overall == CheckResult.NOT_CHECKABLE
    assert all(c.result == CheckResult.NOT_CHECKABLE for c in result.checks)


def test_math_verification_respects_rounding_tolerance():
    from decimal import Decimal
    fields = _Fields(line_items=[_LI(1, 100.0, 100.01)], subtotal=100.01, total_tax=0, total_amount=100.01)
    # Default tolerance (0.02) should treat a 0.01 rounding drift as PASS.
    result = run_math_verification(fields)
    line_check = next(c for c in result.checks if c.rule == "line_item_1_total")
    assert line_check.result == CheckResult.PASS

    # A stricter tolerance should now FAIL the same drift.
    strict_result = run_math_verification(fields, tolerance=Decimal("0.001"))
    strict_check = next(c for c in strict_result.checks if c.rule == "line_item_1_total")
    assert strict_check.result == CheckResult.FAIL


def test_math_verification_end_to_end_on_real_extracted_invoice():
    """Runs math verification against fields actually extracted from the PDF fixture (not hand-built)."""
    engine = TesseractOCREngine()
    ocr_result = extract_document_text(_read("text_invoice_full.pdf"), InvoiceFileType.PDF, engine)
    fields = extract_invoice_fields(ocr_result.full_text)
    result = run_math_verification(fields)
    assert result.overall == CheckResult.PASS


# --------------------------------------------------------------------
# Persistence + API tests -- REQUIRE the real PostgreSQL test DB
# (conftest.py `client`/`db_session` fixtures). Skipped automatically if
# the test database cannot be provisioned in this environment.
# --------------------------------------------------------------------

def _make_invoice_with_stored_file(db_session, tmp_path, monkeypatch, fixture_name, file_type):
    from app.core import config as config_module
    from app.models.invoice import Invoice, InvoiceProcessingStatus
    from app.services import storage
    import uuid

    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path))
    config_module.get_settings.cache_clear()

    content = _read(fixture_name)
    invoice_id = uuid.uuid4()
    relative_path = storage.save_file(content, file_type, invoice_id)

    invoice = Invoice(
        id=invoice_id, original_filename=fixture_name,
        stored_filename=f"{invoice_id}{storage.extension_for(file_type)}",
        storage_path=relative_path, file_type=file_type,
        mime_type="application/pdf" if file_type == InvoiceFileType.PDF else "image/png",
        file_size=len(content), file_hash=storage.compute_sha256(content),
        processing_status=InvoiceProcessingStatus.READY_FOR_OCR,
    )
    db_session.add(invoice)
    db_session.commit()
    db_session.refresh(invoice)
    return invoice


def test_run_extraction_persists_full_result_with_math_verification(db_session, tmp_path, monkeypatch):
    from app.core import config as config_module
    from app.models.extraction import ExtractionStatus
    from app.models.invoice import InvoiceProcessingStatus
    from app.services.invoice_extraction import run_extraction

    invoice = _make_invoice_with_stored_file(
        db_session, tmp_path, monkeypatch, "text_invoice_full.pdf", InvoiceFileType.PDF
    )
    try:
        extraction = run_extraction(db_session, invoice, ocr_engine=TesseractOCREngine())
        assert extraction.status == ExtractionStatus.SUCCESS
        assert extraction.extraction_version == 1
        assert extraction.math_verification_status == "PASS"
        assert len(extraction.math_verification_details) >= 5
        db_session.refresh(invoice)
        assert invoice.processing_status == InvoiceProcessingStatus.COMPLETED
    finally:
        config_module.get_settings.cache_clear()


def test_run_extraction_twice_creates_new_version(db_session, tmp_path, monkeypatch):
    from app.core import config as config_module
    from app.services.invoice_extraction import run_extraction

    invoice = _make_invoice_with_stored_file(
        db_session, tmp_path, monkeypatch, "text_invoice_full.pdf", InvoiceFileType.PDF
    )
    try:
        first = run_extraction(db_session, invoice, ocr_engine=TesseractOCREngine())
        second = run_extraction(db_session, invoice, ocr_engine=TesseractOCREngine())
        assert first.extraction_version == 1
        assert second.extraction_version == 2
    finally:
        config_module.get_settings.cache_clear()


def test_run_extraction_missing_file_raises_structured_error(db_session, tmp_path, monkeypatch):
    from app.core import config as config_module
    from app.services import storage
    from app.services.invoice_extraction import ExtractionError, ExtractionErrorCode, run_extraction

    invoice = _make_invoice_with_stored_file(
        db_session, tmp_path, monkeypatch, "text_invoice_full.pdf", InvoiceFileType.PDF
    )
    try:
        storage.resolve_absolute_path(invoice.storage_path).unlink()
        with pytest.raises(ExtractionError) as exc_info:
            run_extraction(db_session, invoice, ocr_engine=TesseractOCREngine())
        assert exc_info.value.code == ExtractionErrorCode.STORED_FILE_MISSING
    finally:
        config_module.get_settings.cache_clear()


def _upload(client, fixture_name, content_type):
    import io
    response = client.post(
        "/api/v1/invoices/upload",
        files={"file": (fixture_name, io.BytesIO(_read(fixture_name)), content_type)},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_extract_endpoint_returns_structured_result_with_math_check(client, tmp_path, monkeypatch):
    from app.core import config as config_module

    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path))
    config_module.get_settings.cache_clear()
    try:
        uploaded = _upload(client, "text_invoice_full.pdf", "application/pdf")
        response = client.post(f"/api/v1/invoices/{uploaded['invoice_id']}/extract")
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["status"] == "SUCCESS"
        assert body["invoice_number"] == "INV-2026-0001"
        assert body["math_verification_status"] == "PASS"
        assert len(body["line_items"]) == 3
    finally:
        config_module.get_settings.cache_clear()


def test_extract_endpoint_invoice_not_found_returns_404(client):
    import uuid
    response = client.post(f"/api/v1/invoices/{uuid.uuid4()}/extract")
    assert response.status_code == 404


def test_get_invoice_detail_includes_latest_extraction(client, tmp_path, monkeypatch):
    from app.core import config as config_module

    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path))
    config_module.get_settings.cache_clear()
    try:
        uploaded = _upload(client, "text_invoice_full.pdf", "application/pdf")
        invoice_id = uploaded["invoice_id"]
        before = client.get(f"/api/v1/invoices/{invoice_id}")
        assert before.json()["latest_extraction"] is None
        client.post(f"/api/v1/invoices/{invoice_id}/extract")
        after = client.get(f"/api/v1/invoices/{invoice_id}")
        assert after.json()["latest_extraction"]["invoice_number"] == "INV-2026-0001"
    finally:
        config_module.get_settings.cache_clear()

        
def _make_invoice_with_analysis_records(
    db_session,
    include_extraction=True,
    include_analysis=True,
):
    """Create an invoice and optionally its extraction and analysis records."""
    import uuid

    from app.models.invoice import (
        Invoice,
        InvoiceFileType,
        InvoiceProcessingStatus,
    )
    from app.models.extraction import InvoiceExtraction, ExtractionStatus
    from app.models.analysis import InvoiceAnalysis

    invoice_id = uuid.uuid4()

    invoice = Invoice(
        id=invoice_id,
        original_filename="test_invoice.pdf",
        stored_filename=f"{invoice_id}.pdf",
        storage_path="test/test_invoice.pdf",
        file_type=InvoiceFileType.PDF,
        mime_type="application/pdf",
        file_size=1024,
        file_hash=uuid.uuid4().hex + uuid.uuid4().hex,
        processing_status=InvoiceProcessingStatus.COMPLETED,
    )

    db_session.add(invoice)
    db_session.flush()

    extraction = None
    analysis = None

    if include_extraction:
        extraction = InvoiceExtraction(
            invoice_id=invoice.id,
            extraction_version=1,
            status=ExtractionStatus.SUCCESS,
            invoice_number="TEST-2026-001",
            subtotal=1000,
            total_amount=1180,
            math_verification_status="PASS",
        )

        db_session.add(extraction)
        db_session.flush()

    if include_analysis and extraction is not None:
        analysis = InvoiceAnalysis(
            extraction_id=extraction.id,
            analysis_version=1,
            verification_status="COMPLETED",
            model_type="LightGBM",
            fraud_score=13.01,
            fraud_risk_category="LOW",
            fraud_supervised_score=1.87,
            fraud_anomaly_score=38.84,
            fraud_mathematical_score=2.14,
            model_prediction={"probability": 0.01866},
            fraud_breakdown={"supervised": 0.93},
            gst_compliance_overall="PASS",
            gst_compliance_result={"status": "PASS"},
            gst_external_verification={"status": "NOT_CONFIGURED"},
            shap_status="AVAILABLE",
            shap_explanation={"features": []},
            key_signals=["Mathematical verification passed"],
            validation_errors=[],
        )

        db_session.add(analysis)

    db_session.commit()

    return invoice, extraction, analysis


# --------------------------------------------------------------------
# Invoice analysis retrieval API tests -- REQUIRE PostgreSQL
# --------------------------------------------------------------------

def test_get_invoice_analysis_returns_saved_results(client, db_session):
    invoice, extraction, analysis = _make_invoice_with_analysis_records(
        db_session
    )

    response = client.get(
        f"/api/v1/invoices/{invoice.id}/analysis"
    )

    assert response.status_code == 200, response.text

    body = response.json()

    assert body["analysis_id"] == str(analysis.id)
    assert body["extraction_id"] == str(extraction.id)
    assert body["analysis_version"] == 1
    assert body["verification_status"] == "COMPLETED"

    assert float(body["fraud_score"]) == 13.01
    assert body["fraud_risk_category"] == "LOW"
    assert float(body["fraud_supervised_score"]) == 1.87
    assert float(body["fraud_anomaly_score"]) == 38.84

    assert body["gst_compliance_overall"] == "PASS"
    assert body["gst_external_verification"]["status"] == "NOT_CONFIGURED"

    assert body["shap_status"] == "AVAILABLE"
    assert body["shap_explanation"] == {"features": []}

    assert body["key_signals"] == [
        "Mathematical verification passed"
    ]
    assert body["validation_errors"] == []


def test_get_invoice_analysis_invoice_not_found_returns_404(client):
    import uuid

    response = client.get(
        f"/api/v1/invoices/{uuid.uuid4()}/analysis"
    )

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "INVOICE_NOT_FOUND"


def test_get_invoice_analysis_without_extraction_returns_404(
    client,
    db_session,
):
    invoice, _, _ = _make_invoice_with_analysis_records(
        db_session,
        include_extraction=False,
    )

    response = client.get(
        f"/api/v1/invoices/{invoice.id}/analysis"
    )

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "EXTRACTION_NOT_FOUND"


def test_get_invoice_analysis_without_analysis_returns_404(
    client,
    db_session,
):
    invoice, _, _ = _make_invoice_with_analysis_records(
        db_session,
        include_analysis=False,
    )

    response = client.get(
        f"/api/v1/invoices/{invoice.id}/analysis"
    )

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "ANALYSIS_NOT_FOUND"

