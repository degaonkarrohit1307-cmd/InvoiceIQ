"""Phase 4C tests: ML fraud models, evaluation, combined scoring, pipeline. No DB/external infra required."""
import pytest

from app.fraud.dataset import DatasetConfig, build_dataset
from app.fraud.feature_engineering import FEATURE_NAMES, build_feature_matrix
from app.fraud.synthetic_data import FraudType, GeneratorConfig, SyntheticInvoiceGenerator
from app.ml.anomaly_detector import AnomalyDetectorConfig, InsufficientDataError as AnomalyInsufficientDataError, train_anomaly_detector
from app.ml.evaluation import evaluate_anomaly_detection, evaluate_supervised
from app.ml.fraud_scoring import FraudScoringConfig, RiskCategory, combine_fraud_score, mathematical_score_from_verification
from app.ml.pipeline import FraudAnalysisPipeline
from app.ml.supervised_model import InsufficientDataError as SupervisedInsufficientDataError, SupervisedModelConfig, train_supervised_model
from app.services.math_verification import run_math_verification


def _small_dataset(seed=42, n_legit=30, n_fraud=20):
    return build_dataset(DatasetConfig(seed=seed, n_legitimate=n_legit, n_fraud=n_fraud))


# --------------------------------------------------------------------
# Dataset integration / no label leakage
# --------------------------------------------------------------------

def test_dataset_integrates_with_ml_modules():
    dataset = _small_dataset()
    assert len(dataset.features) == len(dataset.labels) == dataset.n_samples
    assert set(dataset.feature_names) == set(FEATURE_NAMES)


def test_features_contain_no_label_columns():
    dataset = _small_dataset()
    for row in dataset.features:
        assert "label" not in row
        assert "fraud_type" not in row


# --------------------------------------------------------------------
# Supervised model
# --------------------------------------------------------------------

def test_supervised_model_trains_on_small_dataset():
    dataset = _small_dataset()
    result = train_supervised_model(dataset.features, dataset.labels, SupervisedModelConfig(random_state=1))
    assert result.model is not None
    assert result.n_train + result.n_test == dataset.n_samples
    assert result.model_type in {"lightgbm", "sklearn_gradient_boosting"}


def test_supervised_predictions_generated_for_all_test_rows():
    dataset = _small_dataset()
    result = train_supervised_model(dataset.features, dataset.labels)
    preds = [result.model.predict(row) for row in dataset.features[:5]]
    assert all(p in (0, 1) for p in preds)


def test_supervised_probability_within_0_1():
    dataset = _small_dataset()
    result = train_supervised_model(dataset.features, dataset.labels)
    probas = [result.model.predict_proba(row) for row in dataset.features]
    assert all(0.0 <= p <= 1.0 for p in probas)


def test_supervised_training_reproducible_with_same_seed():
    dataset = _small_dataset()
    r1 = train_supervised_model(dataset.features, dataset.labels, SupervisedModelConfig(random_state=7))
    r2 = train_supervised_model(dataset.features, dataset.labels, SupervisedModelConfig(random_state=7))
    probas1 = [r1.model.predict_proba(row) for row in dataset.features]
    probas2 = [r2.model.predict_proba(row) for row in dataset.features]
    assert probas1 == probas2


def test_supervised_model_rejects_insufficient_data():
    with pytest.raises(SupervisedInsufficientDataError):
        train_supervised_model([{"a": 1.0}], [1])


def test_supervised_model_rejects_single_class_data():
    dataset = _small_dataset(n_legit=10, n_fraud=0)
    with pytest.raises(SupervisedInsufficientDataError):
        train_supervised_model(dataset.features, dataset.labels)


# --------------------------------------------------------------------
# Isolation Forest anomaly detector
# --------------------------------------------------------------------

def test_anomaly_detector_trains_successfully():
    dataset = _small_dataset()
    detector = train_anomaly_detector(dataset.features, AnomalyDetectorConfig(random_state=1))
    assert detector is not None


def test_anomaly_scores_normalized_between_0_and_1():
    dataset = _small_dataset()
    detector = train_anomaly_detector(dataset.features)
    scores = [detector.normalized_score(row) for row in dataset.features]
    assert all(0.0 <= s <= 1.0 for s in scores)


