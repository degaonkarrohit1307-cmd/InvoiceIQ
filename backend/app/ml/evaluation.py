"""
Model evaluation utilities (Phase 4C).

ALL metrics computed here are evaluated against SYNTHETIC data (see
app.fraud.dataset / app.fraud.synthetic_data) and reflect only how well a
model fits that synthetic dataset's generation rules -- they are NOT a
claim about real-world fraud detection accuracy.
"""
from __future__ import annotations

from sklearn.metrics import f1_score, precision_score, recall_score, roc_auc_score


def evaluate_supervised(
    y_true: list[int], y_pred: list[int], y_proba: list[float] | None = None,
) -> dict[str, float | None]:
    """
    Precision/recall/F1 always computed (zero_division=0 avoids a crash on
    an edge case with no positive predictions). ROC-AUC requires both
    classes present in y_true; returns None (not a fabricated 0.5 or 0.0)
    when it can't be validly computed.
    """
    metrics: dict[str, float | None] = {
        "precision": float(precision_score(y_true, y_pred, zero_division=0)),
        "recall": float(recall_score(y_true, y_pred, zero_division=0)),
        "f1": float(f1_score(y_true, y_pred, zero_division=0)),
        "roc_auc": None,
    }
    if y_proba is not None and len(set(y_true)) > 1:
        metrics["roc_auc"] = float(roc_auc_score(y_true, y_proba))
    return metrics


def evaluate_anomaly_detection(
    y_true_fraud_labels: list[int], anomaly_scores: list[float], threshold: float = 0.5,
) -> dict[str, float | None]:
    """
    Evaluates the Isolation Forest's normalized anomaly scores AGAINST the
    synthetic fraud labels, purely as a sanity signal for development --
    the detector itself never sees these labels during training. Treats
    anomaly_score >= threshold as a "predicted fraud" for this evaluation.

    This is evaluation-against-synthetic-labels only; it is not how the
    detector is meant to be used at inference time (it has no labels then).
    """
    predicted = [1 if score >= threshold else 0 for score in anomaly_scores]
    return {
        "threshold_used": threshold,
        **evaluate_supervised(y_true_fraud_labels, predicted, anomaly_scores),
    }
