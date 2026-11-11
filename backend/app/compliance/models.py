"""
GST/tax compliance result models (Phase 6A).

Deliberately reuses `CheckResult` and `RuleCheck` from
app.services.math_verification rather than defining parallel PASS/FAIL/
NOT_CHECKABLE types -- the shape of "a deterministic check with an
expected/actual/difference/tolerance/detail" is identical here, so sharing
the type avoids duplicating that structure while GST compliance's actual
RULES (what gets compared to what, GSTIN format checks, etc.) remain
entirely separate business logic in gst.py.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.services.math_verification import CheckResult, RuleCheck

# Re-exported so callers of app.compliance.models don't need to also
# import from app.services.math_verification directly.
__all__ = ["CheckResult", "RuleCheck", "GSTComplianceResult"]


@dataclass
class GSTComplianceResult:
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

    def to_dict(self) -> dict:
        """JSON-serializable representation."""
        return {
            "overall": self.overall.value,
            "checks": [
                {
                    "rule": c.rule,
                    "result": c.result.value,
                    "expected": c.expected,
                    "actual": c.actual,
                    "difference": c.difference,
                    "tolerance": c.tolerance,
                    "detail": c.detail,
                }
                for c in self.checks
            ],
            "warnings": list(self.warnings),
        }
