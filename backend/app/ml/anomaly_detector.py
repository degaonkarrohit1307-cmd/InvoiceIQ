"""
Unsupervised anomaly detection via Isolation Forest (Phase 4C).

Distinct from the supervised fraud model: this does NOT use fraud labels
at all during training (that's the point of an unsupervised approach) --
it only learns what "normal" feature distributions look like and flags
outliers. `anomaly_score` here is explicitly separate from
`supervised_model.predict_proba()`'s fraud probability; the two are only
combined later, in `fraud_scoring.py`.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from sklearn.ensemble import IsolationForest

from app.fraud.feature_engineering import FEATURE_NAMES


class InsufficientDataError(ValueError):
    """Raised when there isn't enough data to fit an Isolation Forest meaningfully."""


@dataclass
class AnomalyDetectorConfig:
    contamination: float = 0.1  # expected proportion of anomalies in training data
    n_estimators: int = 100
    random_state: int = 42


@dataclass
class IsolationForestDetector:
    """
    Wraps sklearn's IsolationForest with min/max score bounds captured at
    fit time, so `normalized_score()` can map raw decision_function output
    (unbounded, higher = more normal) into a consistent [0, 1] range where
    1.0 = most anomalous. This is a min-max normalization over the TRAINING
    set's score distribution, not a universal constant -- a score outside
    the training range will be clipped to [0, 1] rather than extrapolated.
    """
    estimator: IsolationForest
    feature_names: tuple[str, ...] = field(default_factory=lambda: FEATURE_NAMES)
    _train_score_min: float = 0.0
    _train_score_max: float = 1.0

    def _row(self, features: dict[str, float]) -> list[list[float]]:
        return [[features.get(name, 0.0) for name in self.feature_names]]

    def raw_score(self, features: dict[str, float]) -> float:
        """sklearn's decision_function: higher = more normal, lower/negative = more anomalous."""
        return float(self.estimator.decision_function(self._row(features))[0])

    def normalized_score(self, features: dict[str, float]) -> float:
        """
        Returns a value in [0, 1] where 1.0 = most anomalous, 0.0 = most
        normal (relative to the training data's score distribution) --
        i.e. the raw decision_function is inverted and min-max scaled.
        """
        raw = self.raw_score(features)
        span = self._train_score_max - self._train_score_min
        if span <= 0:
            return 0.0
        normalized_normalcy = (raw - self._train_score_min) / span
        normalized_normalcy = max(0.0, min(1.0, normalized_normalcy))
        return 1.0 - normalized_normalcy

    def predict_is_anomaly(self, features: dict[str, float]) -> bool:
        """True if IsolationForest's own binary decision flags this as an outlier (-1)."""
        return int(self.estimator.predict(self._row(features))[0]) == -1


def train_anomaly_detector(
    features: list[dict[str, float]],
    config: AnomalyDetectorConfig | None = None,
) -> IsolationForestDetector:
    """
    Trains on FEATURES ONLY -- no labels are used or accepted, by design
    (this is the unsupervised counterpart to supervised_model.py).
    """
    config = config or AnomalyDetectorConfig()

    if len(features) < 4:
        raise InsufficientDataError("Need at least 4 samples to fit an Isolation Forest meaningfully.")

    rows = [[row[name] for name in FEATURE_NAMES] for row in features]

    estimator = IsolationForest(
        n_estimators=config.n_estimators,
        contamination=config.contamination,
        random_state=config.random_state,
    )
    estimator.fit(rows)

    train_scores = estimator.decision_function(rows)
    detector = IsolationForestDetector(
        estimator=estimator,
        _train_score_min=float(min(train_scores)),
        _train_score_max=float(max(train_scores)),
    )
    return detector
