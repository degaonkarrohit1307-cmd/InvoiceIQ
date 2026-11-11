"""
Phase 7 tests: SHAP explainability for the supervised fraud model.

No network calls. No model retraining beyond the single shared,
module-scoped fixture trained once for the whole file.
"""
import json

import pytest

from app.fraud.dataset import DatasetConfig
from app.fraud.feature_engineering import FEATURE_NAMES
from app.fraud.synthetic_data import FraudType, GeneratorConfig, SyntheticInvoiceGenerator
from app.ml.pipeline import FraudAnalysisPipeline
from app.ml.shap_explainer import (
    ADDITIVITY_TOLERANCE,
    SHAPExplanationStatus,
    explain_supervised_prediction,
)


@pytest.fixture(scope="module")
def trained_pipeline():
    pipeline = FraudAnalysisPipeline()
    pipeline.train(dataset_config=DatasetConfig(seed=42, n_legitimate=80, n_fraud=40))
    return pipeline


@pytest.fixture(scope="module")
def generator():
    return SyntheticInvoiceGenerator(GeneratorConfig(seed=500))


@pytest.fixture(scope="module")
def sample_features(trained_pipeline, generator):
    from app.fraud.feature_engineering import extract_features
    record = generator.generate_fraud_invoice(FraudType.MATH_MISMATCH)
    return extract_features(record)


# --------------------------------------------------------------------
# Contribution structure
# --------------------------------------------------------------------

def test_explanation_status_is_available_for_lightgbm(trained_pipeline, sample_features):
    assert trained_pipeline.supervised_model.model_type == "lightgbm"
    result = explain_supervised_prediction(trained_pipeline.supervised_model, sample_features)
    assert result.status == SHAPExplanationStatus.AVAILABLE


def test_contributions_respect_top_n(trained_pipeline, sample_features):
    result = explain_supervised_prediction(trained_pipeline.supervised_model, sample_features, top_n=3)
    assert len(result.contributions) == 3


def test_contributions_sorted_by_absolute_magnitude_descending(trained_pipeline, sample_features):
    result = explain_supervised_prediction(trained_pipeline.supervised_model, sample_features, top_n=10)
    magnitudes = [abs(c.shap_value) for c in result.contributions]
    assert magnitudes == sorted(magnitudes, reverse=True)


def test_each_contribution_has_feature_name_and_value(trained_pipeline, sample_features):
    result = explain_supervised_prediction(trained_pipeline.supervised_model, sample_features)
    for c in result.contributions:
        assert isinstance(c.feature, str) and c.feature
        assert isinstance(c.shap_value, float)
        assert isinstance(c.feature_value, float)


# --------------------------------------------------------------------
# Feature alignment (SHAP additivity self-check: contributions genuinely
# correspond to this model's own predicted probability)
# --------------------------------------------------------------------

def test_reconstructed_probability_matches_model_predicted_probability(trained_pipeline, sample_features):
    result = explain_supervised_prediction(trained_pipeline.supervised_model, sample_features)
    assert result.predicted_probability == pytest.approx(result.reconstructed_probability, abs=1e-6)


def test_contribution_feature_names_are_a_subset_of_known_features(trained_pipeline, sample_features):
    result = explain_supervised_prediction(trained_pipeline.supervised_model, sample_features, top_n=100)
    contributed_names = {c.feature for c in result.contributions}
    assert contributed_names.issubset(set(FEATURE_NAMES))


def test_contribution_feature_values_match_input_features(trained_pipeline, sample_features):
    result = explain_supervised_prediction(trained_pipeline.supervised_model, sample_features, top_n=100)
    for c in result.contributions:
        assert c.feature_value == pytest.approx(sample_features[c.feature])


def test_all_features_contributed_when_top_n_covers_all(trained_pipeline, sample_features):
    result = explain_supervised_prediction(trained_pipeline.supervised_model, sample_features, top_n=100)
    assert len(result.contributions) == len(FEATURE_NAMES)


# --------------------------------------------------------------------
# Missing / invalid feature values (never silently defaulted to zero)
# --------------------------------------------------------------------

