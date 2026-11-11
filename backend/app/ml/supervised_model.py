"""
Supervised fraud classification (Phase 4C).

Preferred model: LightGBM (LGBMClassifier). If LightGBM is unavailable in
the runtime environment, falls back to scikit-learn's
GradientBoostingClassifier -- this is an HONEST fallback, not a fake
implementation: it's a real, different model, clearly labeled as such via
`SupervisedFraudModel.model_type`.

Training/inference are deliberately separate: `train_supervised_model()`
does the fitting + evaluation; `SupervisedFraudModel.predict_proba()` is
pure inference on an already-fitted model.

IMPORTANT: all training/evaluation here uses SYNTHETIC data from
app.fraud.dataset. Metrics reported are performance on that synthetic
dataset only and make no claim about real-world fraud detection accuracy.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sklearn.model_selection import train_test_split

from app.fraud.feature_engineering import FEATURE_NAMES

try:
    from lightgbm import LGBMClassifier
    _LIGHTGBM_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised only when lightgbm truly isn't installed
    from sklearn.ensemble import GradientBoostingClassifier
    _LIGHTGBM_AVAILABLE = False


class InsufficientDataError(ValueError):
    """Raised when there isn't enough data (or only one class) to train/evaluate a supervised model."""


@dataclass
class SupervisedModelConfig:
    random_state: int = 42
    test_size: float = 0.25
    n_estimators: int = 100
    max_depth: int = 4
    learning_rate: float = 0.1
    class_weight_balanced: bool = True  # handles class imbalance (fraud is typically the minority class)


@dataclass
class SupervisedTrainingResult:
    model: "SupervisedFraudModel"
    metrics: dict[str, float | None]
    n_train: int
    n_test: int
    model_type: str


def _rows_to_matrix(features: list[dict[str, float]]) -> list[list[float]]:
    """Converts a list of feature dicts into a list of rows, in FEATURE_NAMES order -- never relying on dict ordering alone."""
    return [[row[name] for name in FEATURE_NAMES] for row in features]


def _build_estimator(config: SupervisedModelConfig):
    if _LIGHTGBM_AVAILABLE:
        return LGBMClassifier(
            n_estimators=config.n_estimators,
            max_depth=config.max_depth,
            learning_rate=config.learning_rate,
            random_state=config.random_state,
            class_weight="balanced" if config.class_weight_balanced else None,
            verbosity=-1,
        ), "lightgbm"
    else:
        from sklearn.ensemble import GradientBoostingClassifier
        # GradientBoostingClassifier has no native class_weight; sample_weight
        # is applied manually in train_supervised_model() when requested.
        return GradientBoostingClassifier(
            n_estimators=config.n_estimators,
            max_depth=config.max_depth,
            learning_rate=config.learning_rate,
            random_state=config.random_state,
        ), "sklearn_gradient_boosting"


@dataclass
class SupervisedFraudModel:
    """A fitted supervised model + the feature order it expects. Inference only -- no training logic here."""
    estimator: Any
    model_type: str
    feature_names: tuple[str, ...] = field(default_factory=lambda: FEATURE_NAMES)

    def _row(self, features: dict[str, float]) -> list[list[float]]:
        return [[features.get(name, 0.0) for name in self.feature_names]]

    def predict_proba(self, features: dict[str, float]) -> float:
        """Returns the predicted probability of fraud (class 1), in [0, 1]."""
        proba = self.estimator.predict_proba(self._row(features))[0]
        # class order is estimator.classes_; find index of class 1 defensively
        classes = list(self.estimator.classes_)
        idx = classes.index(1) if 1 in classes else -1
        return float(proba[idx])

    def predict(self, features: dict[str, float]) -> int:
        return int(self.estimator.predict(self._row(features))[0])


def train_supervised_model(
    features: list[dict[str, float]],
    labels: list[int],
    config: SupervisedModelConfig | None = None,
) -> SupervisedTrainingResult:
    """
    Trains a supervised fraud classifier on (features, labels).

    `features` and `labels` MUST already be separate (this function never
    reads a "label" key out of a feature dict) -- callers should get both
    from `app.fraud.feature_engineering.build_feature_matrix`, which
    enforces that separation structurally.
    """
    config = config or SupervisedModelConfig()

    if len(features) != len(labels):
        raise InsufficientDataError("features and labels must be the same length.")
    if len(features) < 4:
        raise InsufficientDataError("Need at least 4 samples to train and evaluate a supervised model.")
    if len(set(labels)) < 2:
        raise InsufficientDataError("Training data must contain both fraud and legitimate examples (only one class present).")

    X = _rows_to_matrix(features)
    y = list(labels)

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=config.test_size, random_state=config.random_state,
        stratify=y if len(set(y)) > 1 else None,
    )

    estimator, model_type = _build_estimator(config)

    if model_type == "sklearn_gradient_boosting" and config.class_weight_balanced:
        # Manual class balancing for the fallback model (no native class_weight support).
        from collections import Counter
        counts = Counter(y_train)
        total = len(y_train)
        sample_weight = [total / (len(counts) * counts[label]) for label in y_train]
        estimator.fit(X_train, y_train, sample_weight=sample_weight)
    else:
        estimator.fit(X_train, y_train)

    model = SupervisedFraudModel(estimator=estimator, model_type=model_type)

    from app.ml.evaluation import evaluate_supervised
    y_pred = [model.predict(dict(zip(FEATURE_NAMES, row))) for row in X_test]
    y_proba = [model.predict_proba(dict(zip(FEATURE_NAMES, row))) for row in X_test]
    metrics = evaluate_supervised(y_test, y_pred, y_proba)

    return SupervisedTrainingResult(
        model=model, metrics=metrics, n_train=len(X_train), n_test=len(X_test), model_type=model_type,
    )
