"""
SHAP explainability for the supervised fraud model (Phase 7).

IMPORTANT, HONEST SCOPE: this explains the SUPERVISED MODEL's prediction
only -- it does not explain the Isolation Forest anomaly score or the
mathematical verification result, and it is NOT proof that an invoice is
fraudulent. SHAP values describe which features pushed THIS model's output
up or down for this specific input, relative to its training data; a
feature contributing positively to a fraud probability is evidence about
model behavior, not a finding of fact about the invoice.

Two independently-verified computation paths, chosen empirically after
discovering that this environment's shap/lightgbm version combination has
a real compatibility bug in shap's `TreeExplainer.shap_values()` /
`explainer(X)` call paths for LGBMClassifier (binary classification):
  - LightGBM: uses LightGBM's OWN native `pred_contrib` SHAP computation
    (`booster.predict(X, pred_contrib=True)`), which is unaffected by the
    shap-library bug and is verified (in this module's tests) to sum
    exactly to the model's own predict_proba output via a sigmoid.
  - sklearn GradientBoostingClassifier fallback: uses shap.TreeExplainer
    normally (no bug observed for this estimator type in this environment).

Both paths are deterministic for a fixed fitted model and fixed input
(tree-based SHAP has no sampling/randomness), and both are checked against
each other via the additivity property (contributions + base value, after
a sigmoid, equal the model's own predicted probability) as a self-test the
module recomputes at call time -- not just asserted in a docstring.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from app.ml.supervised_model import SupervisedFraudModel

try:
    import shap as _shap
    _SHAP_LIBRARY_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised only when shap truly isn't installed
    _SHAP_LIBRARY_AVAILABLE = False

# Model types this module knows how to explain. A model_type outside this
# set (e.g. some future, non-tree-based model) is reported as
# UNSUPPORTED_MODEL rather than guessed at.
_SUPPORTED_MODEL_TYPES = frozenset({"lightgbm", "sklearn_gradient_boosting"})

# Documented numerical tolerance for the SHAP additivity self-check:
# sigmoid(sum(full contribution vector) + base_value) must match the
# model's own predict_proba() to within this absolute probability
# difference. 1e-4 comfortably exceeds ordinary floating-point rounding
# noise (differences are normally ~1e-9 to 1e-12 in practice, as verified
# empirically against this environment's LightGBM/shap versions) while
# still catching a genuine mismatch between the reported contributions and
# the model's actual prediction. If the check fails, the explanation is
# withheld (ERROR status) rather than shown as if it were verified.
ADDITIVITY_TOLERANCE = 1e-4


class SHAPExplanationStatus:
    AVAILABLE = "AVAILABLE"
    SHAP_LIBRARY_UNAVAILABLE = "SHAP_LIBRARY_UNAVAILABLE"
    UNSUPPORTED_MODEL = "UNSUPPORTED_MODEL"
    ERROR = "ERROR"


@dataclass
class FeatureContribution:
    feature: str
    feature_value: float
    shap_value: float  # contribution in margin (log-odds) space; sign/magnitude meaningful, not a probability itself

    def to_dict(self) -> dict:
        return {"feature": self.feature, "feature_value": self.feature_value, "shap_value": self.shap_value}


@dataclass
class SHAPExplanationResult:
    status: str
    detail: str
    model_type: str | None = None
    predicted_probability: float | None = None  # the model's own predict_proba, for cross-reference
    reconstructed_probability: float | None = None  # sigmoid(sum(shap values) + base value) -- the self-check
    base_value: float | None = None
    contributions: list[FeatureContribution] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "detail": self.detail,
            "model_type": self.model_type,
            "predicted_probability": self.predicted_probability,
            "reconstructed_probability": self.reconstructed_probability,
            "base_value": self.base_value,
            "contributions": [c.to_dict() for c in self.contributions],
        }


def _unavailable(detail: str, model_type: str | None = None) -> SHAPExplanationResult:
    return SHAPExplanationResult(status=SHAPExplanationStatus.SHAP_LIBRARY_UNAVAILABLE, detail=detail, model_type=model_type)


def _unsupported(detail: str, model_type: str) -> SHAPExplanationResult:
    return SHAPExplanationResult(status=SHAPExplanationStatus.UNSUPPORTED_MODEL, detail=detail, model_type=model_type)


def _error(detail: str, model_type: str) -> SHAPExplanationResult:
    return SHAPExplanationResult(status=SHAPExplanationStatus.ERROR, detail=detail, model_type=model_type)


def _sigmoid(x: float) -> float:
    """
    Numerically stable sigmoid. The naive `1 / (1 + exp(-x))` overflows
    `math.exp` (raises OverflowError) for sufficiently large-magnitude
    finite `x` (e.g. x <= ~-746, where exp(-x) = exp(746+) exceeds a
    float's range). This splits on the sign of `x` so `math.exp` is only
    ever called on a non-positive argument, which cannot overflow:
      - x >= 0: 1 / (1 + exp(-x))      (exp(-x) with -x <= 0)
      - x <  0: exp(x) / (1 + exp(x))  (exp(x) with x < 0)
    Both forms are mathematically identical to the standard sigmoid.
    """
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)


def _validate_and_order_features(feature_names: list[str], features: dict[str, float]) -> tuple[list[float] | None, str | None]:
    """
    Validates that every feature the model was trained on is actually
    present in `features` and holds a finite numeric value, returning the
    values in `feature_names` order (preserving the model's own training
    feature order -- required for the contribution vector to line up with
    the model correctly).

    Returns (ordered_values, None) on success, or (None, error_detail) on
    failure. A missing feature or a non-finite value (NaN/inf) is NEVER
    silently coerced to 0.0 -- that would fabricate an input the model
    never actually saw and make the resulting explanation meaningless.
    """
    missing = [name for name in feature_names if name not in features]
    if missing:
        return None, f"Missing required feature(s): {', '.join(missing)}."

    non_finite: list[str] = []
    ordered_values: list[float] = []
    for name in feature_names:
        raw_value = features[name]
        try:
            value = float(raw_value)
        except (TypeError, ValueError):
            non_finite.append(name)
            continue
        if not math.isfinite(value):
            non_finite.append(name)
            continue
        ordered_values.append(value)

    if non_finite:
        return None, f"Non-finite or non-numeric value(s) for required feature(s): {', '.join(non_finite)}."

    return ordered_values, None


def _explain_lightgbm(model: SupervisedFraudModel, row: list[list[float]]) -> tuple[list[float], float]:
    """Returns (per_feature_contributions, base_value) in margin space, via LightGBM's native pred_contrib."""
    contrib = model.estimator.predict(row, pred_contrib=True)[0]
    feature_contributions = list(contrib[:-1])
    base_value = float(contrib[-1])
    return feature_contributions, base_value


def _explain_sklearn_gbc(model: SupervisedFraudModel, row: list[list[float]]) -> tuple[list[float], float]:
    """Returns (per_feature_contributions, base_value) in margin space, via shap.TreeExplainer."""
    import numpy as np

    explainer = _shap.TreeExplainer(model.estimator)
    shap_values = explainer.shap_values(np.array(row))
    feature_contributions = list(shap_values[0])
    base_value = float(explainer.expected_value[0] if hasattr(explainer.expected_value, "__len__") else explainer.expected_value)
    return feature_contributions, base_value


def explain_supervised_prediction(
    model: SupervisedFraudModel,
    features: dict[str, float],
    top_n: int = 5,
) -> SHAPExplanationResult:
    """
    Explains ONE supervised-model prediction for ONE invoice's already-
    extracted feature dict. Does not retrain or refit anything -- `model`
    must already be fitted (e.g. from FraudAnalysisPipeline.supervised_model).

    Returns a result with `status` ALWAYS set -- never raises for a missing
    dependency or unsupported model; those are reported as a clear status
    rather than an exception, per the "handle gracefully" requirement.
    An unexpected failure during SHAP computation itself (status=ERROR)
    still does not fabricate contribution values.
    """
    model_type = model.model_type

    if model_type == "lightgbm" and not hasattr(model.estimator, "predict"):
        return _error("LightGBM model does not expose a predict() method.", model_type)

    if model_type not in _SUPPORTED_MODEL_TYPES:
        return _unsupported(
            f"SHAP explanation is not implemented for model_type='{model_type}'.", model_type,
        )

    if model_type == "sklearn_gradient_boosting" and not _SHAP_LIBRARY_AVAILABLE:
        return _unavailable(
            "The 'shap' library is not installed, so feature-level explanations are unavailable "
            "for this model type.", model_type,
        )

    ordered_values, validation_error = _validate_and_order_features(model.feature_names, features)
    if validation_error:
        return _error(validation_error, model_type)

    row = [ordered_values]  # preserves model.feature_names order -- required for correct contribution alignment

    try:
        if model_type == "lightgbm":
            feature_contributions, base_value = _explain_lightgbm(model, row)
        else:
            feature_contributions, base_value = _explain_sklearn_gbc(model, row)
    except Exception as exc:  # noqa: BLE001 - SHAP/LightGBM internals fail in varied, library-specific ways
        return _error(f"SHAP computation failed: {exc}", model_type)

    predicted_probability = model.predict_proba(features)
    # Additivity check uses the COMPLETE contribution vector (all features,
    # not just the eventual top_n) -- truncating first would make this
    # check meaningless, since a partial sum would almost never reconstruct
    # the true prediction.
    reconstructed_probability = _sigmoid(sum(feature_contributions) + base_value)

    # Explicit finiteness check BEFORE the additivity comparison. This is
    # not redundant with that comparison: in Python, any `>` comparison
    # involving NaN evaluates to False, so `abs(nan - x) > TOLERANCE` is
    # silently False -- a non-finite value could otherwise slip through
    # the additivity check below and be reported as a falsely-"verified"
    # AVAILABLE explanation. Checked separately and explicitly instead.
    non_finite_parts: list[str] = []
    if not math.isfinite(predicted_probability):
        non_finite_parts.append("predicted probability")
    if not math.isfinite(reconstructed_probability):
        non_finite_parts.append("reconstructed probability")
    if not math.isfinite(base_value):
        non_finite_parts.append("base value")
    if any(not math.isfinite(value) for value in feature_contributions):
        non_finite_parts.append("one or more feature contributions")

    if non_finite_parts:
        return _error(
            f"SHAP explanation produced non-finite value(s) for: {', '.join(non_finite_parts)}. "
            "The explanation is withheld rather than shown as verified.",
            model_type,
        )

    if abs(predicted_probability - reconstructed_probability) > ADDITIVITY_TOLERANCE:
        return _error(
            f"SHAP additivity check failed: reconstructed probability ({reconstructed_probability:.6f}) "
            f"differs from the model's actual predicted probability ({predicted_probability:.6f}) by more "
            f"than the allowed tolerance ({ADDITIVITY_TOLERANCE}). The explanation is withheld rather than "
            "shown as verified, since it cannot be confirmed to correctly represent this prediction.",
            model_type,
        )

    contributions = [
        FeatureContribution(feature=name, feature_value=value, shap_value=float(contribution))
        for name, value, contribution in zip(model.feature_names, ordered_values, feature_contributions)
    ]
    contributions.sort(key=lambda c: abs(c.shap_value), reverse=True)
    top_contributions = contributions[: max(top_n, 0)]

    return SHAPExplanationResult(
        status=SHAPExplanationStatus.AVAILABLE,
        detail=(
            f"Top {len(top_contributions)} feature contributions to this model's fraud-probability prediction, "
            "in descending order of magnitude. This explains model behavior only -- it is not evidence of fraud."
        ),
        model_type=model_type,
        predicted_probability=predicted_probability,
        reconstructed_probability=reconstructed_probability,
        base_value=base_value,
        contributions=top_contributions,
    )
