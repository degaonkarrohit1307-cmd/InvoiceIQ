"""
High-level fraud analysis pipeline (Phase 4C).

This is the single entry point later phases (e.g. Phase 5) should call --
it hides the individual supervised/anomaly/math-verification/scoring
components behind one `FraudAnalysisPipeline.analyze(record)` call.

Training uses the Phase 4B synthetic dataset via app.fraud.dataset;
inference (`analyze`) works on any object with the same attribute shape
(SyntheticInvoiceRecord now, a real InvoiceExtraction later).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import joblib

from app.fraud.dataset import DatasetConfig, DatasetResult, build_dataset
from app.fraud.feature_engineering import extract_features
from app.ml.anomaly_detector import AnomalyDetectorConfig, IsolationForestDetector, train_anomaly_detector
from app.ml.fraud_scoring import FraudScoreResult, FraudScoringConfig, combine_fraud_score
from app.ml.shap_explainer import SHAPExplanationResult, explain_supervised_prediction
from app.ml.supervised_model import SupervisedFraudModel, SupervisedModelConfig, SupervisedTrainingResult, train_supervised_model
from app.services.math_verification import run_math_verification


@dataclass
class PipelineTrainingReport:
    supervised: SupervisedTrainingResult
    dataset: DatasetResult


class FraudAnalysisPipeline:
    """Holds a fitted supervised model + anomaly detector + scoring config. Not fitted until `.train()` or `.load()`."""

    def __init__(
        self,
        supervised_model: SupervisedFraudModel | None = None,
        anomaly_detector: IsolationForestDetector | None = None,
        scoring_config: FraudScoringConfig | None = None,
    ):
        self.supervised_model = supervised_model
        self.anomaly_detector = anomaly_detector
        self.scoring_config = scoring_config or FraudScoringConfig()

    @property
    def is_trained(self) -> bool:
        return self.supervised_model is not None and self.anomaly_detector is not None

    def train(
        self,
        dataset_config: DatasetConfig | None = None,
        supervised_config: SupervisedModelConfig | None = None,
        anomaly_config: AnomalyDetectorConfig | None = None,
    ) -> PipelineTrainingReport:
        """Trains both models on a freshly generated Phase 4B synthetic dataset. Deterministic for a fixed dataset_config."""
        dataset = build_dataset(dataset_config)

        supervised_result = train_supervised_model(dataset.features, dataset.labels, supervised_config)
        self.supervised_model = supervised_result.model

        self.anomaly_detector = train_anomaly_detector(dataset.features, anomaly_config)

        return PipelineTrainingReport(supervised=supervised_result, dataset=dataset)

    def analyze(self, record: Any, discount: float | None = None) -> FraudScoreResult:
        """Feature extraction -> math verification -> supervised probability -> anomaly score -> combined score."""
        if not self.is_trained:
            raise RuntimeError("FraudAnalysisPipeline is not trained/loaded yet. Call .train() or .load() first.")

        features = extract_features(record)
        effective_discount = discount if discount is not None else getattr(record, "discount", None)
        math_result = run_math_verification(record, discount=effective_discount)

        supervised_probability = self.supervised_model.predict_proba(features)
        anomaly_score = self.anomaly_detector.normalized_score(features)

        return combine_fraud_score(supervised_probability, anomaly_score, math_result, self.scoring_config)

    def explain_supervised_prediction(self, record: Any, discount: float | None = None, top_n: int = 5) -> dict:
        """
        Phase 7: structured explanation for a single invoice, with three
        CLEARLY DISTINGUISHED sections (never merged into one number):
          - "model_prediction": the supervised model's own binary
            predict()/predict_proba() output, independent of SHAP.
          - "fraud_score": the EXISTING, UNCHANGED Phase 4C combined score
            (supervised + anomaly + math verification) and risk category
            -- calls self.analyze() as-is, does not recompute or alter it.
          - "shap_explanation": feature-level SHAP contributions for the
            supervised model's prediction (see app.ml.shap_explainer).
            NOT proof of fraud -- explains model behavior only. Its status
            is always present (AVAILABLE / SHAP_LIBRARY_UNAVAILABLE /
            UNSUPPORTED_MODEL / ERROR) so an unavailable dependency or
            unsupported configuration is reported honestly, never
            papered over with fabricated contribution values.

        Does not retrain or refit anything -- uses the already-trained
        self.supervised_model as-is.
        """
        if not self.is_trained:
            raise RuntimeError("FraudAnalysisPipeline is not trained/loaded yet. Call .train() or .load() first.")

        features = extract_features(record)
        fraud_result = self.analyze(record, discount=discount)  # unchanged existing behavior, reused as-is
        shap_result = explain_supervised_prediction(self.supervised_model, features, top_n=top_n)

        return {
            "model_prediction": {
                "predicted_label": self.supervised_model.predict(features),
                "predicted_probability": self.supervised_model.predict_proba(features),
            },
            "fraud_score": {
                "final_score": fraud_result.final_score,
                "risk_category": fraud_result.risk_category.value,
                "supervised_score": fraud_result.supervised_score,
                "anomaly_score": fraud_result.anomaly_score,
                "mathematical_score": fraud_result.mathematical_score,
            },
            "shap_explanation": shap_result.to_dict(),
        }

    # --- persistence (lightweight joblib serialization, no MLOps infra) ---

    def save(self, path: str | Path) -> Path:
        if not self.is_trained:
            raise RuntimeError("Cannot save an untrained pipeline.")
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump(
            {
                "supervised_model": self.supervised_model,
                "anomaly_detector": self.anomaly_detector,
                "scoring_config": self.scoring_config,
            },
            path,
        )
        return path

    @classmethod
    def load(cls, path: str | Path) -> "FraudAnalysisPipeline":
        data = joblib.load(Path(path))
        return cls(
            supervised_model=data["supervised_model"],
            anomaly_detector=data["anomaly_detector"],
            scoring_config=data["scoring_config"],
        )