def test_missing_required_feature_returns_error_not_silent_zero(trained_pipeline, sample_features):
    incomplete = dict(sample_features)
    removed_feature = next(iter(incomplete))
    del incomplete[removed_feature]

    result = explain_supervised_prediction(trained_pipeline.supervised_model, incomplete)
    assert result.status == SHAPExplanationStatus.ERROR
    assert removed_feature in result.detail
    assert result.contributions == []


def test_multiple_missing_features_all_named_in_error(trained_pipeline, sample_features):
    incomplete = dict(sample_features)
    removed = list(incomplete.keys())[:2]
    for name in removed:
        del incomplete[name]

    result = explain_supervised_prediction(trained_pipeline.supervised_model, incomplete)
    assert result.status == SHAPExplanationStatus.ERROR
    for name in removed:
        assert name in result.detail


def test_nan_feature_value_rejected(trained_pipeline, sample_features):
    bad = dict(sample_features)
    target = next(iter(bad))
    bad[target] = float("nan")

    result = explain_supervised_prediction(trained_pipeline.supervised_model, bad)
    assert result.status == SHAPExplanationStatus.ERROR
    assert target in result.detail
    assert result.contributions == []


def test_positive_infinity_feature_value_rejected(trained_pipeline, sample_features):
    bad = dict(sample_features)
    target = next(iter(bad))
    bad[target] = float("inf")

    result = explain_supervised_prediction(trained_pipeline.supervised_model, bad)
    assert result.status == SHAPExplanationStatus.ERROR
    assert target in result.detail


def test_negative_infinity_feature_value_rejected(trained_pipeline, sample_features):
    bad = dict(sample_features)
    target = next(iter(bad))
    bad[target] = float("-inf")

    result = explain_supervised_prediction(trained_pipeline.supervised_model, bad)
    assert result.status == SHAPExplanationStatus.ERROR


def test_non_numeric_feature_value_rejected(trained_pipeline, sample_features):
    bad = dict(sample_features)
    target = next(iter(bad))
    bad[target] = "not-a-number"

    result = explain_supervised_prediction(trained_pipeline.supervised_model, bad)
    assert result.status == SHAPExplanationStatus.ERROR


# --------------------------------------------------------------------
# Additivity consistency check (runtime-enforced, not just documented)
# --------------------------------------------------------------------

def test_additivity_check_passes_for_genuine_prediction(trained_pipeline, sample_features):
    """Sanity check that the real, correct computation is always within tolerance (not accidentally too strict)."""
    result = explain_supervised_prediction(trained_pipeline.supervised_model, sample_features)
    assert result.status == SHAPExplanationStatus.AVAILABLE
    assert abs(result.predicted_probability - result.reconstructed_probability) <= 1e-4


def test_additivity_failure_returns_error_and_withholds_contributions(trained_pipeline, sample_features, monkeypatch):
    """
    Deterministically forces a mismatch: the SHAP contribution/base-value
    computation is mocked to reconstruct probability 0.5 (all-zero
    contributions, base_value=0.0 -> sigmoid(0.0)=0.5), while predict_proba()
    is mocked to return 0.9. 0.9 vs 0.5 differs by 0.4, far beyond
    ADDITIVITY_TOLERANCE (1e-4), so the additivity check is guaranteed to
    fail on every run -- not conditional on real model output.
    """
    import app.ml.shap_explainer as shap_explainer_module

    monkeypatch.setattr(trained_pipeline.supervised_model, "predict_proba", lambda features: 0.9)

    def _fake_lightgbm(model, row):
        contributions = [0.0] * len(model.feature_names)  # sum = 0.0
        base_value = 0.0                                   # sigmoid(0.0 + 0.0) = 0.5
        return contributions, base_value

    monkeypatch.setattr(shap_explainer_module, "_explain_lightgbm", _fake_lightgbm)

    result = explain_supervised_prediction(trained_pipeline.supervised_model, sample_features)

    assert result.status == SHAPExplanationStatus.ERROR
    assert result.contributions == []
    assert "additivity" in result.detail.lower()


