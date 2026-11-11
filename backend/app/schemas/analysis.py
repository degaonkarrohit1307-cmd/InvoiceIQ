"""Pydantic schemas for invoice analysis API responses."""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel


class InvoiceAnalysisResponse(BaseModel):
    analysis_id: uuid.UUID
    extraction_id: uuid.UUID
    analysis_version: int
    verification_status: str

    model_type: str | None

    fraud_score: Decimal | None
    fraud_risk_category: str | None
    fraud_supervised_score: Decimal | None
    fraud_anomaly_score: Decimal | None
    fraud_mathematical_score: Decimal | None

    model_prediction: dict[str, Any] | None
    fraud_breakdown: dict[str, Any] | None

    gst_compliance_overall: str | None
    gst_compliance_result: dict[str, Any] | None
    gst_external_verification: dict[str, Any] | None

    shap_status: str | None
    shap_explanation: dict[str, Any] | None

    key_signals: list[str]
    validation_errors: list[str]
    error: str | None

    created_at: datetime

    @classmethod
    def from_model(cls, analysis) -> "InvoiceAnalysisResponse":
        return cls(
            analysis_id=analysis.id,
            extraction_id=analysis.extraction_id,
            analysis_version=analysis.analysis_version,
            verification_status=analysis.verification_status,
            model_type=analysis.model_type,
            fraud_score=analysis.fraud_score,
            fraud_risk_category=analysis.fraud_risk_category,
            fraud_supervised_score=analysis.fraud_supervised_score,
            fraud_anomaly_score=analysis.fraud_anomaly_score,
            fraud_mathematical_score=analysis.fraud_mathematical_score,
            model_prediction=analysis.model_prediction,
            fraud_breakdown=analysis.fraud_breakdown,
            gst_compliance_overall=analysis.gst_compliance_overall,
            gst_compliance_result=analysis.gst_compliance_result,
            gst_external_verification=analysis.gst_external_verification,
            shap_status=analysis.shap_status,
            shap_explanation=analysis.shap_explanation,
            key_signals=analysis.key_signals or [],
            validation_errors=analysis.validation_errors or [],
            error=analysis.error,
            created_at=analysis.created_at,
        )