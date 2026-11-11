"""Phase 4B tests: synthetic fraud data generation, feature engineering, dataset prep. No DB/external infra required."""
import csv

from app.fraud.dataset import DatasetConfig, DatasetResult, build_dataset
from app.fraud.feature_engineering import FEATURE_NAMES, build_feature_matrix, extract_features
from app.fraud.synthetic_data import (
    ALL_FRAUD_TYPES,
    FraudType,
    GeneratorConfig,
    SyntheticInvoiceGenerator,
)


# --------------------------------------------------------------------
# Generator reproducibility
# --------------------------------------------------------------------

def test_same_seed_produces_identical_dataset():
    gen1 = SyntheticInvoiceGenerator(GeneratorConfig(seed=7))
    gen2 = SyntheticInvoiceGenerator(GeneratorConfig(seed=7))
    records1 = gen1.generate_dataset(n_legitimate=10, n_fraud=5)
    records2 = gen2.generate_dataset(n_legitimate=10, n_fraud=5)

    for r1, r2 in zip(records1, records2):
        assert r1.subtotal == r2.subtotal
        assert r1.total_amount == r2.total_amount
        assert r1.label == r2.label
        assert r1.fraud_type == r2.fraud_type
        assert [li.line_total for li in r1.line_items] == [li.line_total for li in r2.line_items]


def test_different_seeds_produce_variation():
    gen1 = SyntheticInvoiceGenerator(GeneratorConfig(seed=1))
    gen2 = SyntheticInvoiceGenerator(GeneratorConfig(seed=2))
    records1 = gen1.generate_dataset(n_legitimate=10, n_fraud=5)
    records2 = gen2.generate_dataset(n_legitimate=10, n_fraud=5)

    totals1 = [r.total_amount for r in records1]
    totals2 = [r.total_amount for r in records2]
    assert totals1 != totals2


def test_generator_does_not_use_global_random_state(monkeypatch):
    """Confirms the generator uses its own random.Random instance, not the shared global `random` module."""
    import random as global_random

    global_random.seed(12345)
    state_before = global_random.getstate()

    gen = SyntheticInvoiceGenerator(GeneratorConfig(seed=1))
    gen.generate_dataset(n_legitimate=5, n_fraud=5)

    assert global_random.getstate() == state_before


# --------------------------------------------------------------------
# Legitimate vs fraud record generation
# --------------------------------------------------------------------

def test_generate_dataset_produces_requested_counts():
    gen = SyntheticInvoiceGenerator(GeneratorConfig(seed=3))
    records = gen.generate_dataset(n_legitimate=15, n_fraud=8)
    assert len(records) == 23
    assert sum(1 for r in records if r.label == "legitimate") == 15
    assert sum(1 for r in records if r.label == "fraud") == 8


def test_legitimate_invoice_is_mathematically_consistent():
    gen = SyntheticInvoiceGenerator(GeneratorConfig(seed=4))
    record = gen.generate_legitimate_invoice()

    expected_subtotal = round(sum(li.line_total for li in record.line_items), 2)
    assert abs(record.subtotal - expected_subtotal) < 0.01

    expected_total = round(record.subtotal + record.total_tax - record.discount, 2)
    assert abs(record.total_amount - expected_total) < 0.02


def test_all_fraud_types_are_generatable():
    gen = SyntheticInvoiceGenerator(GeneratorConfig(seed=5))
    for fraud_type in ALL_FRAUD_TYPES:
        record = gen.generate_fraud_invoice(fraud_type)
        assert record.label == "fraud"
        assert record.fraud_type == fraud_type.value


def test_math_mismatch_fraud_actually_breaks_grand_total():
    gen = SyntheticInvoiceGenerator(GeneratorConfig(seed=6))
    record = gen.generate_fraud_invoice(FraudType.MATH_MISMATCH)
    expected = round(record.subtotal + record.total_tax - record.discount, 2)
    assert abs(record.total_amount - expected) > 0.02


def test_line_item_inconsistency_fraud_breaks_a_line_total():
    gen = SyntheticInvoiceGenerator(GeneratorConfig(seed=8))
    record = gen.generate_fraud_invoice(FraudType.LINE_ITEM_INCONSISTENCY)
    mismatches = [
        li for li in record.line_items
        if li.quantity is not None and li.unit_price is not None and li.line_total is not None
        and abs(li.quantity * li.unit_price - li.line_total) > 0.02
    ]
    assert len(mismatches) >= 1


def test_duplicate_invoice_number_fraud_reuses_a_number():
    gen = SyntheticInvoiceGenerator(GeneratorConfig(seed=9))
    gen.generate_dataset(n_legitimate=5, n_fraud=0)  # seed some invoice numbers first
    record = gen.generate_fraud_invoice(FraudType.DUPLICATE_INVOICE_NUMBER)
    assert record.is_duplicate_of is not None
    assert record.invoice_number == record.is_duplicate_of