def test_additivity_tolerance_is_a_documented_module_constant():
    assert isinstance(ADDITIVITY_TOLERANCE, float)
    assert ADDITIVITY_TOLERANCE > 0


# --------------------------------------------------------------------
# Numerically stable sigmoid: extreme finite inputs
# --------------------------------------------------------------------

def test_sigmoid_large_positive_input_does_not_overflow():
    from app.ml.shap_explainer import _sigmoid
    assert _sigmoid(1000.0) == 1.0


def test_sigmoid_large_negative_input_does_not_overflow():
    from app.ml.shap_explainer import _sigmoid
    assert _sigmoid(-1000.0) == 0.0


def test_sigmoid_extreme_inputs_near_float_exp_overflow_boundary():
    """math.exp overflows for arguments beyond ~709.78; the stable sigmoid must not raise here."""
    from app.ml.shap_explainer import _sigmoid
    assert _sigmoid(800.0) == 1.0
    assert _sigmoid(-800.0) == 0.0


def test_sigmoid_zero_is_one_half():
    from app.ml.shap_explainer import _sigmoid
    assert _sigmoid(0.0) == pytest.approx(0.5)


def test_sigmoid_matches_naive_formula_for_moderate_inputs():
    """For inputs where the naive formula doesn't overflow, the stable version must agree with it exactly."""
    import math
    from app.ml.shap_explainer import _sigmoid
    for x in (-10.0, -1.0, -0.001, 0.5, 3.7, 50.0):
        naive = 1.0 / (1.0 + math.exp(-x))
        assert _sigmoid(x) == pytest.approx(naive, rel=1e-12)


def test_sigmoid_is_symmetric_around_zero():
    from app.ml.shap_explainer import _sigmoid
    for x in (0.3, 5.0, 100.0):
        assert _sigmoid(x) + _sigmoid(-x) == pytest.approx(1.0)


# --------------------------------------------------------------------
# Non-finite explanation outputs are rejected, never fabricated as AVAILABLE
# --------------------------------------------------------------------

def test_non_finite_predicted_probability_returns_error(trained_pipeline, sample_features, monkeypatch):
    monkeypatch.setattr(trained_pipeline.supervised_model, "predict_proba", lambda features: float("nan"))
    result = explain_supervised_prediction(trained_pipeline.supervised_model, sample_features)
    assert result.status == SHAPExplanationStatus.ERROR
    assert result.contributions == []
    assert "non-finite" in result.detail.lower()


def test_non_finite_base_value_returns_error(trained_pipeline, sample_features, monkeypatch):
    import app.ml.shap_explainer as shap_explainer_module

    def _fake_lightgbm(model, row):
        contributions = [0.1] * len(model.feature_names)
        return contributions, float("inf")  # non-finite base value

    monkeypatch.setattr(shap_explainer_module, "_explain_lightgbm", _fake_lightgbm)
    result = explain_supervised_prediction(trained_pipeline.supervised_model, sample_features)
    assert result.status == SHAPExplanationStatus.ERROR
    assert result.contributions == []
    assert "non-finite" in result.detail.lower()


def test_non_finite_feature_contribution_returns_error(trained_pipeline, sample_features, monkeypatch):
    import app.ml.shap_explainer as shap_explainer_module

    def _fake_lightgbm(model, row):
        contributions = [0.1] * len(model.feature_names)
        contributions[0] = float("nan")
        return contributions, 0.0

    monkeypatch.setattr(shap_explainer_module, "_explain_lightgbm", _fake_lightgbm)
    result = explain_supervised_prediction(trained_pipeline.supervised_model, sample_features)
    assert result.status == SHAPExplanationStatus.ERROR
    assert result.contributions == []
    assert "non-finite" in result.detail.lower()


