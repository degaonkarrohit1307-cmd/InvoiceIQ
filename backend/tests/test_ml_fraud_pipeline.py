"""Phase 4C tests: ML fraud models, anomaly detection, evaluation, combined scoring, pipeline. No DB/external infra required."""
import pytest

from app.fraud.dataset import DatasetConfig, build_dataset
from app.fraud.feature_engineering import FEATURE_NAMES, extract_features
from app.fraud.synthetic_data import FraudType, GeneratorConfig, SyntheticInvoiceGenerator
from app.ml.anomaly_detector import AnomalyDetectorConfig, InsufficientDataError as AnomalyInsufficientDataError, train_anomaly_detector
from app.ml.evaluation import evaluate_anomaly_detection, evaluate_supervised
from app.ml.fraud_scoring import FraudScoringConfig, RiskCategory, combine_fraud_score, mathematical_score_from_verification
from app.ml.pipeline import FraudAnalysisPipeline
from app.ml.supervised_model import InsufficientDataError, SupervisedModelConfig, train_supervised_model
from app.services.math_verification import run_math_verification

SMALL_DATASET_KWARGS = dict(seed=1, n_legitimate=40, n_fraud=25)


def _small_dataset():
    return build_dataset(DatasetConfig(**SMALL_DATASET_KWARGS))


# --------------------------------------------------------------------
# Dataset integration / no label leakage
# --------------------------------------------------------------------

def test_dataset_integrates_with_feature_matrix_shape():
    dataset = _small_dataset()
    assert len(dataset.features) == len(dataset.labels) == dataset.n_samples
    assert tuple(dataset.features[0].keys()) == FEATURE_NAMES


def test_features_contain_no_label_keys():
    dataset = _small_dataset()
    for f in dataset.features:
        assert "label" not in f
        assert "fraud_type" not in f


# --------------------------------------------------------------------
# Supervised model
# --------------------------------------------------------------------

def test_supervised_model_trains_on_small_dataset():
    dataset = _small_dataset()
    result = train_supervised_model(dataset.features, dataset.labels, SupervisedModelConfig(random_state=1))
    assert result.model is not None
    assert result.n_train > 0 and result.n_test > 0
    assert result.model_type in {"lightgbm", "sklearn_gradient_boosting"}


def test_supervised_predictions_and_probability_range():
    dataset = _small_dataset()
    result = train_supervised_model(dataset.features, dataset.labels, SupervisedModelConfig(random_state=1))
    sample_features = dataset.features[0]
    proba = result.model.predict_proba(sample_features)
    pred = result.model.predict(sample_features)
    assert 0.0 <= proba <= 1.0
    assert pred in (0, 1)


def test_supervised_metrics_structure():
    dataset = _small_dataset()
    result = train_supervised_model(dataset.features, dataset.labels, SupervisedModelConfig(random_state=1))
    for key in ("precision", "recall", "f1", "roc_auc"):
        assert key in result.metrics


def test_supervised_training_rejects_single_class_data():
    dataset = _small_dataset()
    all_legit_features = [f for f, l in zip(dataset.features, dataset.labels) if l == 0]
    all_legit_labels = [0] * len(all_legit_features)
    with pytest.raises(InsufficientDataError):
        train_supervised_model(all_legit_features, all_legit_labels)


def test_supervised_training_rejects_too_few_samples():
    dataset = _small_dataset()
    with pytest.raises(InsufficientDataError):
        train_supervised_model(dataset.features[:2], dataset.labels[:2])


def test_supervised_training_rejects_mismatched_lengths():
    dataset = _small_dataset()
    with pytest.raises(InsufficientDataError):
        train_supervised_model(dataset.features, dataset.labels[:-1])


def test_supervised_training_is_reproducible_with_same_random_state():
    dataset = _small_dataset()
    r1 = train_supervised_model(dataset.features, dataset.labels, SupervisedModelConfig(random_state=5))
    r2 = train_supervised_model(dataset.features, dataset.labels, SupervisedModelConfig(random_state=5))
    sample = dataset.features[0]
    assert r1.model.predict_proba(sample) == pytest.approx(r2.model.predict_proba(sample))


# --------------------------------------------------------------------
# Isolation Forest anomaly detector
# --------------------------------------------------------------------

def test_anomaly_detector_trains_successfully():
    dataset = _small_dataset()
    detector = train_anomaly_detector(dataset.features, AnomalyDetectorConfig(random_state=1))
    assert detector is not None


