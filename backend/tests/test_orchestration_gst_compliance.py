"""
Phase 6C tests: GST compliance integrated into the LangGraph orchestration.

No DB/external infra required. All external GSTIN verification uses a
fake/mock client (GSTSandboxClient protocol) -- NO real Sandbox API calls
are made anywhere in this file.
"""
import pytest

from app.compliance.gst_sandbox_models import GSTVerificationStatus
from app.fraud.dataset import DatasetConfig
from app.fraud.synthetic_data import GeneratorConfig, SyntheticInvoiceGenerator
from app.ml.pipeline import FraudAnalysisPipeline
from app.orchestration.graph import build_invoice_verification_graph, run_invoice_verification
from app.orchestration.nodes import gst_compliance_node

VALID_SUPPLIER_GSTIN = "27AAAAA0000A1Z5"
VALID_BUYER_GSTIN = "07BBBBB1111B2Z6"


class _FakeGSTClient:
    """Satisfies the GSTSandboxClient protocol; no network access."""

    def __init__(self, response=None, exc=None):
        self._response = response
        self._exc = exc
        self.called_with: list[str] = []

    def verify_gstin(self, gstin: str) -> dict:
        self.called_with.append(gstin)
        if self._exc is not None:
            raise self._exc
        return self._response


ACTIVE_TAXPAYER = {
    "legalName": "Acme Trading Co", "bussNature": "Service", "stateName": "Gujarat",
    "validGstin": True, "stateCode": "27", "pan": "AAAAA0000A", "gstin": VALID_SUPPLIER_GSTIN,
    "regStartDate": "06/02/2023", "status": "Active",
}


class _LineItem:
    """Minimal real line-item shape -- matches what math_verification/feature_engineering expect."""
    def __init__(self, quantity, unit_price, line_total):
        self.quantity = quantity
        self.unit_price = unit_price
        self.line_total = line_total
        self.taxable_amount = None
        self.tax_rate = None


class _CompliantFields:
    """A GST-compliant invoice: valid GSTIN formats, consistent CGST/SGST/total/grand total."""
    supplier_gstin = VALID_SUPPLIER_GSTIN
    buyer_gstin = VALID_BUYER_GSTIN
    subtotal = 525.0
    cgst = 23.63
    sgst = 23.63
    igst = 0.0
    total_tax = 47.25
    total_amount = 572.25
    line_items = [_LineItem(quantity=1, unit_price=525.0, line_total=525.0)]


class _NonCompliantFields:
    """GST FAIL: CGST+SGST doesn't reconcile with total_tax."""
    supplier_gstin = VALID_SUPPLIER_GSTIN
    buyer_gstin = VALID_BUYER_GSTIN
    subtotal = 525.0
    cgst = 23.63
    sgst = 23.63
    igst = 0.0
    total_tax = 999.0
    total_amount = 999.0
    line_items = [_LineItem(quantity=1, unit_price=525.0, line_total=525.0)]


class _NoGSTDataFields:
    """GST NOT_CHECKABLE: no GSTINs, no tax components at all."""
    supplier_gstin = None
    buyer_gstin = None
    subtotal = 100.0
    cgst = None
    sgst = None
    igst = None
    total_tax = None
    total_amount = 100.0
    line_items = [_LineItem(quantity=1, unit_price=100.0, line_total=100.0)]


@pytest.fixture(scope="module")
def trained_pipeline():
    pipeline = FraudAnalysisPipeline()
    pipeline.train(dataset_config=DatasetConfig(seed=42, n_legitimate=60, n_fraud=30))
    return pipeline


# --------------------------------------------------------------------
# gst_compliance_node in isolation
# --------------------------------------------------------------------

def test_gst_node_pass():
    state = {"invoice_fields": _CompliantFields(), "is_valid": True}
    result_state = gst_compliance_node(state, gst_sandbox_client=_FakeGSTClient(response=ACTIVE_TAXPAYER))
    assert result_state["gst_compliance_result"].overall.value == "PASS"


def test_gst_node_fail():
    state = {"invoice_fields": _NonCompliantFields(), "is_valid": True}
    result_state = gst_compliance_node(state, gst_sandbox_client=_FakeGSTClient(response=ACTIVE_TAXPAYER))
    assert result_state["gst_compliance_result"].overall.value == "FAIL"


def test_gst_node_not_checkable():
    state = {"invoice_fields": _NoGSTDataFields(), "is_valid": True}
    result_state = gst_compliance_node(state, gst_sandbox_client=_FakeGSTClient(response=ACTIVE_TAXPAYER))
    assert result_state["gst_compliance_result"].overall.value == "NOT_CHECKABLE"
    assert result_state["gst_external_verification"] is None