def test_non_finite_values_never_pass_through_additivity_check_as_available(trained_pipeline, sample_features, monkeypatch):
    """
    Regression guard for the specific bug class this correction fixes: a
    NaN reconstructed_probability must be caught by the explicit finiteness
    check, not silently slip past `abs(nan - x) > TOLERANCE` (which is
    always False for NaN) and be reported as a falsely-verified AVAILABLE.
    """
    import app.ml.shap_explainer as shap_explainer_module

    def _fake_lightgbm(model, row):
        # sum(contributions) + base_value will be NaN -> reconstructed_probability is NaN.
        contributions = [float("nan")] + [0.0] * (len(model.feature_names) - 1)
        return contributions, 0.0

    monkeypatch.setattr(shap_explainer_module, "_explain_lightgbm", _fake_lightgbm)
    result = explain_supervised_prediction(trained_pipeline.supervised_model, sample_features)
    assert result.status != SHAPExplanationStatus.AVAILABLE
    assert result.status == SHAPExplanationStatus.ERROR
    assert result.contributions == []


# --------------------------------------------------------------------
# Feature ordering preserved; reconstructed probability uses full vector
# --------------------------------------------------------------------

def test_contribution_order_in_output_matches_sorted_not_model_order(trained_pipeline, sample_features):
    """Returned contributions are sorted by magnitude for display, but each one's value must match its own feature."""
    result = explain_supervised_prediction(trained_pipeline.supervised_model, sample_features, top_n=100)
    for c in result.contributions:
        assert c.feature_value == pytest.approx(sample_features[c.feature])


def test_feature_values_passed_to_model_preserve_model_feature_name_order(trained_pipeline, sample_features):
    """The row built internally must follow model.feature_names order exactly, not dict insertion order."""
    captured_rows = []
    original_predict = trained_pipeline.supervised_model.estimator.predict

    def spy_predict(row, pred_contrib=False):
        if pred_contrib:
            captured_rows.append(row)
        return original_predict(row, pred_contrib=pred_contrib)

    import unittest.mock
    with unittest.mock.patch.object(trained_pipeline.supervised_model.estimator, "predict", side_effect=spy_predict):
        explain_supervised_prediction(trained_pipeline.supervised_model, sample_features)

    assert len(captured_rows) == 1
    expected_order = [sample_features[name] for name in trained_pipeline.supervised_model.feature_names]
    assert captured_rows[0][0] == expected_order


def test_reconstructed_probability_uses_full_vector_not_just_top_n(trained_pipeline, sample_features):
    """Reconstructed probability must be identical regardless of top_n, since it's computed from the full vector."""
    full = explain_supervised_prediction(trained_pipeline.supervised_model, sample_features, top_n=100)
    truncated = explain_supervised_prediction(trained_pipeline.supervised_model, sample_features, top_n=1)
    assert full.reconstructed_probability == truncated.reconstructed_probability
    assert len(truncated.contributions) == 1
    assert len(full.contributions) == len(FEATURE_NAMES)


# --------------------------------------------------------------------
# top_n edge cases
# --------------------------------------------------------------------

def test_top_n_zero_returns_no_contributions_but_still_available(trained_pipeline, sample_features):
    result = explain_supervised_prediction(trained_pipeline.supervised_model, sample_features, top_n=0)
    assert result.status == SHAPExplanationStatus.AVAILABLE
    assert result.contributions == []
    assert result.predicted_probability is not None  # the prediction info is still reported


def test_top_n_larger_than_feature_count_returns_all_features(trained_pipeline, sample_features):
    result = explain_supervised_prediction(trained_pipeline.supervised_model, sample_features, top_n=9999)
    assert len(result.contributions) == len(FEATURE_NAMES)


def test_top_n_negative_treated_as_zero(trained_pipeline, sample_features):
    result = explain_supervised_prediction(trained_pipeline.supervised_model, sample_features, top_n=-5)
    assert result.contributions == []
    assert result.status == SHAPExplanationStatus.AVAILABLE


# --------------------------------------------------------------------
# Both supported model paths exercised for real (not mocked-unavailable)
# --------------------------------------------------------------------

def test_lightgbm_path_produces_available_explanation(trained_pipeline, sample_features):
    assert trained_pipeline.supervised_model.model_type == "lightgbm"
    result = explain_supervised_prediction(trained_pipeline.supervised_model, sample_features)
    assert result.status == SHAPExplanationStatus.AVAILABLE
    assert result.model_type == "lightgbm"


