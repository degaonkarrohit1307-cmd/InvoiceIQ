"""Phase 4A: rule-based mathematical verification tests. No DB/external infra required."""
from decimal import Decimal

from app.services.math_verification import (
    CheckResult,
    run_math_verification,
    verify_discount_consistency,
    verify_grand_total,
)


class _LI:
    def __init__(self, quantity=None, unit_price=None, line_total=None):
        self.quantity, self.unit_price, self.line_total = quantity, unit_price, line_total


class _Fields:
    def __init__(self, line_items=None, subtotal=None, cgst=None, sgst=None, igst=None,
                 total_tax=None, total_amount=None):
        self.line_items = line_items or []
        self.subtotal, self.cgst, self.sgst, self.igst = subtotal, cgst, sgst, igst
        self.total_tax, self.total_amount = total_tax, total_amount


def test_correct_line_item_passes():
    result = run_math_verification(_Fields(line_items=[_LI(2, 100.0, 200.0)], subtotal=200.0,
                                            total_tax=0, total_amount=200.0))
    assert next(c for c in result.checks if c.rule == "line_item_1_total").result == CheckResult.PASS


def test_incorrect_line_item_fails():
    result = run_math_verification(_Fields(line_items=[_LI(2, 100.0, 999.0)], subtotal=999.0,
                                             total_tax=0, total_amount=999.0))
    check = next(c for c in result.checks if c.rule == "line_item_1_total")
    assert check.result == CheckResult.FAIL
    assert check.expected == 200.0
    assert check.actual == 999.0
    assert check.tolerance == float(Decimal("0.02"))


def test_subtotal_pass():
    result = run_math_verification(_Fields(line_items=[_LI(1, 100.0, 100.0), _LI(1, 50.0, 50.0)],
                                             subtotal=150.0, total_tax=0, total_amount=150.0))
    assert next(c for c in result.checks if c.rule == "subtotal_matches_line_items").result == CheckResult.PASS


def test_subtotal_fail():
    result = run_math_verification(_Fields(line_items=[_LI(1, 100.0, 100.0)], subtotal=999.0,
                                             total_tax=0, total_amount=999.0))
    assert next(c for c in result.checks if c.rule == "subtotal_matches_line_items").result == CheckResult.FAIL


def test_cgst_sgst_pass_and_fail():
    good = run_math_verification(_Fields(cgst=9.0, sgst=9.0, total_tax=18.0))
    assert next(c for c in good.checks if c.rule == "cgst_plus_sgst").result == CheckResult.PASS

    bad = run_math_verification(_Fields(cgst=9.0, sgst=9.0, total_tax=50.0))
    assert next(c for c in bad.checks if c.rule == "cgst_plus_sgst").result == CheckResult.FAIL


def test_igst_used_instead_of_cgst_sgst_when_present():
    result = run_math_verification(_Fields(igst=18.0, total_tax=18.0))
    rules = [c.rule for c in result.checks]
    assert "igst_matches_total_gst" in rules
    assert "cgst_plus_sgst" not in rules
    assert next(c for c in result.checks if c.rule == "igst_matches_total_gst").result == CheckResult.PASS


def test_igst_fail_scenario():
    result = run_math_verification(_Fields(igst=18.0, total_tax=99.0))
    assert next(c for c in result.checks if c.rule == "igst_matches_total_gst").result == CheckResult.FAIL


def test_grand_total_pass_and_fail():
    good = verify_grand_total(100.0, 18.0, 118.0)
    assert good.result == CheckResult.PASS

    bad = verify_grand_total(100.0, 18.0, 500.0)
    assert bad.result == CheckResult.FAIL


def test_grand_total_with_discount():
    # 100 + 18 - 10 = 108
    passing = verify_grand_total(100.0, 18.0, 108.0, discount=10.0)
    assert passing.result == CheckResult.PASS

    failing = verify_grand_total(100.0, 18.0, 118.0, discount=10.0)  # ignores discount -> should fail now
    assert failing.result == CheckResult.FAIL


def test_grand_total_without_discount_defaults_to_zero():
    """Absence of a discount value must not make the check NOT_CHECKABLE -- it's treated as 0."""
    result = verify_grand_total(100.0, 18.0, 118.0, discount=None)
    assert result.result == CheckResult.PASS


def test_missing_values_produce_not_checkable_never_fabricated():
    result = run_math_verification(_Fields())  # everything None
    assert result.overall == CheckResult.NOT_CHECKABLE
    assert all(c.result == CheckResult.NOT_CHECKABLE for c in result.checks)
    # None of the NOT_CHECKABLE checks should have fabricated expected/actual values.
    for c in result.checks:
        assert c.expected is None
        assert c.actual is None


def test_missing_quantity_is_not_checkable_not_zero():
    result = run_math_verification(_Fields(line_items=[_LI(quantity=None, unit_price=100.0, line_total=0.0)]))
    check = next(c for c in result.checks if c.rule == "line_item_1_total")
    assert check.result == CheckResult.NOT_CHECKABLE
    assert check.expected is None  # never fabricated as 0


def test_tolerance_boundary_pass_and_fail():
    # exactly at tolerance boundary (0.02) -> PASS
    at_boundary = verify_grand_total(100.0, 0.0, 100.02, tolerance=Decimal("0.02"))
    assert at_boundary.result == CheckResult.PASS

    # just over tolerance -> FAIL
    over_boundary = verify_grand_total(100.0, 0.0, 100.03, tolerance=Decimal("0.02"))
    assert over_boundary.result == CheckResult.FAIL

    # custom stricter tolerance
    strict = verify_grand_total(100.0, 0.0, 100.005, tolerance=Decimal("0.001"))
    assert strict.result == CheckResult.FAIL


def test_discount_consistency_pass_fail_not_checkable():
    passing = verify_discount_consistency(discount=10.0, subtotal=100.0)
    assert passing.result == CheckResult.PASS

    failing = verify_discount_consistency(discount=150.0, subtotal=100.0)
    assert failing.result == CheckResult.FAIL

    not_checkable = verify_discount_consistency(discount=None, subtotal=100.0)
    assert not_checkable.result == CheckResult.NOT_CHECKABLE


def test_summary_counts_are_accurate():
    result = run_math_verification(_Fields(
        line_items=[_LI(1, 100.0, 100.0)], subtotal=100.0, cgst=9.0, sgst=9.0,
        total_tax=18.0, total_amount=118.0,
    ))
    summary = result.summary
    assert summary["total_checks"] == len(result.checks)
    assert summary["passed"] + summary["failed"] + summary["not_checkable"] == summary["total_checks"]
    assert summary["overall"] in {"PASS", "FAIL", "NOT_CHECKABLE"}


def test_unparseable_discount_produces_warning_not_crash():
    result = run_math_verification(_Fields(subtotal=100.0, total_tax=0, total_amount=100.0),
                                    discount="not-a-number")
    assert any("could not be parsed" in w for w in result.warnings)