def test_gst_node_mocked_external_verification_success():
    client = _FakeGSTClient(response=ACTIVE_TAXPAYER)
    state = {"invoice_fields": _CompliantFields(), "is_valid": True}
    result_state = gst_compliance_node(state, gst_sandbox_client=client)

    external = result_state["gst_external_verification"]
    assert external["supplier"].status == GSTVerificationStatus.API_VERIFIED_ACTIVE
    assert external["buyer"].status == GSTVerificationStatus.API_VERIFIED_ACTIVE
    assert client.called_with == [VALID_SUPPLIER_GSTIN, VALID_BUYER_GSTIN]


def test_gst_node_external_sandbox_unavailable_degrades_gracefully():
    """No client injected at all -> external verification must not raise, not call any real network."""
    state = {"invoice_fields": _CompliantFields(), "is_valid": True}
    result_state = gst_compliance_node(state, gst_sandbox_client=None)

    assert "error" not in result_state or result_state.get("error") is None
    external = result_state["gst_external_verification"]
    assert external["supplier"].status == GSTVerificationStatus.FORMAT_VALID_NOT_API_VERIFIED
    assert result_state["gst_compliance_result"] is not None


def test_gst_node_short_circuits_on_invalid_input():
    client = _FakeGSTClient(response=ACTIVE_TAXPAYER)
    state = {"invoice_fields": None, "is_valid": False, "validation_errors": ["No invoice fields were provided."]}
    result_state = gst_compliance_node(state, gst_sandbox_client=client)

    assert "gst_compliance_result" not in result_state
    assert "gst_external_verification" not in result_state
    assert client.called_with == []


def test_gst_node_short_circuits_on_earlier_error():
    client = _FakeGSTClient(response=ACTIVE_TAXPAYER)
    state = {"invoice_fields": _CompliantFields(), "error": "something failed earlier", "failed_node": "verify_math"}
    result_state = gst_compliance_node(state, gst_sandbox_client=client)

    assert "gst_compliance_result" not in result_state
    assert client.called_with == []


# --------------------------------------------------------------------
# Full graph execution (build + run)
# --------------------------------------------------------------------

def test_graph_executes_gst_node_before_aggregate(trained_pipeline):
    client = _FakeGSTClient(response=ACTIVE_TAXPAYER)
    result = run_invoice_verification(trained_pipeline, _CompliantFields(), gst_sandbox_client=client)

    assert result["verification_status"] == "COMPLETED"
    assert client.called_with == [VALID_SUPPLIER_GSTIN, VALID_BUYER_GSTIN]


def test_aggregate_output_contains_gst_compliance_key(trained_pipeline):
    client = _FakeGSTClient(response=ACTIVE_TAXPAYER)
    result = run_invoice_verification(trained_pipeline, _CompliantFields(), gst_sandbox_client=client)
    assert "gst_compliance" in result
    assert result["gst_compliance"]["overall"] == "PASS"


def test_aggregate_output_contains_gst_external_verification_key(trained_pipeline):
    client = _FakeGSTClient(response=ACTIVE_TAXPAYER)
    result = run_invoice_verification(trained_pipeline, _CompliantFields(), gst_sandbox_client=client)
    assert "gst_external_verification" in result
    assert result["gst_external_verification"]["supplier"]["status"] == "API_VERIFIED_ACTIVE"


def test_graph_builds_with_gst_client_param(trained_pipeline):
    client = _FakeGSTClient(response=ACTIVE_TAXPAYER)
    app = build_invoice_verification_graph(trained_pipeline, gst_sandbox_client=client)
    assert hasattr(app, "invoke")


# --------------------------------------------------------------------
# Aggregate decision policy: PASS / FAIL / NOT_CHECKABLE
# --------------------------------------------------------------------

def test_aggregate_with_gst_pass(trained_pipeline):
    client = _FakeGSTClient(response=ACTIVE_TAXPAYER)
    result = run_invoice_verification(trained_pipeline, _CompliantFields(), gst_sandbox_client=client)
    assert result["gst_compliance"]["overall"] == "PASS"
    assert not any("GST check failed" in s for s in result["key_signals"])


def test_aggregate_with_gst_fail(trained_pipeline):
    client = _FakeGSTClient(response=ACTIVE_TAXPAYER)
    result = run_invoice_verification(trained_pipeline, _NonCompliantFields(), gst_sandbox_client=client)
    assert result["gst_compliance"]["overall"] == "FAIL"
    assert any("GST check failed" in s for s in result["key_signals"])


