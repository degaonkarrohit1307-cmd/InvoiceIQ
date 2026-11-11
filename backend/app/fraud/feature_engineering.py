"""
Feature engineering (Phase 4B).

Converts a SyntheticInvoiceRecord (or any object with the same attribute
shape, e.g. a real InvoiceExtraction ORM row later) into a flat numerical
feature dict suitable for ML.

CRITICAL: `extract_features()` NEVER reads `record.label` or
`record.fraud_type` -- those are provenance/target metadata, not inputs.
`build_feature_matrix()` returns features and labels as two SEPARATE
structures precisely to make that separation structurally hard to violate
by accident (a caller has to go out of their way to smuggle the label into
the feature dict).
"""
from __future__ import annotations

import statistics
from typing import Any

from app.services.math_verification import CheckResult, run_math_verification

# The full, explicit set of feature names this module produces. Kept as a
# constant so `build_feature_matrix` can guarantee every row has exactly
# these keys, in this order, even when a record has zero line items etc.
FEATURE_NAMES: tuple[str, ...] = (
    "subtotal",
    "total_tax",
    "discount",
    "total_amount",
    "num_line_items",
    "quantity_mean",
    "quantity_std",
    "unit_price_mean",
    "unit_price_std",
    "line_total_sum",
    "tax_ratio",
    "discount_ratio",
    "math_checks_failed",
    "math_checks_not_checkable",
    "math_overall_fail_flag",
    "grand_total_diff_abs",
    "max_line_item_diff_abs",
    "is_duplicate_number_flag",
)


def _safe_float(value: Any, default: float = 0.0) -> float:
    """Missing-value handling: None (or anything unconvertible) becomes `default`, never a crash or NaN."""
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _stats(values: list[float]) -> tuple[float, float]:
    """(mean, population_stdev). Returns (0.0, 0.0) for an empty list -- never raises."""
    if not values:
        return 0.0, 0.0
    mean = statistics.fmean(values)
    stdev = statistics.pstdev(values) if len(values) > 1 else 0.0
    return mean, stdev


def extract_features(record: Any) -> dict[str, float]:
    """
    record: any object exposing .line_items, .subtotal, .cgst, .sgst,
    .igst, .total_tax, .total_amount, .discount, .is_duplicate_of (all
    Optional). Does NOT read .label or .fraud_type.
    """
    subtotal = _safe_float(record.subtotal)
    total_tax = _safe_float(getattr(record, "total_tax", None))
    discount = _safe_float(getattr(record, "discount", None))
    total_amount = _safe_float(record.total_amount)

    line_items = list(getattr(record, "line_items", []) or [])
    quantities = [_safe_float(li.quantity) for li in line_items if li.quantity is not None]
    unit_prices = [_safe_float(li.unit_price) for li in line_items if li.unit_price is not None]
    line_totals = [_safe_float(li.line_total) for li in line_items if li.line_total is not None]

    quantity_mean, quantity_std = _stats(quantities)
    unit_price_mean, unit_price_std = _stats(unit_prices)

    tax_ratio = (total_tax / subtotal) if subtotal else 0.0
    discount_ratio = (discount / subtotal) if subtotal else 0.0

    math_result = run_math_verification(record, discount=getattr(record, "discount", None))
    failed = sum(1 for c in math_result.checks if c.result == CheckResult.FAIL)
    not_checkable = sum(1 for c in math_result.checks if c.result == CheckResult.NOT_CHECKABLE)
    overall_fail_flag = 1.0 if math_result.overall == CheckResult.FAIL else 0.0

    grand_total_check = next(
        (c for c in math_result.checks if c.rule == "subtotal_plus_tax_equals_grand_total"), None
    )
    grand_total_diff_abs = (
        abs(grand_total_check.difference)
        if grand_total_check and grand_total_check.difference is not None
        else 0.0
    )

    line_item_diffs = [
        abs(c.difference) for c in math_result.checks
        if c.rule.startswith("line_item_") and c.difference is not None
    ]
    max_line_item_diff_abs = max(line_item_diffs) if line_item_diffs else 0.0

    is_duplicate_flag = 1.0 if getattr(record, "is_duplicate_of", None) else 0.0

    features = {
        "subtotal": subtotal,
        "total_tax": total_tax,
        "discount": discount,
        "total_amount": total_amount,
        "num_line_items": float(len(line_items)),
        "quantity_mean": quantity_mean,
        "quantity_std": quantity_std,
        "unit_price_mean": unit_price_mean,
        "unit_price_std": unit_price_std,
        "line_total_sum": float(sum(line_totals)) if line_totals else 0.0,
        "tax_ratio": tax_ratio,
        "discount_ratio": discount_ratio,
        "math_checks_failed": float(failed),
        "math_checks_not_checkable": float(not_checkable),
        "math_overall_fail_flag": overall_fail_flag,
        "grand_total_diff_abs": grand_total_diff_abs,
        "max_line_item_diff_abs": max_line_item_diff_abs,
        "is_duplicate_number_flag": is_duplicate_flag,
    }
    assert tuple(features.keys()) == FEATURE_NAMES  # guards against drift between the two definitions
    return features


def build_feature_matrix(records: list[Any]) -> tuple[list[dict[str, float]], list[int]]:
    """
    Returns (features, labels) as two separate lists -- never a combined
    structure -- so a label can't accidentally end up inside a feature row.
    labels[i] corresponds to features[i]. 1 = fraud, 0 = legitimate.
    """
    features = [extract_features(r) for r in records]
    labels = [1 if getattr(r, "label", "legitimate") == "fraud" else 0 for r in records]
    return features, labels
