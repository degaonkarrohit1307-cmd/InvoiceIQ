"""
Combined fraud scoring (Phase 4C).

Combines three independently-computed signals into a single explainable
0-100 score:
  1. supervised model fraud probability (learned from labeled examples)
  2. Isolation Forest anomaly score (learned from feature distribution only)
  3. Phase 4A deterministic mathematical verification result

The combination is a fixed, named, configurable weighted sum -- not a
black box. Risk category thresholds are explicit configuration values,
not "industry-certified" cutoffs; they're reasonable defaults for this
project's synthetic-data development context and should be reviewed
before any real deployment.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from app.services.math_verification import MathVerificationResult


class RiskCategory(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


@dataclass
class FraudScoringConfig:
    supervised_weight: float = 0.5
    anomaly_weight: float = 0.3
    mathematical_weight: float = 0.2

    low_threshold: float = 39.0
    medium_threshold: float = 69.0

    math_fail_weight: float = 100.0
    math_uncertainty_weight: float = 15.0

    def validate(self) -> None:
        total = self.supervised_weight + self.anomaly_weight + self.mathematical_weight
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"Fraud scoring weights must sum to 1.0, got {total}.")
        if not (0 <= self.low_threshold < self.medium_threshold <= 100):
            raise ValueError("Risk thresholds must satisfy 0 <= low_threshold < medium_threshold <= 100.")


@dataclass
class FraudScoreResult:
    supervised_score: float
    anomaly_score: float
    mathematical_score: float
    final_score: float
    risk_category: RiskCategory
    breakdown: dict = field(default_factory=dict)


def mathematical_score_from_verification(math_result: MathVerificationResult, config: FraudScoringConfig) -> float:
    """
    Converts a Phase 4A MathVerificationResult into a 0-100 risk score.

    Rationale (explicit, not a black box):
      - Each FAILed check contributes proportionally: (failed / total) * math_fail_weight.
      - Each NOT_CHECKABLE check contributes a SMALLER amount, reflecting
        genuine uncertainty rather than evidence of a problem:
        (not_checkable / total) * math_uncertainty_weight.
      - A fully PASSing verification (no fails, no uncertainty) scores 0.
      - No checks at all (total == 0) scores 0 rather than fabricating a
        risk signal from nothing.
    """
    summary = math_result.summary
    total = summary["total_checks"]
    if total == 0:
        return 0.0

    fail_component = config.math_fail_weight * (summary["failed"] / total)
    uncertainty_component = config.math_uncertainty_weight * (summary["not_checkable"] / total)
    return min(100.0, fail_component + uncertainty_component)


def combine_fraud_score(
    supervised_probability: float,
    anomaly_normalized_score: float,
    math_result: MathVerificationResult,
    config: FraudScoringConfig | None = None,
) -> FraudScoreResult:
    """
    supervised_probability: [0, 1] from SupervisedFraudModel.predict_proba()
    anomaly_normalized_score: [0, 1] from IsolationForestDetector.normalized_score()
    math_result: the Phase 4A MathVerificationResult for this invoice
    """
    config = config or FraudScoringConfig()
    config.validate()

    supervised_score = max(0.0, min(100.0, supervised_probability * 100))
    anomaly_score = max(0.0, min(100.0, anomaly_normalized_score * 100))
    mathematical_score = mathematical_score_from_verification(math_result, config)

    final_score = (
        config.supervised_weight * supervised_score
        + config.anomaly_weight * anomaly_score
        + config.mathematical_weight * mathematical_score
    )
    final_score = max(0.0, min(100.0, final_score))

    if final_score <= config.low_threshold:
        risk_category = RiskCategory.LOW
    elif final_score <= config.medium_threshold:
        risk_category = RiskCategory.MEDIUM
    else:
        risk_category = RiskCategory.HIGH

    breakdown = {
        "supervised_contribution": round(config.supervised_weight * supervised_score, 2),
        "anomaly_contribution": round(config.anomaly_weight * anomaly_score, 2),
        "mathematical_contribution": round(config.mathematical_weight * mathematical_score, 2),
        "weights": {
            "supervised": config.supervised_weight,
            "anomaly": config.anomaly_weight,
            "mathematical": config.mathematical_weight,
        },
        "math_verification_overall": math_result.overall.value,
        "math_verification_summary": math_result.summary,
    }

    return FraudScoreResult(
        supervised_score=round(supervised_score, 2),
        anomaly_score=round(anomaly_score, 2),
        mathematical_score=round(mathematical_score, 2),
        final_score=round(final_score, 2),
        risk_category=risk_category,
        breakdown=breakdown,
    )