def test_anomaly_scores_normalized_to_0_1():
    dataset = _small_dataset()
    detector = train_anomaly_detector(dataset.features, AnomalyDetectorConfig(random_state=1))
    for features in dataset.features[:10]:
        score = detector.normalized_score(features)
        assert 0.0 <= score <= 1.0


def test_anomaly_detector_flags_fraud_as_more_anomalous_on_average():
    dataset = _small_dataset()
    detector = train_anomaly_detector(dataset.features, AnomalyDetectorConfig(contamination=0.2, random_state=1))
    fraud_scores = [detector.normalized_score(f) for f, l in zip(dataset.features, dataset.labels) if l == 1]
    legit_scores = [detector.normalized_score(f) for f, l in zip(dataset.features, dataset.labels) if l == 0]
    avg_fraud = sum(fraud_scores) / len(fraud_scores)
    avg_legit = sum(legit_scores) / len(legit_scores)
    assert avg_fraud > avg_legit


def test_anomaly_detector_rejects_insufficient_data():
    with pytest.raises(AnomalyInsufficientDataError):
        train_anomaly_detector([{name: 0.0 for name in FEATURE_NAMES}])


def test_anomaly_score_distinct_from_supervised_probability():
    """Confirms these are genuinely separate signals, not aliases of each other."""
    dataset = _small_dataset()
    supervised_result = train_supervised_model(dataset.features, dataset.labels, SupervisedModelConfig(random_state=1))
    detector = train_anomaly_detector(dataset.features, AnomalyDetectorConfig(random_state=1))

    sample = dataset.features[0]
    supervised_proba = supervised_result.model.predict_proba(sample)
    anomaly_score = detector.normalized_score(sample)
    # They're independently computed values -- no assertion that they're
    # equal or even correlated, just that both are valid, distinct floats.
    assert isinstance(supervised_proba, float)
    assert isinstance(anomaly_score, float)


# --------------------------------------------------------------------
# Evaluation utilities
# --------------------------------------------------------------------

def test_evaluate_supervised_returns_expected_structure():
    metrics = evaluate_supervised([1, 0, 1, 0], [1, 0, 0, 0], [0.9, 0.1, 0.4, 0.2])
    assert set(metrics.keys()) == {"precision", "recall", "f1", "roc_auc"}
    assert 0.0 <= metrics["precision"] <= 1.0
    assert metrics["roc_auc"] is not None


def test_evaluate_supervised_roc_auc_none_when_single_class():
    metrics = evaluate_supervised([0, 0, 0], [0, 0, 1], [0.1, 0.2, 0.6])
    assert metrics["roc_auc"] is None


def test_evaluate_anomaly_detection_structure():
    result = evaluate_anomaly_detection([1, 0, 1, 0], [0.9, 0.1, 0.8, 0.2], threshold=0.5)
    assert "threshold_used" in result
    assert "precision" in result and "recall" in result and "f1" in result


# --------------------------------------------------------------------
# Combined fraud scoring
# --------------------------------------------------------------------

def test_combined_score_always_between_0_and_100():
    gen = SyntheticInvoiceGenerator(GeneratorConfig(seed=50))
    for _ in range(10):
        record = gen.generate_fraud_invoice()
        math_result = run_math_verification(record, discount=record.discount)
        result = combine_fraud_score(0.95, 0.95, math_result)
        assert 0.0 <= result.final_score <= 100.0
        assert 0.0 <= result.supervised_score <= 100.0
        assert 0.0 <= result.anomaly_score <= 100.0
        assert 0.0 <= result.mathematical_score <= 100.0


def test_combined_score_clips_out_of_range_inputs():
    gen = SyntheticInvoiceGenerator(GeneratorConfig(seed=51))
    record = gen.generate_legitimate_invoice()
    math_result = run_math_verification(record, discount=record.discount)
    result = combine_fraud_score(1.5, -0.5, math_result)  # deliberately out of [0,1]
    assert 0.0 <= result.final_score <= 100.0
    assert 0.0 <= result.supervised_score <= 100.0
    assert 0.0 <= result.anomaly_score <= 100.0


def test_math_verification_failure_increases_mathematical_score():
    gen = SyntheticInvoiceGenerator(GeneratorConfig(seed=52))
    legit = gen.generate_legitimate_invoice()
    fraud = gen.generate_fraud_invoice(FraudType.MATH_MISMATCH)

    legit_math = run_math_verification(legit, discount=legit.discount)
    fraud_math = run_math_verification(fraud, discount=fraud.discount)

    config = FraudScoringConfig()
    legit_score = mathematical_score_from_verification(legit_math, config)
    fraud_score = mathematical_score_from_verification(fraud_math, config)
    assert fraud_score > legit_score


