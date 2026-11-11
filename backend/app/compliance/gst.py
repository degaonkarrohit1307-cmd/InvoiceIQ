"""
GST/tax compliance rules (Phase 6A).

Scope and honesty constraints (deliberate, see each function's docstring):
  - GSTIN checks here are FORMAT validation only (via the existing
    `is_valid_gstin_format`). A format-valid GSTIN is NOT claimed to be
    real, active, or registered -- that requires an external GST
    government API lookup, explicitly out of scope for this phase.
  - No external network calls are made anywhere in this module.
  - Every check is deterministic and returns PASS / FAIL / NOT_CHECKABLE
    (never fabricates a result when required inputs are missing).

Reuse policy: where an existing math_verification function already
computes exactly the needed comparison (CGST+SGST vs tax, IGST vs tax,
subtotal+tax vs grand total), this module calls that function directly
instead of reimplementing the arithmetic. Checks that don't already exist
there (GSTIN format, total_tax-vs-components) are implemented locally.
"""
from __future__ import annotations

from decimal import Decimal

from app.services.math_verification import (
    DEFAULT_TOLERANCE,
    CheckResult,
    RuleCheck,
    _to_decimal,
    verify_cgst_sgst,
    verify_grand_total,
    verify_igst,
)
from app.services.normalization import is_valid_gstin_format
from app.compliance.models import GSTComplianceResult


def validate_gstin_format(gstin: str | None, label: str) -> RuleCheck:
    """
    FORMAT validation only -- structural check via the existing regex-based
    `is_valid_gstin_format`. This does NOT verify the GSTIN belongs to a
    real, active, or registered taxpayer; that would require a live GST
    government API call, which this phase deliberately does not make.
    """
    rule = f"{label}_gstin_format"
    if gstin is None:
        return RuleCheck(rule=rule, result=CheckResult.NOT_CHECKABLE, detail=f"No {label} GSTIN was provided.")

    if is_valid_gstin_format(gstin):
        return RuleCheck(
            rule=rule, result=CheckResult.PASS,
            detail=(
                f"{label.capitalize()} GSTIN matches the expected structural format. "
                "This confirms format only -- it does NOT confirm the GSTIN is real, "
                "active, or registered with GST authorities."
            ),
        )

    return RuleCheck(
        rule=rule, result=CheckResult.FAIL,
        detail=f"{label.capitalize()} GSTIN '{gstin}' does not match the expected structural format.",
    )


def _is_populated(value) -> bool:
    """True if a tax-component value is present and non-zero (i.e. actually used on this invoice)."""
    decimal_value = _to_decimal(value)
    return decimal_value is not None and decimal_value != 0


def verify_cgst_sgst_applicability(cgst, sgst, total_tax, tolerance: Decimal = DEFAULT_TOLERANCE) -> RuleCheck:
    """
    A GST invoice is either intra-state (CGST+SGST) or inter-state (IGST),
    never both. Running verify_cgst_sgst() unconditionally on an
    inter-state invoice (CGST=SGST=0) would spuriously FAIL against a
    nonzero total_tax that's entirely IGST -- so this wrapper first checks
    whether CGST/SGST are actually populated (non-zero) before delegating
    the real arithmetic check to the existing math_verification function.
    """
    if not _is_populated(cgst) and not _is_populated(sgst):
        return RuleCheck(
            rule="cgst_plus_sgst", result=CheckResult.NOT_CHECKABLE, tolerance=float(tolerance),
            detail="CGST/SGST are not populated (or are zero) -- likely an inter-state invoice using IGST instead.",
        )
    return verify_cgst_sgst(cgst, sgst, total_tax, tolerance)


def verify_igst_applicability(igst, total_tax, tolerance: Decimal = DEFAULT_TOLERANCE) -> RuleCheck:
    """Mirror of verify_cgst_sgst_applicability for the IGST (inter-state) side."""
    if not _is_populated(igst):
        return RuleCheck(
            rule="igst_matches_total_gst", result=CheckResult.NOT_CHECKABLE, tolerance=float(tolerance),
            detail="IGST is not populated (or is zero) -- likely an intra-state invoice using CGST/SGST instead.",
        )
    return verify_igst(igst, total_tax, tolerance)


