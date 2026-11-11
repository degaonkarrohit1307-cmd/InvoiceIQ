"""Phase 6A tests: GST/tax compliance module. No DB/external infra required."""
import json

from app.compliance.gst import run_gst_compliance_check, validate_gstin_format
from app.compliance.models import CheckResult


class _Fields:
    def __init__(self, supplier_gstin=None, buyer_gstin=None, subtotal=None,
                 cgst=None, sgst=None, igst=None, total_tax=None, total_amount=None):
        self.supplier_gstin, self.buyer_gstin = supplier_gstin, buyer_gstin
        self.subtotal, self.cgst, self.sgst, self.igst = subtotal, cgst, sgst, igst
        self.total_tax, self.total_amount = total_tax, total_amount


VALID_SUPPLIER_GSTIN = "27AAAAA0000A1Z5"
VALID_BUYER_GSTIN = "07BBBBB1111B2Z6"


def _check(result, rule):
    return next(c for c in result.checks if c.rule == rule)


# --------------------------------------------------------------------
# Valid CGST + SGST (intra-state) invoice
# --------------------------------------------------------------------

def test_valid_cgst_sgst_invoice_passes_overall():
    fields = _Fields(
        supplier_gstin=VALID_SUPPLIER_GSTIN, buyer_gstin=VALID_BUYER_GSTIN,
        subtotal=525.0, cgst=23.63, sgst=23.63, igst=0.0, total_tax=47.25, total_amount=572.25,
    )
    result = run_gst_compliance_check(fields)
    assert result.overall == CheckResult.PASS


def test_valid_cgst_sgst_check_passes_and_igst_not_checkable():
    fields = _Fields(subtotal=525.0, cgst=23.63, sgst=23.63, igst=0.0, total_tax=47.25, total_amount=572.25)
    result = run_gst_compliance_check(fields)
    assert _check(result, "cgst_plus_sgst").result == CheckResult.PASS
    assert _check(result, "igst_matches_total_gst").result == CheckResult.NOT_CHECKABLE


# --------------------------------------------------------------------
# Valid IGST (inter-state) invoice
# --------------------------------------------------------------------

def test_valid_igst_invoice_passes_overall():
    fields = _Fields(
        supplier_gstin=VALID_SUPPLIER_GSTIN, buyer_gstin=VALID_BUYER_GSTIN,
        subtotal=100.0, cgst=0.0, sgst=0.0, igst=18.0, total_tax=18.0, total_amount=118.0,
    )
    result = run_gst_compliance_check(fields)
    assert result.overall == CheckResult.PASS


def test_valid_igst_check_passes_and_cgst_sgst_not_checkable():
    fields = _Fields(subtotal=100.0, cgst=0.0, sgst=0.0, igst=18.0, total_tax=18.0, total_amount=118.0)
    result = run_gst_compliance_check(fields)
    assert _check(result, "igst_matches_total_gst").result == CheckResult.PASS
    assert _check(result, "cgst_plus_sgst").result == CheckResult.NOT_CHECKABLE


# --------------------------------------------------------------------
# CGST/SGST mismatch
# --------------------------------------------------------------------

def test_cgst_sgst_mismatch_fails():
    fields = _Fields(subtotal=525.0, cgst=23.63, sgst=23.63, igst=0.0, total_tax=999.0, total_amount=999.0)
    result = run_gst_compliance_check(fields)
    assert _check(result, "cgst_plus_sgst").result == CheckResult.FAIL
    assert result.overall == CheckResult.FAIL


# --------------------------------------------------------------------
# IGST mismatch
# --------------------------------------------------------------------

def test_igst_mismatch_fails():
    fields = _Fields(subtotal=100.0, cgst=0.0, sgst=0.0, igst=18.0, total_tax=999.0, total_amount=999.0)
    result = run_gst_compliance_check(fields)
    assert _check(result, "igst_matches_total_gst").result == CheckResult.FAIL


# --------------------------------------------------------------------
# Total-tax mismatch (sum of components vs stated total_tax)
# --------------------------------------------------------------------

def test_total_tax_mismatch_with_components_fails():
    # cgst+sgst+igst = 23.63+23.63+0 = 47.26, but total_tax claims 999
    fields = _Fields(cgst=23.63, sgst=23.63, igst=0.0, total_tax=999.0)
    result = run_gst_compliance_check(fields)
    assert _check(result, "total_tax_matches_components").result == CheckResult.FAIL


def test_total_tax_matches_components_passes():
    fields = _Fields(cgst=9.0, sgst=9.0, igst=0.0, total_tax=18.0)
    result = run_gst_compliance_check(fields)
    assert _check(result, "total_tax_matches_components").result == CheckResult.PASS


# --------------------------------------------------------------------
# Grand-total mismatch
# --------------------------------------------------------------------

def test_grand_total_mismatch_fails():
    fields = _Fields(subtotal=100.0, cgst=9.0, sgst=9.0, total_tax=18.0, total_amount=999.0)
    result = run_gst_compliance_check(fields)
    assert _check(result, "subtotal_plus_tax_equals_grand_total").result == CheckResult.FAIL


def test_grand_total_match_passes():
    fields = _Fields(subtotal=100.0, cgst=9.0, sgst=9.0, total_tax=18.0, total_amount=118.0)
    result = run_gst_compliance_check(fields)
    assert _check(result, "subtotal_plus_tax_equals_grand_total").result == CheckResult.PASS


# --------------------------------------------------------------------
# GSTIN format: valid / invalid / format != active
# --------------------------------------------------------------------

