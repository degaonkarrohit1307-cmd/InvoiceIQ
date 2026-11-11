"""
Phase 5 focused tests: LangGraph invoice verification/fraud orchestration.

No DB/external infra required -- these test the graph's construction and
execution against an in-memory FraudAnalysisPipeline trained on the Phase
4B synthetic dataset.
"""
import pytest

from app.fraud.dataset import DatasetConfig
from app.fraud.synthetic_data import FraudType, GeneratorConfig, SyntheticInvoiceGenerator
from app.ml.pipeline import FraudAnalysisPipeline
from app.orchestration.graph import build_invoice_verification_graph, run_invoice_verification
from app.orchestration.nodes import (
    aggregate_decision_node,
    mathematical_verification_node,
    validate_invoice_node,
)


@pytest.fixture(scope="module")
def trained_pipeline():
    pipeline = FraudAnalysisPipeline()
    pipeline.train(dataset_config=DatasetConfig(seed=42, n_legitimate=80, n_fraud=40))
    return pipeline


@pytest.fixture(scope="module")
def generator():
    return SyntheticInvoiceGenerator(GeneratorConfig(seed=100))


class _EmptyRecord:
    subtotal = None
    total_amount = None
    line_items = []


class _MissingSubtotalRecord:
    subtotal = None
    total_amount = 100.0
    line_items = [1]  # any non-empty list; only truthiness matters to validate_invoice_node


# --------------------------------------------------------------------
# 1. Graph construction
# --------------------------------------------------------------------

def test_graph_builds_and_compiles(trained_pipeline):
    app = build_invoice_verification_graph(trained_pipeline)
    assert app is not None
    # A compiled LangGraph exposes .invoke -- confirms this is a real, runnable graph.
    assert hasattr(app, "invoke")


def test_graph_construction_does_not_require_retraining(trained_pipeline):
    """Building the graph twice from the same pipeline must not mutate or retrain it."""
    app1 = build_invoice_verification_graph(trained_pipeline)
    app2 = build_invoice_verification_graph(trained_pipeline)
    assert app1 is not app2  # separate compiled graphs
    assert trained_pipeline.is_trained  # still the same trained pipeline, untouched


# --------------------------------------------------------------------
# 2 & 3. Successful execution / valid invoice flow
# --------------------------------------------------------------------

def test_valid_legitimate_invoice_completes_successfully(trained_pipeline, generator):
    record = generator.generate_legitimate_invoice()
    result = run_invoice_verification(trained_pipeline, record)

    assert result["verification_status"] == "COMPLETED"
    assert result["error"] is None
    assert result["math_verification"] is not None
    assert result["fraud_score"] is not None
    assert result["fraud_risk_category"] in {"LOW", "MEDIUM", "HIGH"}


def test_valid_invoice_math_verification_passes(trained_pipeline, generator):
    record = generator.generate_legitimate_invoice()
    result = run_invoice_verification(trained_pipeline, record)
    assert result["math_verification"]["overall"] == "PASS"


# --------------------------------------------------------------------
# 4. Mathematical-verification failure flow
# --------------------------------------------------------------------

def test_math_mismatch_fraud_flags_failed_math_check(trained_pipeline, generator):
    record = generator.generate_fraud_invoice(FraudType.MATH_MISMATCH)
    result = run_invoice_verification(trained_pipeline, record)

    assert result["verification_status"] == "COMPLETED"
    assert result["math_verification"]["overall"] == "FAIL"
    failed_rules = [c["rule"] for c in result["math_verification"]["checks"] if c["result"] == "FAIL"]
    assert "subtotal_plus_tax_equals_grand_total" in failed_rules
    assert any("Math check failed" in s for s in result["key_signals"])


def test_line_item_inconsistency_fraud_flags_line_item_check(trained_pipeline, generator):
    record = generator.generate_fraud_invoice(FraudType.LINE_ITEM_INCONSISTENCY)
    result = run_invoice_verification(trained_pipeline, record)
    failed_rules = [c["rule"] for c in result["math_verification"]["checks"] if c["result"] == "FAIL"]
    assert any(rule.startswith("line_item_") for rule in failed_rules)


# --------------------------------------------------------------------
# 5. Suspicious/fraud invoice flow (ML side)
# --------------------------------------------------------------------

def test_fraud_invoice_scores_higher_than_legitimate_on_average(trained_pipeline, generator):
    legit_scores = [
        run_invoice_verification(trained_pipeline, generator.generate_legitimate_invoice())["fraud_score"]
        for _ in range(8)
    ]
    fraud_scores = [
        run_invoice_verification(trained_pipeline, generator.generate_fraud_invoice(FraudType.MATH_MISMATCH))["fraud_score"]
        for _ in range(8)
    ]
    assert sum(fraud_scores) / len(fraud_scores) > sum(legit_scores) / len(legit_scores)


def test_fraud_result_includes_risk_category_and_signals(trained_pipeline, generator):
    record = generator.generate_fraud_invoice(FraudType.TAX_MISMATCH)
    result = run_invoice_verification(trained_pipeline, record)
    assert result["fraud_risk_category"] in {"LOW", "MEDIUM", "HIGH"}
    assert isinstance(result["key_signals"], list)
    assert len(result["key_signals"]) > 0


# --------------------------------------------------------------------
# 6. State propagation between nodes
# --------------------------------------------------------------------