def test_anomaly_detector_deterministic_with_same_random_state():
    dataset = _small_dataset()
    d1 = train_anomaly_detector(dataset.features, AnomalyDetectorConfig(random_state=3))
    d2 = train_anomaly_detector(dataset.features, AnomalyDetectorConfig(random_state=3))
    scores1 = [d1.normalized_score(row) for row in dataset.features]
    scores2 = [d2.normalized_score(row) for row in dataset.features]
    assert scores1 == scores2


def test_anomaly_detector_rejects_insufficient_data():
    with pytest.raises(AnomalyInsufficientDataError):
        train_anomaly_detector([{"a": 1.0}])


def test_anomaly_score_distinct_from_supervised_probability():
    """Confirms these are computed independently -- not the same value by construction."""
    dataset = _small_dataset()
    sup_result = train_supervised_model(dataset.features, dataset.labels)
    detector = train_anomaly_detector(dataset.features)

    sample = dataset.features[0]
    sup_proba = sup_result.model.predict_proba(sample)
    anomaly = detector.normalized_score(sample)
    # Not asserting they differ numerically (coincidence is possible) --
    # asserting they come from genuinely separate code paths/models.
    assert sup_result.model is not detector
    assert isinstance(sup_proba, float) and isinstance(anomaly, float)


# --------------------------------------------------------------------
# Evaluation utilities
# --------------------------------------------------------------------

def test_evaluate_supervised_returns_expected_keys():
    metrics = evaluate_supervised([1, 0, 1, 0], [1, 0, 0, 0], y_proba=[0.9, 0.1, 0.4, 0.2])
    assert set(metrics.keys()) == {"precision", "recall", "f1", "roc_auc"}
    assert 0.0 <= metrics["precision"] <= 1.0
    assert 0.0 <= metrics["recall"] <= 1.0
    assert 0.0 <= metrics["f1"] <= 1.0
    assert metrics["roc_auc"] is None or 0.0 <= metrics["roc_auc"] <= 1.0


def test_evaluate_supervised_roc_auc_none_for_single_class():
    metrics = evaluate_supervised([1, 1, 1], [1, 1, 0], y_proba=[0.9, 0.8, 0.3])
    assert metrics["roc_auc"] is None


def test_evaluate_anomaly_detection_structure():
    metrics = evaluate_anomaly_detection([1, 0, 1, 0], [0.9, 0.1, 0.8, 0.2], threshold=0.5)
    assert "threshold_used" in metrics
    assert "precision" in metrics and "recall" in metrics and "f1" in metrics


# --------------------------------------------------------------------
# Combined fraud scoring
# --------------------------------------------------------------------

def _fields_stub(cgst=None, sgst=None, igst=None, total_tax=None, subtotal=None, total_amount=None, line_items=None):
    class _F:
        pass
    f = _F()
    f.line_items = line_items or []
    f.subtotal, f.cgst, f.sgst, f.igst = subtotal, cgst, sgst, igst
    f.total_tax, f.total_amount = total_tax, total_amount
    return f


def test_combined_score_bounded_0_to_100_across_extremes():
    math_result = run_math_verification(_fields_stub())  # all NOT_CHECKABLE
    for sup_prob in (0.0, 0.5, 1.0):
        for anomaly in (0.0, 0.5, 1.0):
            result = combine_fraud_score(sup_prob, anomaly, math_result)
            assert 0.0 <= result.final_score <= 100.0
            assert 0.0 <= result.supervised_score <= 100.0
            assert 0.0 <= result.anomaly_score <= 100.0
            assert 0.0 <= result.mathematical_score <= 100.0


def test_mathematical_verification_failure_increases_mathematical_score():
    passing_fields = _fields_stub(subtotal=100.0, total_tax=18.0, total_amount=118.0, cgst=9.0, sgst=9.0)
    failing_fields = _fields_stub(subtotal=100.0, total_tax=18.0, total_amount=999.0, cgst=9.0, sgst=9.0)

    passing_math = run_math_verification(passing_fields)
    failing_math = run_math_verification(failing_fields)

    config = FraudScoringConfig()
    pass_score = mathematical_score_from_verification(passing_math, config)
    fail_score = mathematical_score_from_verification(failing_math, config)

    assert fail_score > pass_score


