"""Service-level tests for invoice analysis orchestration and persistence."""

import uuid
from types import SimpleNamespace
from unittest.mock import Mock

from app.ml.fraud_scoring import FraudScoreResult, RiskCategory
from app.models.analysis import InvoiceAnalysis
from app.models.extraction import ExtractionStatus, InvoiceExtraction
from app.models.invoice import (
    Invoice,
    InvoiceFileType,
    InvoiceProcessingStatus,
)
from app.services.invoice_analysis import run_invoice_analysis


def _create_extraction(db_session):
    """Create and commit a valid invoice/extraction pair in PostgreSQL."""
    invoice_id = uuid.uuid4()

    invoice = Invoice(
        id=invoice_id,
        original_filename="service_test_invoice.pdf",
        stored_filename=f"{invoice_id}.pdf",
        storage_path=f"test/{invoice_id}.pdf",
        file_type=InvoiceFileType.PDF,
        mime_type="application/pdf",
        file_size=1024,
        file_hash=uuid.uuid4().hex * 2,
        processing_status=InvoiceProcessingStatus.COMPLETED,
    )

    db_session.add(invoice)
    db_session.flush()

    extraction = InvoiceExtraction(
        invoice_id=invoice.id,
        extraction_version=1,
        status=ExtractionStatus.SUCCESS,
        invoice_number="SERVICE-TEST-001",
        supplier_name="Example Supplier",
        subtotal=1000,
        total_amount=1180,
        math_verification_status="PASS",
    )

    db_session.add(extraction)
    db_session.commit()
    db_session.refresh(extraction)

    return extraction


def _fraud_result(supervised_score=1.87):
    """Return the project's actual FraudScoreResult type."""
    return FraudScoreResult(
        supervised_score=supervised_score,
        anomaly_score=38.84,
        mathematical_score=2.14,
        final_score=13.01,
        risk_category=RiskCategory.LOW,
        breakdown={
            "supervised_contribution": 0.94,
            "anomaly_contribution": 11.65,
            "mathematical_contribution": 0.43,
        },
    )


def _graph_result(fraud_result=None):
    """Build the final-result shape produced by aggregate_decision_node."""
    return {
        "fraud_result": fraud_result or _fraud_result(),
        "final_result": {
            "verification_status": "COMPLETED",
            "error": None,
            "gst_compliance": {
                "overall": "PASS",
                "checks": [],
                "warnings": [],
            },
            "gst_external_verification": {
                "supplier": {
                    "gstin": "27ABCDE1234F1Z5",
                    "status": "FORMAT_VALID_NOT_API_VERIFIED",
                    "format_valid": True,
                    "detail": "External verification was not configured.",
                    "legal_name": None,
                    "checked_at": "2026-11-04T00:00:00+00:00",
                }
            },
            "key_signals": ["Mathematical verification passed"],
            "validation_errors": [],
        },
    }


def _pipeline(explain_result=None, explain_side_effect=None):
    """Create a pipeline test double exposing the service's required attributes."""
    explain = Mock(return_value=explain_result)

    if explain_side_effect is not None:
        explain.side_effect = explain_side_effect

    return SimpleNamespace(
        supervised_model=SimpleNamespace(model_type="LightGBM"),
        explain_supervised_prediction=explain,
    )


def test_successful_analysis_and_complete_shap_persistence(db_session):
    extraction = _create_extraction(db_session)

    fraud = _fraud_result()
    graph = Mock(invoke=Mock(return_value=_graph_result(fraud)))

    prediction = {
        "predicted_label": 0,
        "predicted_probability": 0.0187,
    }

    shap_explanation = {
        "status": "AVAILABLE",
        "detail": "SHAP explanation generated and additivity check passed.",
        "model_type": "LightGBM",
        "predicted_probability": 0.0187,
        "reconstructed_probability": 0.0187,
        "base_value": -3.5,
        "contributions": [
            {
                "feature": "amount",
                "feature_value": 1180.0,
                "shap_value": 0.12,
            },
            {
                "feature": "tax_ratio",
                "feature_value": 0.18,
                "shap_value": -0.04,
            },
        ],
    }

    pipeline = _pipeline(
        explain_result={
            "model_prediction": prediction,
            "fraud_score": {
                "final_score": 13.01,
                "risk_category": "LOW",
            },
            "shap_explanation": shap_explanation,
        }
    )

    result = run_invoice_analysis(
        db_session,
        extraction,
        graph,
        pipeline,
    )

    assert result.verification_status == "COMPLETED"
    assert result.shap_status == "AVAILABLE"
    assert result.shap_explanation == shap_explanation
    assert result.model_prediction == prediction
    assert float(result.fraud_score) == 13.01
    assert result.gst_compliance_overall == "PASS"

    pipeline.explain_supervised_prediction.assert_called_once_with(extraction)

    graph.invoke.assert_called_once_with(
        {
            "invoice_fields": extraction,
            "discount": None,
        }
    )

    db_session.expire_all()

    saved = db_session.get(InvoiceAnalysis, result.id)

    assert saved is not None
    assert saved.shap_status == "AVAILABLE"
    assert saved.shap_explanation == shap_explanation
    assert saved.model_prediction == prediction

    assert saved.gst_compliance_result == {
        "overall": "PASS",
        "checks": [],
        "warnings": [],
    }

    assert (
        saved.gst_external_verification["supplier"]["status"]
        == "FORMAT_VALID_NOT_API_VERIFIED"
    )


