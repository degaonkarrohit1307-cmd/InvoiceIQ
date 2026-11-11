"""Invoice analysis orchestration and persistence."""

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.logging_config import get_logger
from app.ml.pipeline import FraudAnalysisPipeline
from app.models.analysis import InvoiceAnalysis

logger = get_logger(__name__)


def run_invoice_analysis(
    db: Session,
    extraction,
    verification_graph,
    pipeline: FraudAnalysisPipeline | None,
) -> InvoiceAnalysis:
    """
    Runs LangGraph analysis and persists fraud, GST and SHAP results
    against a specific versioned invoice extraction.
    """

    # Step 1: Run the existing LangGraph workflow.
    try:
        if verification_graph is None:
            raise RuntimeError("Invoice verification graph is not initialized.")

        graph_state = verification_graph.invoke({
            "invoice_fields": extraction,
            "discount": None,
        })

        final_result = graph_state.get("final_result") or {}

    except Exception:
        logger.exception(
            "Invoice analysis graph failed for extraction %s",
            extraction.id,
        )

        graph_state = {}
        final_result = {
            "verification_status": "ERROR",
            "error": "Invoice analysis graph execution failed.",
            "key_signals": [],
            "validation_errors": [],
        }

    # Step 2: Retrieve the existing fraud result.
    fraud_result = graph_state.get("fraud_result")

    # Step 3: Generate SHAP explanation separately.
    explanation_result = None

    if (
        pipeline is not None
        and fraud_result is not None
        and final_result.get("verification_status") == "COMPLETED"
    ):
        try:
            explanation_result = pipeline.explain_supervised_prediction(
                extraction
            )
        except Exception:
            logger.exception(
                "SHAP explanation failed for extraction %s",
                extraction.id,
            )

            explanation_result = {
                "shap_explanation": {
                    "status": "ERROR",
                    "detail": "SHAP explanation failed. See backend logs.",
                }
            }

    model_prediction = (
        explanation_result.get("model_prediction")
        if explanation_result else None
    )

    shap_explanation = (
        explanation_result.get("shap_explanation")
        if explanation_result else None
    )

    # Step 4: Generate the next analysis version.
    current_version = (
        db.query(func.max(InvoiceAnalysis.analysis_version))
        .filter(InvoiceAnalysis.extraction_id == extraction.id)
        .scalar()
        or 0
    )

    # Step 5: Build the database record.
    analysis = InvoiceAnalysis(
        extraction_id=extraction.id,
        analysis_version=current_version + 1,

        verification_status=final_result.get(
            "verification_status", "ERROR"
        ),

        model_type=(
            pipeline.supervised_model.model_type
            if pipeline and pipeline.supervised_model
            else None
        ),

        fraud_score=(
            fraud_result.final_score if fraud_result else None
        ),

        fraud_risk_category=(
            fraud_result.risk_category.value if fraud_result else None
        ),

        fraud_supervised_score=(
            fraud_result.supervised_score if fraud_result else None
        ),

        fraud_anomaly_score=(
            fraud_result.anomaly_score if fraud_result else None
        ),

        fraud_mathematical_score=(
            fraud_result.mathematical_score if fraud_result else None
        ),

        model_prediction=model_prediction,

        fraud_breakdown=(
            fraud_result.breakdown if fraud_result else None
        ),

        gst_compliance_overall=(
            final_result.get("gst_compliance", {}).get("overall")
            if final_result.get("gst_compliance")
            else None
        ),

        gst_compliance_result=final_result.get("gst_compliance"),

        gst_external_verification=final_result.get(
            "gst_external_verification"
        ),

        shap_status=(
            shap_explanation.get("status")
            if shap_explanation else None
        ),

        shap_explanation=shap_explanation,

        key_signals=final_result.get("key_signals") or [],

        validation_errors=final_result.get("validation_errors") or [],

        error=final_result.get("error"),
    )

    # Step 6: Persist the results.
    try:
        db.add(analysis)
        db.commit()
        db.refresh(analysis)

        logger.info(
            "Invoice analysis saved: extraction=%s version=%s status=%s",
            extraction.id,
            analysis.analysis_version,
            analysis.verification_status,
        )

        return analysis

    except Exception:
        db.rollback()
        logger.exception(
            "Failed to persist invoice analysis for extraction %s",
            extraction.id,
        )
        raise