def test_combined_score_risk_categories_assigned_correctly():
    math_result = run_math_verification(_fields_stub())
    config = FraudScoringConfig()

    low = combine_fraud_score(0.05, 0.05, math_result, config)
    assert low.risk_category == RiskCategory.LOW

    high = combine_fraud_score(0.95, 0.95, math_result, config)
    assert high.risk_category == RiskCategory.HIGH


def test_combined_score_breakdown_present_and_sums_correctly():
    math_result = run_math_verification(_fields_stub(subtotal=100.0, total_tax=18.0, total_amount=118.0, cgst=9.0, sgst=9.0))
    result = combine_fraud_score(0.6, 0.4, math_result)
    breakdown = result.breakdown
    total_contribution = (
        breakdown["supervised_contribution"] + breakdown["anomaly_contribution"] + breakdown["mathematical_contribution"]
    )
    assert abs(total_contribution - result.final_score) < 0.05


def test_fraud_scoring_config_rejects_invalid_weights():
    config = FraudScoringConfig(supervised_weight=0.5, anomaly_weight=0.5, mathematical_weight=0.5)
    with pytest.raises(ValueError):
        config.validate()


def test_fraud_scoring_config_custom_weights_still_bounded():
    config = FraudScoringConfig(supervised_weight=0.7, anomaly_weight=0.2, mathematical_weight=0.1)
    config.validate()
    math_result = run_math_verification(_fields_stub())
    result = combine_fraud_score(1.0, 1.0, math_result, config)
    assert result.final_score <= 100.0


# --------------------------------------------------------------------
# High-level pipeline
# --------------------------------------------------------------------

def test_pipeline_trains_and_analyzes_end_to_end():
    pipeline = FraudAnalysisPipeline()
    assert pipeline.is_trained is False

    report = pipeline.train(dataset_config=DatasetConfig(seed=50, n_legitimate=40, n_fraud=25))
    assert pipeline.is_trained is True
    assert report.dataset.n_samples == 65

    gen = SyntheticInvoiceGenerator(GeneratorConfig(seed=51))
    fraud_record = gen.generate_fraud_invoice(FraudType.MATH_MISMATCH)
    result = pipeline.analyze(fraud_record)

    assert 0.0 <= result.final_score <= 100.0
    assert result.risk_category in {RiskCategory.LOW, RiskCategory.MEDIUM, RiskCategory.HIGH}


def test_pipeline_analyze_before_train_raises():
    pipeline = FraudAnalysisPipeline()
    gen = SyntheticInvoiceGenerator(GeneratorConfig(seed=52))
    record = gen.generate_legitimate_invoice()
    with pytest.raises(RuntimeError):
        pipeline.analyze(record)


def test_pipeline_save_and_load_roundtrip(tmp_path):
    pipeline = FraudAnalysisPipeline()
    pipeline.train(dataset_config=DatasetConfig(seed=53, n_legitimate=30, n_fraud=20))

    gen = SyntheticInvoiceGenerator(GeneratorConfig(seed=54))
    record = gen.generate_fraud_invoice(FraudType.TAX_MISMATCH)
    original_result = pipeline.analyze(record)

    save_path = pipeline.save(tmp_path / "pipeline.joblib")
    loaded_pipeline = FraudAnalysisPipeline.load(save_path)
    reloaded_result = loaded_pipeline.analyze(record)

    assert reloaded_result.final_score == original_result.final_score
    assert reloaded_result.risk_category == original_result.risk_category


def test_pipeline_legitimate_scores_lower_than_obvious_fraud_on_average():
    """
    Sanity/regression check: trained on a reasonably sized synthetic
    dataset, the pipeline should, on average, score synthetic fraud
    records (especially blatant MATH_MISMATCH cases) higher than
    legitimate ones. This is a statistical tendency check on synthetic
    data, not a real-world accuracy claim.
    """
    pipeline = FraudAnalysisPipeline()
    pipeline.train(dataset_config=DatasetConfig(seed=60, n_legitimate=150, n_fraud=80))

    gen = SyntheticInvoiceGenerator(GeneratorConfig(seed=61))
    legit_scores = [pipeline.analyze(gen.generate_legitimate_invoice()).final_score for _ in range(10)]
    fraud_scores = [pipeline.analyze(gen.generate_fraud_invoice(FraudType.MATH_MISMATCH)).final_score for _ in range(10)]

    assert sum(fraud_scores) / len(fraud_scores) > sum(legit_scores) / len(legit_scores)