def verify_tax_regime_consistency(cgst, sgst, igst, tolerance: Decimal = DEFAULT_TOLERANCE) -> RuleCheck:
    """
    An invoice is intra-state (CGST+SGST) XOR inter-state (IGST) -- never
    both. This check explicitly flags the case where BOTH regimes have
    non-zero values populated, which is itself a structural inconsistency
    regardless of whether the individual CGST+SGST-vs-tax or IGST-vs-tax
    arithmetic happens to reconcile.
    """
    cgst_sgst_present = _is_populated(cgst) or _is_populated(sgst)
    igst_present = _is_populated(igst)

    if not cgst_sgst_present and not igst_present:
        return RuleCheck(
            rule="tax_regime_consistency", result=CheckResult.NOT_CHECKABLE, tolerance=float(tolerance),
            detail="No usable CGST, SGST, or IGST values were provided to determine the tax regime.",
        )

    if cgst_sgst_present and igst_present:
        return RuleCheck(
            rule="tax_regime_consistency", result=CheckResult.FAIL, tolerance=float(tolerance),
            detail=(
                "Both CGST/SGST (intra-state) and IGST (inter-state) are populated with non-zero "
                "values on the same invoice. A GST invoice should use exactly one tax regime, not both."
            ),
        )

    regime = "CGST/SGST (intra-state)" if cgst_sgst_present else "IGST (inter-state)"
    return RuleCheck(
        rule="tax_regime_consistency", result=CheckResult.PASS, tolerance=float(tolerance),
        detail=f"Invoice consistently uses a single tax regime: {regime}.",
    )


def verify_total_tax_matches_components(cgst, sgst, igst, total_tax,
                                         tolerance: Decimal = DEFAULT_TOLERANCE) -> RuleCheck:
    """
    (CGST + SGST + IGST) ~= total_tax. Distinct from verify_cgst_sgst /
    verify_igst (which each compare only their own component(s) against
    total_tax individually) -- this checks that ALL THREE components
    together reconcile with the stated total_tax, catching cases where,
    say, an invoice has both CGST/SGST AND a nonzero IGST which shouldn't
    normally coexist, or a component was simply omitted from the total.
    """
    c, s, i = _to_decimal(cgst), _to_decimal(sgst), _to_decimal(igst)
    if c is None and s is None and i is None:
        return RuleCheck(
            rule="total_tax_matches_components", result=CheckResult.NOT_CHECKABLE,
            tolerance=float(tolerance), detail="No CGST, SGST, or IGST values were provided.",
        )

    component_sum = (c or Decimal("0")) + (s or Decimal("0")) + (i or Decimal("0"))
    actual = _to_decimal(total_tax)
    if actual is None:
        return RuleCheck(
            rule="total_tax_matches_components", result=CheckResult.NOT_CHECKABLE,
            tolerance=float(tolerance), detail="No total_tax value was provided to compare against.",
        )

    diff = abs(component_sum - actual)
    passed = diff <= tolerance
    return RuleCheck(
        rule="total_tax_matches_components",
        result=CheckResult.PASS if passed else CheckResult.FAIL,
        expected=float(component_sum), actual=float(actual), difference=float(diff), tolerance=float(tolerance),
        detail=(
            "Sum of CGST + SGST + IGST matches the stated total tax."
            if passed else
            f"Sum of CGST + SGST + IGST ({component_sum}) does not match the stated total tax ({actual})."
        ),
    )


def run_gst_compliance_check(fields, tolerance: Decimal = DEFAULT_TOLERANCE) -> GSTComplianceResult:
    """
    fields: an ExtractedInvoiceFields (or any object exposing the same
    attribute names: supplier_gstin, buyer_gstin, subtotal, cgst, sgst,
    igst, total_tax, total_amount).

    Runs the full Phase 6A GST compliance rule set. Does not raise for
    missing data -- missing fields simply produce NOT_CHECKABLE checks.
    """
    checks: list[RuleCheck] = []

    checks.append(validate_gstin_format(getattr(fields, "supplier_gstin", None), "supplier"))
    checks.append(validate_gstin_format(getattr(fields, "buyer_gstin", None), "buyer"))

    cgst = getattr(fields, "cgst", None)
    sgst = getattr(fields, "sgst", None)
    igst = getattr(fields, "igst", None)
    total_tax = getattr(fields, "total_tax", None)
    subtotal = getattr(fields, "subtotal", None)
    total_amount = getattr(fields, "total_amount", None)

    # Reused, unmodified arithmetic from Phase 4A math_verification -- not
    # duplicated here, only wrapped with GST-specific applicability logic
    # (an invoice is intra-state XOR inter-state, never both at once).
    checks.append(verify_cgst_sgst_applicability(cgst, sgst, total_tax, tolerance))
    checks.append(verify_igst_applicability(igst, total_tax, tolerance))
    checks.append(verify_tax_regime_consistency(cgst, sgst, igst, tolerance))
    checks.append(verify_grand_total(subtotal, total_tax, total_amount, discount=None, tolerance=tolerance))

    # New to this module -- not present in math_verification.
    checks.append(verify_total_tax_matches_components(cgst, sgst, igst, total_tax, tolerance))

    return GSTComplianceResult(checks=checks)