def test_sklearn_gradient_boosting_path_produces_available_explanation():
    """
    SupervisedFraudModel is inference-only and model_type is normally
    auto-selected by LightGBM availability -- to genuinely exercise the
    sklearn fallback path (not a mock), a real GradientBoostingClassifier
    is fitted directly and wrapped the same way train_supervised_model()
    would if LightGBM were unavailable.
    """
    from sklearn.ensemble import GradientBoostingClassifier
    from app.ml.supervised_model import SupervisedFraudModel
    from app.fraud.dataset import DatasetConfig, build_dataset
    from app.fraud.feature_engineering import FEATURE_NAMES as _FN

    dataset = build_dataset(DatasetConfig(seed=55, n_legitimate=40, n_fraud=20))
    X = [[row[name] for name in _FN] for row in dataset.features]
    estimator = GradientBoostingClassifier(n_estimators=50, max_depth=3, random_state=42)
    estimator.fit(X, dataset.labels)

    sklearn_model = SupervisedFraudModel(estimator=estimator, model_type="sklearn_gradient_boosting")

    result = explain_supervised_prediction(sklearn_model, dataset.features[0])
    assert result.status == SHAPExplanationStatus.AVAILABLE
    assert result.model_type == "sklearn_gradient_boosting"
    assert abs(result.predicted_probability - result.reconstructed_probability) <= ADDITIVITY_TOLERANCE


# --------------------------------------------------------------------
# Missing dependency / unsupported model handling
# --------------------------------------------------------------------

def test_unsupported_model_type_reports_clear_status(sample_features):
    class _FakeUnsupportedModel:
        model_type = "some_future_model_type"
        feature_names = list(FEATURE_NAMES)

        def predict_proba(self, features):
            return 0.5

    result = explain_supervised_prediction(_FakeUnsupportedModel(), sample_features)
    assert result.status == SHAPExplanationStatus.UNSUPPORTED_MODEL
    assert result.contributions == []  # never fabricated


def test_shap_library_unavailable_for_sklearn_fallback_is_handled_gracefully(monkeypatch, sample_features):
    """Simulates shap not being installed for the sklearn fallback path -- must not crash, must report clearly."""
    import app.ml.shap_explainer as shap_explainer_module

    class _FakeSklearnModel:
        model_type = "sklearn_gradient_boosting"
        feature_names = list(FEATURE_NAMES)

        def predict_proba(self, features):
            return 0.3

    monkeypatch.setattr(shap_explainer_module, "_SHAP_LIBRARY_AVAILABLE", False)
    result = explain_supervised_prediction(_FakeSklearnModel(), sample_features)
    assert result.status == SHAPExplanationStatus.SHAP_LIBRARY_UNAVAILABLE
    assert result.contributions == []


def test_shap_computation_failure_reports_error_status_not_fabricated_values(trained_pipeline, sample_features, monkeypatch):
    """Forces the LightGBM native pred_contrib path to raise -- must degrade to ERROR, not invent numbers."""
    def _boom(*args, **kwargs):
        raise RuntimeError("simulated SHAP computation failure")

    monkeypatch.setattr(trained_pipeline.supervised_model.estimator, "predict", _boom)
    result = explain_supervised_prediction(trained_pipeline.supervised_model, sample_features)
    assert result.status == SHAPExplanationStatus.ERROR
    assert result.contributions == []
    assert "simulated SHAP computation failure" in result.detail


# --------------------------------------------------------------------
# JSON serialization
# --------------------------------------------------------------------

def test_explanation_to_dict_is_json_serializable(trained_pipeline, sample_features):
    result = explain_supervised_prediction(trained_pipeline.supervised_model, sample_features)
    serialized = json.dumps(result.to_dict())
    parsed = json.loads(serialized)
    assert parsed["status"] == "AVAILABLE"
    assert len(parsed["contributions"]) == len(result.contributions)