def test_supervised_prediction_matches_graph_fraud_score_units(db_session):
    extraction = _create_extraction(db_session)

    fraud = _fraud_result(supervised_score=1.87)

    graph = Mock(
        invoke=Mock(return_value=_graph_result(fraud))
    )

    probability = 0.0187

    pipeline = _pipeline(
        explain_result={
            "model_prediction": {
                "predicted_label": 0,
                "predicted_probability": probability,
            },
            "fraud_score": {
                "final_score": 13.01,
                "risk_category": "LOW",
            },
            "shap_explanation": {
                "status": "AVAILABLE",
                "contributions": [],
            },
        }
    )

    analysis = run_invoice_analysis(
        db_session,
        extraction,
        graph,
        pipeline,
    )

    assert (
        analysis.model_prediction["predicted_probability"]
        == probability
    )

    # Probability uses a 0..1 scale.
    # FraudScoreResult.supervised_score uses a 0..100 scale.
    assert (
        abs(float(analysis.fraud_supervised_score) - probability * 100)
        < 0.01
    )


def test_shap_failure_does_not_discard_fraud_or_gst_results(db_session):
    extraction = _create_extraction(db_session)

    fraud = _fraud_result()

    graph = Mock(
        invoke=Mock(return_value=_graph_result(fraud))
    )

    pipeline = _pipeline(
        explain_side_effect=RuntimeError(
            "simulated SHAP generation failure"
        )
    )

    analysis = run_invoice_analysis(
        db_session,
        extraction,
        graph,
        pipeline,
    )

    assert analysis.verification_status == "COMPLETED"

    assert analysis.shap_status == "ERROR"

    assert analysis.shap_explanation == {
        "status": "ERROR",
        "detail": "SHAP explanation failed. See backend logs.",
    }

    assert float(analysis.fraud_score) == 13.01
    assert analysis.fraud_risk_category == "LOW"
    assert float(analysis.fraud_supervised_score) == 1.87

    assert analysis.gst_compliance_overall == "PASS"
    assert analysis.gst_compliance_result["overall"] == "PASS"

    assert (
        analysis.gst_external_verification["supplier"]["status"]
        == "FORMAT_VALID_NOT_API_VERIFIED"
    )

    db_session.expire_all()

    saved = db_session.get(InvoiceAnalysis, analysis.id)

    assert saved is not None
    assert saved.shap_status == "ERROR"
    assert saved.fraud_breakdown == fraud.breakdown
    assert saved.gst_compliance_result["overall"] == "PASS"


def test_analysis_versioning_preserves_both_records(db_session):
    extraction = _create_extraction(db_session)

    graph = Mock(
        invoke=Mock(return_value=_graph_result())
    )

    pipeline = _pipeline(
        explain_result={
            "model_prediction": {
                "predicted_label": 0,
                "predicted_probability": 0.0187,
            },
            "fraud_score": {
                "final_score": 13.01,
                "risk_category": "LOW",
            },
            "shap_explanation": {
                "status": "AVAILABLE",
                "contributions": [],
            },
        }
    )

    first = run_invoice_analysis(
        db_session,
        extraction,
        graph,
        pipeline,
    )

    second = run_invoice_analysis(
        db_session,
        extraction,
        graph,
        pipeline,
    )

    assert first.analysis_version == 1
    assert second.analysis_version == 2

    records = (
        db_session.query(InvoiceAnalysis)
        .filter(
            InvoiceAnalysis.extraction_id == extraction.id
        )
        .order_by(
            InvoiceAnalysis.analysis_version.asc()
        )
        .all()
    )

    assert [record.analysis_version for record in records] == [1, 2]

    assert db_session.get(InvoiceAnalysis, first.id) is not None
    assert db_session.get(InvoiceAnalysis, second.id) is not None


def test_graph_failure_persists_error_without_fabricated_results(db_session):
    extraction = _create_extraction(db_session)

    graph = Mock(
        invoke=Mock(
            side_effect=RuntimeError("simulated graph failure")
        )
    )

    analysis = run_invoice_analysis(
        db=db_session,
        extraction=extraction,
        verification_graph=graph,
        pipeline=None,
    )

    assert analysis.verification_status == "ERROR"

    assert analysis.error == "Invoice analysis graph execution failed."

    assert analysis.fraud_score is None
    assert analysis.fraud_risk_category is None
    assert analysis.fraud_supervised_score is None
    assert analysis.fraud_anomaly_score is None
    assert analysis.fraud_mathematical_score is None
    assert analysis.fraud_breakdown is None

    assert analysis.gst_compliance_overall is None
    assert analysis.gst_compliance_result is None
    assert analysis.gst_external_verification is None

    assert analysis.shap_status is None
    assert analysis.shap_explanation is None
    assert analysis.model_prediction is None

    assert analysis.key_signals == []
    assert analysis.validation_errors == []

    db_session.expire_all()

    saved = db_session.get(InvoiceAnalysis, analysis.id)

    assert saved is not None
    assert saved.verification_status == "ERROR"
    assert saved.fraud_score is None
    assert saved.gst_compliance_result is None
    assert saved.shap_explanation is None

    graph.invoke.assert_called_once()