def test_valid_gstin_format_passes():
    check = validate_gstin_format(VALID_SUPPLIER_GSTIN, "supplier")
    assert check.result == CheckResult.PASS


def test_invalid_gstin_format_fails():
    check = validate_gstin_format("NOT-A-GSTIN", "supplier")
    assert check.result == CheckResult.FAIL


def test_valid_gstin_format_does_not_claim_active_or_real():
    """A format-valid GSTIN must explicitly disclaim real/active/registered status."""
    check = validate_gstin_format(VALID_SUPPLIER_GSTIN, "supplier")
    assert check.result == CheckResult.PASS
    detail_lower = check.detail.lower()
    assert "does not confirm" in detail_lower or "not confirm" in detail_lower
    assert "real" in detail_lower or "active" in detail_lower or "registered" in detail_lower


def test_missing_gstin_is_not_checkable():
    check = validate_gstin_format(None, "buyer")
    assert check.result == CheckResult.NOT_CHECKABLE


def test_run_gst_compliance_check_includes_both_gstin_checks():
    fields = _Fields(supplier_gstin=VALID_SUPPLIER_GSTIN, buyer_gstin="INVALID")
    result = run_gst_compliance_check(fields)
    assert _check(result, "supplier_gstin_format").result == CheckResult.PASS
    assert _check(result, "buyer_gstin_format").result == CheckResult.FAIL
    assert result.overall == CheckResult.FAIL  # one bad GSTIN format fails the whole compliance result


# --------------------------------------------------------------------
# Tax regime consistency: CGST/SGST + IGST together is inconsistent
# --------------------------------------------------------------------

def test_both_regimes_populated_flags_inconsistent_tax_structure():
    fields = _Fields(subtotal=100.0, cgst=9.0, sgst=9.0, igst=18.0, total_tax=36.0, total_amount=136.0)
    result = run_gst_compliance_check(fields)
    assert _check(result, "tax_regime_consistency").result == CheckResult.FAIL
    assert result.overall == CheckResult.FAIL


def test_only_cgst_sgst_regime_is_consistent():
    fields = _Fields(subtotal=100.0, cgst=9.0, sgst=9.0, igst=0.0, total_tax=18.0, total_amount=118.0)
    result = run_gst_compliance_check(fields)
    assert _check(result, "tax_regime_consistency").result == CheckResult.PASS


def test_only_igst_regime_is_consistent():
    fields = _Fields(subtotal=100.0, cgst=0.0, sgst=0.0, igst=18.0, total_tax=18.0, total_amount=118.0)
    result = run_gst_compliance_check(fields)
    assert _check(result, "tax_regime_consistency").result == CheckResult.PASS


def test_no_tax_components_regime_check_is_not_checkable():
    fields = _Fields(subtotal=100.0, total_tax=None, total_amount=None)
    result = run_gst_compliance_check(fields)
    assert _check(result, "tax_regime_consistency").result == CheckResult.NOT_CHECKABLE


# --------------------------------------------------------------------
# Missing GST fields / NOT_CHECKABLE / incomplete invoice
# --------------------------------------------------------------------

def test_completely_empty_invoice_is_not_checkable_overall():
    fields = _Fields()  # everything None
    result = run_gst_compliance_check(fields)
    assert result.overall == CheckResult.NOT_CHECKABLE
    assert all(c.result == CheckResult.NOT_CHECKABLE for c in result.checks)


def test_missing_values_never_fabricated_as_pass_or_fail():
    fields = _Fields()
    result = run_gst_compliance_check(fields)
    for c in result.checks:
        assert c.expected is None
        assert c.actual is None


def test_partially_incomplete_invoice_mixes_statuses():
    # Only subtotal/tax/total present; no GSTINs, no CGST/SGST/IGST at all.
    fields = _Fields(subtotal=100.0, total_tax=18.0, total_amount=118.0)
    result = run_gst_compliance_check(fields)
    assert _check(result, "supplier_gstin_format").result == CheckResult.NOT_CHECKABLE
    assert _check(result, "cgst_plus_sgst").result == CheckResult.NOT_CHECKABLE
    assert _check(result, "igst_matches_total_gst").result == CheckResult.NOT_CHECKABLE
    assert _check(result, "subtotal_plus_tax_equals_grand_total").result == CheckResult.PASS
    # total_tax_matches_components: no cgst/sgst/igst at all -> NOT_CHECKABLE
    assert _check(result, "total_tax_matches_components").result == CheckResult.NOT_CHECKABLE


# --------------------------------------------------------------------
# JSON serialization
# --------------------------------------------------------------------

def test_result_to_dict_is_json_serializable():
    fields = _Fields(
        supplier_gstin=VALID_SUPPLIER_GSTIN, buyer_gstin=VALID_BUYER_GSTIN,
        subtotal=525.0, cgst=23.63, sgst=23.63, igst=0.0, total_tax=47.25, total_amount=572.25,
    )
    result = run_gst_compliance_check(fields)
    serialized = json.dumps(result.to_dict())
    assert isinstance(serialized, str)

    parsed = json.loads(serialized)
    assert parsed["overall"] == "PASS"
    assert len(parsed["checks"]) == len(result.checks)
    assert all(isinstance(c["detail"], str) for c in parsed["checks"])


def test_summary_counts_are_consistent():
    fields = _Fields(subtotal=100.0, cgst=9.0, sgst=9.0, total_tax=18.0, total_amount=118.0)
    result = run_gst_compliance_check(fields)
    summary = result.summary
    assert summary["passed"] + summary["failed"] + summary["not_checkable"] == summary["total_checks"]
    assert summary["total_checks"] == len(result.checks)
