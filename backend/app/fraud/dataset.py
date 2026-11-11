"""
Dataset preparation pipeline (Phase 4B).

Ties the synthetic generator and feature engineering together into a
single, deterministic, ML-ready dataset. This is SYNTHETIC data for
development/training purposes -- it does not represent real invoices or
real-world fraud statistics (see synthetic_data.py for the same note).
"""
from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path

from app.fraud.feature_engineering import FEATURE_NAMES, build_feature_matrix
from app.fraud.synthetic_data import FraudType, GeneratorConfig, SyntheticInvoiceGenerator, SyntheticInvoiceRecord


@dataclass
class DatasetConfig:
    seed: int = 42
    n_legitimate: int = 200
    n_fraud: int = 50
    fraud_type_distribution: list[FraudType] | None = None
    generator_config: GeneratorConfig | None = None


@dataclass
class DatasetResult:
    records: list[SyntheticInvoiceRecord]
    features: list[dict[str, float]]
    labels: list[int]
    feature_names: tuple[str, ...] = field(default_factory=lambda: FEATURE_NAMES)

    @property
    def n_samples(self) -> int:
        return len(self.records)

    @property
    def n_fraud(self) -> int:
        return sum(self.labels)

    @property
    def n_legitimate(self) -> int:
        return self.n_samples - self.n_fraud

    def to_csv(self, path: str | Path) -> Path:
        """
        Exports features + label (+ fraud_type for auditability, clearly
        NOT a feature -- see the fieldnames ordering/labeling below) to CSV.
        """
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)

        fieldnames = list(self.feature_names) + ["label", "fraud_type"]
        with path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for feat, label, record in zip(self.features, self.labels, self.records):
                row = dict(feat)
                row["label"] = label
                row["fraud_type"] = record.fraud_type or ""
                writer.writerow(row)
        return path


def build_dataset(config: DatasetConfig | None = None) -> DatasetResult:
    """
    Deterministic for a fixed DatasetConfig (same seed, counts, and fraud
    type distribution always produce the same records, features, and
    labels -- see SyntheticInvoiceGenerator for the reproducibility
    mechanism).
    """
    config = config or DatasetConfig()
    generator_config = config.generator_config or GeneratorConfig(seed=config.seed)
    generator = SyntheticInvoiceGenerator(generator_config)

    records = generator.generate_dataset(
        n_legitimate=config.n_legitimate,
        n_fraud=config.n_fraud,
        fraud_type_distribution=config.fraud_type_distribution,
    )

    features, labels = build_feature_matrix(records)

    return DatasetResult(records=records, features=features, labels=labels)