def test_unavailable_result_to_dict_is_also_json_serializable():
    class _FakeUnsupportedModel:
        model_type = "unknown_type"
        feature_names = list(FEATURE_NAMES)

        def predict_proba(self, features):
            return 0.5

    result = explain_supervised_prediction(_FakeUnsupportedModel(), {})
    json.dumps(result.to_dict())  # must not raise


# --------------------------------------------------------------------
# Pipeline integration: three clearly distinguished sections
# --------------------------------------------------------------------

def test_pipeline_explain_returns_three_distinct_sections(trained_pipeline, generator):
    record = generator.generate_fraud_invoice(FraudType.MATH_MISMATCH)
    result = trained_pipeline.explain_supervised_prediction(record)

    assert set(result.keys()) == {"model_prediction", "fraud_score", "shap_explanation"}
    assert "predicted_label" in result["model_prediction"]
    assert "predicted_probability" in result["model_prediction"]
    assert "final_score" in result["fraud_score"]
    assert "risk_category" in result["fraud_score"]
    assert "status" in result["shap_explanation"]


def test_pipeline_explain_model_prediction_matches_shap_predicted_probability(trained_pipeline, generator):
    record = generator.generate_legitimate_invoice()
    result = trained_pipeline.explain_supervised_prediction(record)
    assert result["model_prediction"]["predicted_probability"] == pytest.approx(
        result["shap_explanation"]["predicted_probability"]
    )


def test_pipeline_explain_fraud_score_matches_analyze_output(trained_pipeline, generator):
    """The fraud_score section must be identical to what .analyze() alone would return -- not recomputed differently."""
    record = generator.generate_legitimate_invoice()
    direct_analysis = trained_pipeline.analyze(record)
    explained = trained_pipeline.explain_supervised_prediction(record)

    assert explained["fraud_score"]["final_score"] == direct_analysis.final_score
    assert explained["fraud_score"]["risk_category"] == direct_analysis.risk_category.value


def test_pipeline_explain_is_json_serializable(trained_pipeline, generator):
    record = generator.generate_fraud_invoice(FraudType.TAX_MISMATCH)
    result = trained_pipeline.explain_supervised_prediction(record)
    json.dumps(result)


def test_pipeline_explain_before_training_raises():
    pipeline = FraudAnalysisPipeline()
    gen = SyntheticInvoiceGenerator(GeneratorConfig(seed=501))
    record = gen.generate_legitimate_invoice()
    with pytest.raises(RuntimeError):
        pipeline.explain_supervised_prediction(record)


# --------------------------------------------------------------------
# Regression: existing fraud scoring untouched by SHAP integration
# --------------------------------------------------------------------

def test_existing_analyze_method_unaffected_by_shap_addition(trained_pipeline, generator):
    """Calling analyze() must behave identically to before -- same type, same fields, no SHAP leakage into it."""
    record = generator.generate_legitimate_invoice()
    result = trained_pipeline.analyze(record)

    assert hasattr(result, "final_score")
    assert hasattr(result, "risk_category")
    assert hasattr(result, "supervised_score")
    assert hasattr(result, "anomaly_score")
    assert hasattr(result, "mathematical_score")
    assert hasattr(result, "breakdown")
    assert not hasattr(result, "shap_explanation")  # SHAP must not have leaked into the existing result type


def test_explain_does_not_change_the_fitted_model():
    """Calling explain_supervised_prediction repeatedly must not mutate/refit the model (determinism check)."""
    pipeline = FraudAnalysisPipeline()
    pipeline.train(dataset_config=DatasetConfig(seed=777, n_legitimate=30, n_fraud=15))
    gen = SyntheticInvoiceGenerator(GeneratorConfig(seed=502))
    record = gen.generate_fraud_invoice(FraudType.MATH_MISMATCH)

    first = pipeline.explain_supervised_prediction(record)
    second = pipeline.explain_supervised_prediction(record)

    assert first["model_prediction"]["predicted_probability"] == second["model_prediction"]["predicted_probability"]
    assert first["shap_explanation"]["contributions"] == second["shap_explanation"]["contributions"]