def test_state_propagates_math_result_into_validate_then_verify(generator):
    """Runs the underlying node functions directly (not the compiled graph) to inspect intermediate state."""
    record = generator.generate_legitimate_invoice()
    state = {"invoice_fields": record, "discount": None}

    state = validate_invoice_node(state)
    assert state["is_valid"] is True
    assert "math_result" not in state  # not yet computed

    state = mathematical_verification_node(state)
    assert state["math_result"] is not None
    assert state["invoice_fields"] is record  # original input preserved through the chain

    state = aggregate_decision_node(state)
    # fraud_result was never set (fraud node skipped in this direct-node test) -> still produces a final_result
    assert state["final_result"]["math_verification"] is not None
    assert state["final_result"]["fraud_score"] is None  # honestly None, not fabricated


def test_state_carries_discount_through_to_math_node():
    class _Fields:
        subtotal = 100.0
        total_tax = 18.0
        total_amount = 108.0
        cgst = 9.0
        sgst = 9.0
        igst = None
        line_items = [1]

    state = {"invoice_fields": _Fields(), "discount": 10.0}
    state = validate_invoice_node(state)
    state = mathematical_verification_node(state)

    grand_total_check = next(c for c in state["math_result"].checks if c.rule == "subtotal_plus_tax_equals_grand_total")
    assert grand_total_check.result.value == "PASS"  # 100 + 18 - 10 = 108, matches total_amount


# --------------------------------------------------------------------
# 7. Final structured result shape
# --------------------------------------------------------------------

def test_final_result_has_all_required_keys(trained_pipeline, generator):
    record = generator.generate_legitimate_invoice()
    result = run_invoice_verification(trained_pipeline, record)
    required_keys = {
        "verification_status", "error", "math_verification",
        "fraud_score", "fraud_risk_category", "key_signals",
    }
    assert required_keys.issubset(result.keys())


def test_math_verification_checks_are_json_serializable_shape(trained_pipeline, generator):
    import json

    record = generator.generate_legitimate_invoice()
    result = run_invoice_verification(trained_pipeline, record)
    # Should not raise -- confirms the final_result is plain dicts/lists/primitives, not custom objects.
    json.dumps(result)


# --------------------------------------------------------------------
# 8. Invalid input handling
# --------------------------------------------------------------------

def test_none_invoice_fields_produces_invalid_input_status(trained_pipeline):
    result = run_invoice_verification(trained_pipeline, None)
    assert result["verification_status"] == "INVALID_INPUT"
    assert "No invoice fields were provided." in result["validation_errors"]
    assert result["fraud_score"] is None
    assert result["math_verification"] is None


def test_missing_required_field_produces_invalid_input_status(trained_pipeline):
    result = run_invoice_verification(trained_pipeline, _MissingSubtotalRecord())
    assert result["verification_status"] == "INVALID_INPUT"
    assert "Missing required field: subtotal" in result["validation_errors"]


def test_empty_line_items_produces_invalid_input_status(trained_pipeline):
    result = run_invoice_verification(trained_pipeline, _EmptyRecord())
    assert result["verification_status"] == "INVALID_INPUT"
    assert "Invoice has no line items." in result["validation_errors"]


def test_invalid_input_never_reaches_math_or_fraud_nodes(trained_pipeline):
    """Regression test for the bug found during Phase 5A smoke-testing: invalid input must short-circuit."""
    result = run_invoice_verification(trained_pipeline, None)
    assert result["verification_status"] == "INVALID_INPUT"  # not "ERROR" -- no exception should have been raised
    assert result["error"] is None


# --------------------------------------------------------------------
# 9. Error handling (genuine exceptions, distinct from invalid input)
# --------------------------------------------------------------------

def test_fraud_node_exception_produces_error_status_not_crash(generator):
    """Uses an untrained pipeline (analyze() raises RuntimeError) to verify the graph catches it gracefully."""
    untrained_pipeline = FraudAnalysisPipeline()
    record = generator.generate_legitimate_invoice()

    result = run_invoice_verification(untrained_pipeline, record)

    assert result["verification_status"] == "ERROR"
    assert result["failed_node"] == "fraud_analysis_node"
    assert result["error"] is not None
    assert result["fraud_score"] is None


def test_error_result_still_has_all_required_keys(generator):
    untrained_pipeline = FraudAnalysisPipeline()
    record = generator.generate_legitimate_invoice()
    result = run_invoice_verification(untrained_pipeline, record)
    required_keys = {
        "verification_status", "error", "math_verification",
        "fraud_score", "fraud_risk_category", "key_signals",
    }
    assert required_keys.issubset(result.keys())


def test_error_in_math_node_short_circuits_fraud_node(trained_pipeline, monkeypatch):
    """Forces math verification to raise, confirming fraud_analysis_node never runs afterward."""
    import app.orchestration.nodes as nodes_module

    def _boom(fields, discount=None):
        raise ValueError("simulated math verification failure")

    monkeypatch.setattr(nodes_module, "run_math_verification", _boom)

    class _Fields:
        subtotal = 100.0
        total_amount = 100.0
        line_items = [1]

    result = run_invoice_verification(trained_pipeline, _Fields())
    assert result["verification_status"] == "ERROR"
    assert result["failed_node"] == "mathematical_verification_node"
    assert result["fraud_score"] is None  # fraud node never ran
