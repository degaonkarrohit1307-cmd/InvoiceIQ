"""
Deterministic rule-based invoice arithmetic checks (Phase 4A, no ML).

Each check returns PASS / FAIL / NOT_CHECKABLE. NOT_CHECKABLE means
required inputs were missing -- a missing value is NEVER silently treated
as zero, and a check is never fabricated as PASS/FAIL when its inputs are
absent. A FAIL is evidence/signal only, not an automatic fraud
classification -- that judgment belongs to a later phase.

Design is intentionally extensible: additional rules (e.g. future GST
compliance checks) can be added as more `_compare(...)`-based functions
without touching the result model.
"""
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from enum import Enum

DEFAULT_TOLERANCE = Decimal("0.02")  # absolute currency-unit tolerance for rounding differences


class CheckResult(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    NOT_CHECKABLE = "NOT_CHECKABLE"


@dataclass
class RuleCheck:
    rule: str
    result: CheckResult
    expected: float | None = None
    actual: float | None = None
    difference: float | None = None
    tolerance: float | None = None
    detail: str = ""


@dataclass
class MathVerificationResult:
    checks: list[RuleCheck] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def overall(self) -> CheckResult:
        results = [c.result for c in self.checks]
        if any(r == CheckResult.FAIL for r in results):
            return CheckResult.FAIL
        if all(r == CheckResult.NOT_CHECKABLE for r in results):
            return CheckResult.NOT_CHECKABLE
        return CheckResult.PASS

    @property
    def summary(self) -> dict:
        counts = {CheckResult.PASS.value: 0, CheckResult.FAIL.value: 0, CheckResult.NOT_CHECKABLE.value: 0}
        for c in self.checks:
            counts[c.result.value] += 1
        return {
            "overall": self.overall.value,
            "total_checks": len(self.checks),
            "passed": counts[CheckResult.PASS.value],
            "failed": counts[CheckResult.FAIL.value],
            "not_checkable": counts[CheckResult.NOT_CHECKABLE.value],
            "warnings": list(self.warnings),
        }


def _to_decimal(value) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _compare(rule: str, expected: Decimal | None, actual: Decimal | None, tolerance: Decimal,
             missing_detail: str = "Required input value(s) missing.") -> RuleCheck:
    if expected is None or actual is None:
        return RuleCheck(rule=rule, result=CheckResult.NOT_CHECKABLE, tolerance=float(tolerance), detail=missing_detail)
    diff = abs(expected - actual)
    passed = diff <= tolerance
    return RuleCheck(
        rule=rule,
        result=CheckResult.PASS if passed else CheckResult.FAIL,
        expected=float(expected),
        actual=float(actual),
        difference=float(diff),
        tolerance=float(tolerance),
        detail="Within tolerance." if passed else f"Difference {diff} exceeds tolerance {tolerance}.",
    )


def verify_line_items(line_items: list, tolerance: Decimal = DEFAULT_TOLERANCE) -> list[RuleCheck]:
    """quantity x unit_price ~= line_total, per line item."""
    checks = []
    for idx, item in enumerate(line_items, start=1):
        qty = _to_decimal(getattr(item, "quantity", None))
        price = _to_decimal(getattr(item, "unit_price", None))
        total = _to_decimal(getattr(item, "line_total", None))
        expected = (qty * price) if (qty is not None and price is not None) else None
        checks.append(_compare(
            f"line_item_{idx}_total", expected, total, tolerance,
            missing_detail="Missing quantity, unit price, and/or line total for this item.",
        ))
    return checks


def verify_subtotal(line_items: list, subtotal, tolerance: Decimal = DEFAULT_TOLERANCE) -> RuleCheck:
    """sum(line_totals) ~= subtotal."""
    totals = [_to_decimal(getattr(i, "line_total", None)) for i in line_items]
    if not line_items or any(t is None for t in totals):
        return RuleCheck("subtotal_matches_line_items", CheckResult.NOT_CHECKABLE, tolerance=float(tolerance),
                          detail="Missing line item totals or no line items.")
    expected = sum(totals, Decimal("0"))
    return _compare("subtotal_matches_line_items", expected, _to_decimal(subtotal), tolerance)


def verify_cgst_sgst(cgst, sgst, expected_total_gst, tolerance: Decimal = DEFAULT_TOLERANCE) -> RuleCheck:
    """CGST + SGST ~= applicable GST (intra-state invoice)."""
    c, s = _to_decimal(cgst), _to_decimal(sgst)
    expected_sum = (c + s) if (c is not None and s is not None) else None
    return _compare("cgst_plus_sgst", expected_sum, _to_decimal(expected_total_gst), tolerance,
                     missing_detail="Missing CGST, SGST, and/or comparison tax amount.")


def verify_igst(igst, expected_total_gst, tolerance: Decimal = DEFAULT_TOLERANCE) -> RuleCheck:
    """IGST ~= applicable total GST (inter-state invoice)."""
    return _compare("igst_matches_total_gst", _to_decimal(igst), _to_decimal(expected_total_gst), tolerance,
                     missing_detail="Missing IGST and/or comparison tax amount.")


def verify_grand_total(subtotal, total_tax, total_amount, discount=None,
                        tolerance: Decimal = DEFAULT_TOLERANCE) -> RuleCheck:
    """
    subtotal + total_tax - discount ~= grand_total.

    `discount` is optional (Phase 3 extraction does not currently produce
    one). When absent, it's treated as 0 in the arithmetic rather than
    making the whole check NOT_CHECKABLE -- an invoice legitimately has no
    discount most of the time, so requiring a discount value to be present
    would make this check NOT_CHECKABLE far more often than warranted.
    Missing subtotal/tax/total still correctly yields NOT_CHECKABLE via
    `_compare`.
    """
    sub, tax = _to_decimal(subtotal), _to_decimal(total_tax)
    disc = _to_decimal(discount) or Decimal("0")
    expected = (sub + tax - disc) if (sub is not None and tax is not None) else None
    return _compare("subtotal_plus_tax_equals_grand_total", expected, _to_decimal(total_amount),
                     tolerance, missing_detail="Missing subtotal and/or total tax.")


def verify_discount_consistency(discount, subtotal, tolerance: Decimal = DEFAULT_TOLERANCE) -> RuleCheck:
    """
    A discount should not exceed the subtotal it's applied to. This is a
    light sanity invariant, not a GST-specific rule. NOT_CHECKABLE if no
    discount was extracted (most invoices have none) or subtotal is missing.
    """
    disc, sub = _to_decimal(discount), _to_decimal(subtotal)
    if disc is None:
        return RuleCheck("discount_does_not_exceed_subtotal", CheckResult.NOT_CHECKABLE, tolerance=float(tolerance),
                          detail="No discount value present.")
    if sub is None:
        return RuleCheck("discount_does_not_exceed_subtotal", CheckResult.NOT_CHECKABLE, tolerance=float(tolerance),
                          detail="Missing subtotal to compare discount against.")
    passed = disc <= sub + tolerance
    return RuleCheck(
        "discount_does_not_exceed_subtotal",
        CheckResult.PASS if passed else CheckResult.FAIL,
        expected=float(sub), actual=float(disc), difference=float(disc - sub), tolerance=float(tolerance),
        detail="Discount is within the subtotal." if passed else "Discount exceeds the subtotal.",
    )


def run_math_verification(fields, discount=None, tolerance: Decimal = DEFAULT_TOLERANCE) -> MathVerificationResult:
    """
    fields: an ExtractedInvoiceFields (or InvoiceExtraction ORM row) with
    the standard attribute names (line_items, subtotal, cgst, sgst, igst,
    total_tax, total_amount).

    `discount` is accepted as an explicit optional parameter rather than
    read off `fields`, since the Phase 3 extraction/persistence models do
    not currently have a discount field -- callers that DO have a discount
    value (e.g. a future phase) can pass it in without requiring a schema
    change here.
    """
    checks: list[RuleCheck] = []
    warnings: list[str] = []

    checks.extend(verify_line_items(fields.line_items, tolerance))
    checks.append(verify_subtotal(fields.line_items, fields.subtotal, tolerance))

    cgst, sgst, igst = fields.cgst, fields.sgst, fields.igst
    total_tax = fields.total_tax
    if igst not in (None, 0, 0.0):
        checks.append(verify_igst(igst, total_tax, tolerance))
    else:
        checks.append(verify_cgst_sgst(cgst, sgst, total_tax, tolerance))

    checks.append(verify_grand_total(fields.subtotal, fields.total_tax, fields.total_amount, discount, tolerance))
    checks.append(verify_discount_consistency(discount, fields.subtotal, tolerance))

    if discount is not None and _to_decimal(discount) is None:
        warnings.append(f"Discount value '{discount}' could not be parsed as a number and was ignored.")

    return MathVerificationResult(checks=checks, warnings=warnings)