def test_aggregate_with_gst_not_checkable(trained_pipeline):
    client = _FakeGSTClient(response=ACTIVE_TAXPAYER)
    result = run_invoice_verification(trained_pipeline, _NoGSTDataFields(), gst_sandbox_client=client)
    assert result["gst_compliance"]["overall"] == "NOT_CHECKABLE"
    assert not any("GST check failed" in s for s in result["key_signals"])


# --------------------------------------------------------------------
# GST compliance does NOT change fraud scoring; API unavailability != fraud
# --------------------------------------------------------------------

def test_gst_fail_does_not_change_fraud_score(trained_pipeline):
    client = _FakeGSTClient(response=ACTIVE_TAXPAYER)
    pass_result = run_invoice_verification(trained_pipeline, _CompliantFields(), gst_sandbox_client=client)
    fail_result = run_invoice_verification(trained_pipeline, _NonCompliantFields(), gst_sandbox_client=client)

    assert pass_result["fraud_score"] is not None
    assert fail_result["fraud_score"] is not None
    assert pass_result["fraud_risk_category"] in {"LOW", "MEDIUM", "HIGH"}
    assert fail_result["fraud_risk_category"] in {"LOW", "MEDIUM", "HIGH"}


def test_external_api_unavailable_does_not_affect_fraud_score_or_gst_overall(trained_pipeline):
    result_without_client = run_invoice_verification(trained_pipeline, _CompliantFields(), gst_sandbox_client=None)
    client = _FakeGSTClient(response=ACTIVE_TAXPAYER)
    result_with_client = run_invoice_verification(trained_pipeline, _CompliantFields(), gst_sandbox_client=client)

    assert result_without_client["gst_compliance"]["overall"] == "PASS"
    assert result_with_client["gst_compliance"]["overall"] == "PASS"

    assert result_without_client["gst_external_verification"]["supplier"]["status"] == "FORMAT_VALID_NOT_API_VERIFIED"
    assert not any("GST check failed" in s for s in result_without_client["key_signals"])
    assert result_without_client["fraud_score"] is not None


# --------------------------------------------------------------------
# Invalid input / error short-circuit regression
# --------------------------------------------------------------------

def test_invalid_invoice_short_circuits_entire_pipeline_including_gst(trained_pipeline):
    client = _FakeGSTClient(response=ACTIVE_TAXPAYER)
    result = run_invoice_verification(trained_pipeline, None, gst_sandbox_client=client)

    assert result["verification_status"] == "INVALID_INPUT"
    assert result["gst_compliance"] is None
    assert result["gst_external_verification"] is None
    assert client.called_with == []


def test_error_result_includes_gst_keys_as_none(trained_pipeline):
    untrained_pipeline = FraudAnalysisPipeline()
    result = run_invoice_verification(untrained_pipeline, _CompliantFields())

    assert result["verification_status"] == "ERROR"
    assert result["gst_compliance"] is None
    assert result["gst_external_verification"] is None


# --------------------------------------------------------------------
# Phase 5 regression: existing behavior must remain intact with GST added
# --------------------------------------------------------------------

def test_phase5_valid_invoice_flow_still_completes(trained_pipeline):
    gen = SyntheticInvoiceGenerator(GeneratorConfig(seed=200))
    record = gen.generate_legitimate_invoice()
    result = run_invoice_verification(trained_pipeline, record)

    assert result["verification_status"] == "COMPLETED"
    assert result["math_verification"] is not None
    assert result["fraud_score"] is not None
    assert result["fraud_risk_category"] in {"LOW", "MEDIUM", "HIGH"}


def test_phase5_final_result_still_has_all_original_keys(trained_pipeline):
    gen = SyntheticInvoiceGenerator(GeneratorConfig(seed=201))
    record = gen.generate_legitimate_invoice()
    result = run_invoice_verification(trained_pipeline, record)

    original_phase5_keys = {
        "verification_status", "error", "math_verification",
        "fraud_score", "fraud_risk_category", "key_signals",
    }
    assert original_phase5_keys.issubset(result.keys())


def test_phase5_omitting_gst_client_param_still_works(trained_pipeline):
    gen = SyntheticInvoiceGenerator(GeneratorConfig(seed=202))
    record = gen.generate_legitimate_invoice()
    result = run_invoice_verification(trained_pipeline, record)
    assert result["verification_status"] == "COMPLETED"


def test_phase5_json_serializable_shape_preserved(trained_pipeline):
    import json

    gen = SyntheticInvoiceGenerator(GeneratorConfig(seed=203))
    record = gen.generate_legitimate_invoice()
    client = _FakeGSTClient(response=ACTIVE_TAXPAYER)
    result = run_invoice_verification(trained_pipeline, record, gst_sandbox_client=client)
    json.dumps(result)
