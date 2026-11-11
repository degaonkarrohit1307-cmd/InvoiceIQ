"""
Typed graph state for the Phase 5 LangGraph orchestration layer.

This does not duplicate any Phase 3/4A/4C data structures -- it simply
carries references to them (ExtractedInvoiceFields, MathVerificationResult,
FraudScoreResult) plus a few orchestration-only fields (validation status,
error) through the graph.
"""
from __future__ import annotations

from typing import Any, TypedDict

from app.compliance.gst_sandbox_models import GSTVerificationResult
from app.compliance.models import GSTComplianceResult
from app.ml.fraud_scoring import FraudScoreResult
from app.services.field_extraction import ExtractedInvoiceFields
from app.services.math_verification import MathVerificationResult


class InvoiceGraphState(TypedDict, total=False):
    # --- input ---
    invoice_fields: ExtractedInvoiceFields | Any  # anything shaped like ExtractedInvoiceFields
    discount: float | None

    # --- validation node output ---
    is_valid: bool
    validation_errors: list[str]

    # --- mathematical verification node output (Phase 4A, reused as-is) ---
    math_result: MathVerificationResult | None

    # --- ML fraud analysis node output (Phase 4C pipeline, reused as-is) ---
    fraud_result: FraudScoreResult | None

    # --- GST compliance node output (Phase 6A local checks, reused as-is) ---
    gst_compliance_result: GSTComplianceResult | None
    # --- optional external GSTIN verification (Phase 6B, reused as-is) ---
    # Keyed "supplier"/"buyer"; absent/empty dict if neither GSTIN was present.
    gst_external_verification: dict[str, GSTVerificationResult] | None

    # --- final aggregated output ---
    final_result: dict | None

    # --- error handling ---
    error: str | None
    failed_node: str | None