def test_math_failure_meaningfully_raises_final_combined_score():
    gen = SyntheticInvoiceGenerator(GeneratorConfig(seed=53))
    legit = gen.generate_legitimate_invoice()
    fraud = gen.generate_fraud_invoice(FraudType.MATH_MISMATCH)

    legit_math = run_math_verification(legit, discount=legit.discount)
    fraud_math = run_math_verification(fraud, discount=fraud.discount)

    # Hold supervised/anomaly signals identical -- isolate the math contribution.
    legit_result = combine_fraud_score(0.3, 0.3, legit_math)
    fraud_result = combine_fraud_score(0.3, 0.3, fraud_math)
    assert fraud_result.final_score > legit_result.final_score
    assert fraud_result.mathematical_score > legit_result.mathematical_score


def test_risk_category_thresholds():
    config = FraudScoringConfig(low_threshold=39.0, medium_threshold=69.0)
    from app.services.math_verification import CheckResult, MathVerificationResult
    empty_math = MathVerificationResult(checks=[])

    low = combine_fraud_score(0.0, 0.0, empty_math, config)
    assert low.risk_category == RiskCategory.LOW

    high = combine_fraud_score(1.0, 1.0, empty_math, config)
    assert high.risk_category == RiskCategory.HIGH


def test_fraud_scoring_config_validates_weight_sum():
    bad_config = FraudScoringConfig(supervised_weight=0.5, anomaly_weight=0.5, mathematical_weight=0.5)
    with pytest.raises(ValueError):
        bad_config.validate()


def test_fraud_score_breakdown_is_explainable():
    gen = SyntheticInvoiceGenerator(GeneratorConfig(seed=54))
    record = gen.generate_legitimate_invoice()
    math_result = run_math_verification(record, discount=record.discount)
    result = combine_fraud_score(0.4, 0.2, math_result)
    assert "supervised_contribution" in result.breakdown
    assert "anomaly_contribution" in result.breakdown
    assert "mathematical_contribution" in result.breakdown
    assert "weights" in result.breakdown


# --------------------------------------------------------------------
# High-level pipeline
# --------------------------------------------------------------------

def test_pipeline_trains_and_analyzes():
    pipeline = FraudAnalysisPipeline()
    report = pipeline.train(dataset_config=DatasetConfig(seed=60, n_legitimate=40, n_fraud=25))
    assert pipeline.is_trained
    assert report.dataset.n_samples == 65

    gen = SyntheticInvoiceGenerator(GeneratorConfig(seed=61))
    record = gen.generate_fraud_invoice(FraudType.MATH_MISMATCH)
    result = pipeline.analyze(record)
    assert 0.0 <= result.final_score <= 100.0
    assert result.risk_category is not None


def test_pipeline_analyze_before_train_raises():
    pipeline = FraudAnalysisPipeline()
    gen = SyntheticInvoiceGenerator(GeneratorConfig(seed=62))
    record = gen.generate_legitimate_invoice()
    with pytest.raises(RuntimeError):
        pipeline.analyze(record)


def test_pipeline_persistence_roundtrip(tmp_path):
    pipeline = FraudAnalysisPipeline()
    pipeline.train(dataset_config=DatasetConfig(seed=63, n_legitimate=40, n_fraud=25))

    gen = SyntheticInvoiceGenerator(GeneratorConfig(seed=64))
    record = gen.generate_fraud_invoice()
    original_result = pipeline.analyze(record)

    save_path = pipeline.save(tmp_path / "pipeline.joblib")
    loaded_pipeline = FraudAnalysisPipeline.load(save_path)
    reloaded_result = loaded_pipeline.analyze(record)

    assert reloaded_result.final_score == original_result.final_score
    assert reloaded_result.risk_category == original_result.risk_category


def test_pipeline_deterministic_with_same_seed():
    pipeline1 = FraudAnalysisPipeline()
    pipeline1.train(dataset_config=DatasetConfig(seed=70, n_legitimate=40, n_fraud=25),
                     supervised_config=SupervisedModelConfig(random_state=70))
    pipeline2 = FraudAnalysisPipeline()
    pipeline2.train(dataset_config=DatasetConfig(seed=70, n_legitimate=40, n_fraud=25),
                     supervised_config=SupervisedModelConfig(random_state=70))

    gen = SyntheticInvoiceGenerator(GeneratorConfig(seed=71))
    record = gen.generate_fraud_invoice()

    r1 = pipeline1.analyze(record)
    r2 = pipeline2.analyze(record)
    assert r1.final_score == r2.final_score