# --------------------------------------------------------------------
# Feature engineering: label/feature separation
# --------------------------------------------------------------------

def test_extract_features_returns_exactly_expected_columns():
    gen = SyntheticInvoiceGenerator(GeneratorConfig(seed=10))
    record = gen.generate_legitimate_invoice()
    features = extract_features(record)
    assert tuple(features.keys()) == FEATURE_NAMES


def test_features_never_contain_label_or_fraud_type():
    gen = SyntheticInvoiceGenerator(GeneratorConfig(seed=11))
    record = gen.generate_fraud_invoice(FraudType.TAX_MISMATCH)
    features = extract_features(record)
    assert "label" not in features
    assert "fraud_type" not in features
    assert all(isinstance(v, float) for v in features.values())


def test_build_feature_matrix_separates_features_and_labels():
    gen = SyntheticInvoiceGenerator(GeneratorConfig(seed=12))
    records = gen.generate_dataset(n_legitimate=10, n_fraud=10)
    features, labels = build_feature_matrix(records)

    assert len(features) == len(labels) == 20
    assert all("label" not in f for f in features)
    assert set(labels) <= {0, 1}
    assert sum(labels) == 10  # exactly the 10 fraud records


def test_missing_line_items_handled_safely_not_crash():
    class _EmptyRecord:
        subtotal = None
        total_tax = None
        discount = None
        total_amount = None
        cgst = None
        sgst = None
        igst = None
        line_items = []
        is_duplicate_of = None

    features = extract_features(_EmptyRecord())
    assert features["num_line_items"] == 0.0
    assert features["quantity_mean"] == 0.0
    assert features["subtotal"] == 0.0  # None -> safe default, not a crash


def test_math_verification_derived_features_reflect_actual_inconsistency():
    gen = SyntheticInvoiceGenerator(GeneratorConfig(seed=13))
    legit = gen.generate_legitimate_invoice()
    fraud = gen.generate_fraud_invoice(FraudType.MATH_MISMATCH)

    legit_features = extract_features(legit)
    fraud_features = extract_features(fraud)

    assert legit_features["math_overall_fail_flag"] == 0.0
    assert fraud_features["math_overall_fail_flag"] == 1.0
    assert fraud_features["grand_total_diff_abs"] > legit_features["grand_total_diff_abs"]


def test_duplicate_flag_feature_reflects_generator_metadata():
    gen = SyntheticInvoiceGenerator(GeneratorConfig(seed=14))
    gen.generate_dataset(n_legitimate=5, n_fraud=0)
    dup_record = gen.generate_fraud_invoice(FraudType.DUPLICATE_INVOICE_NUMBER)
    features = extract_features(dup_record)
    assert features["is_duplicate_number_flag"] == 1.0


# --------------------------------------------------------------------
# Dataset preparation pipeline
# --------------------------------------------------------------------

def test_build_dataset_shape_consistency():
    result = build_dataset(DatasetConfig(seed=20, n_legitimate=30, n_fraud=15))
    assert isinstance(result, DatasetResult)
    assert result.n_samples == 45
    assert result.n_legitimate == 30
    assert result.n_fraud == 15
    assert len(result.records) == len(result.features) == len(result.labels) == 45


def test_build_dataset_reproducible_with_same_config():
    r1 = build_dataset(DatasetConfig(seed=21, n_legitimate=10, n_fraud=5))
    r2 = build_dataset(DatasetConfig(seed=21, n_legitimate=10, n_fraud=5))
    assert r1.features == r2.features
    assert r1.labels == r2.labels


def test_build_dataset_respects_fraud_type_distribution():
    result = build_dataset(DatasetConfig(
        seed=22, n_legitimate=0, n_fraud=4,
        fraud_type_distribution=[FraudType.MATH_MISMATCH, FraudType.TAX_MISMATCH],
    ))
    fraud_types = [r.fraud_type for r in result.records]
    assert fraud_types == ["MATH_MISMATCH", "TAX_MISMATCH", "MATH_MISMATCH", "TAX_MISMATCH"]


def test_csv_export_has_expected_columns_and_row_count(tmp_path):
    result = build_dataset(DatasetConfig(seed=23, n_legitimate=5, n_fraud=3))
    csv_path = result.to_csv(tmp_path / "dataset.csv")
    assert csv_path.exists()

    with csv_path.open() as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    assert len(rows) == 8
    assert set(FEATURE_NAMES).issubset(set(reader.fieldnames))
    assert "label" in reader.fieldnames
    assert "fraud_type" in reader.fieldnames
