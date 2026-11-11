"""
LangGraph node functions (Phase 5).

Every node here is a thin wrapper: it reuses an existing Phase 3/4A/4C
function/class and adapts its result into the graph state. None of them
reimplement validation rules, arithmetic checks, or ML scoring -- that
logic lives exactly where it already did.

Each node catches exceptions from the underlying call and records them in
state (`error`, `failed_node`) rather than letting the graph crash, so a
single bad invoice can't take down the whole orchestration run.
"""
from __future__ import annotations

from app.compliance.gst import run_gst_compliance_check
from app.compliance.gst_sandbox_client import GSTSandboxClient
from app.compliance.gst_sandbox_service import verify_gstin_externally
from app.ml.pipeline import FraudAnalysisPipeline
from app.orchestration.state import InvoiceGraphState
from app.services.math_verification import run_math_verification

# Fields considered mandatory for an invoice to be worth running through
# verification/fraud analysis at all. This is orchestration-level input
# validation (garbage in / garbage out), distinct from Phase 3's own
# "missing_critical_fields" extraction-quality tracking, which is preserved
# untouched on the ExtractedInvoiceFields object itself.
_REQUIRED_FIELDS = ("subtotal", "total_amount")


def validate_invoice_node(state: InvoiceGraphState) -> InvoiceGraphState:
    """Basic structural validation: does this record have enough data to even attempt verification?"""
    try:
        fields = state.get("invoice_fields")
        errors: list[str] = []

        if fields is None:
            errors.append("No invoice fields were provided.")
        else:
            for name in _REQUIRED_FIELDS:
                if getattr(fields, name, None) is None:
                    errors.append(f"Missing required field: {name}")
            if not getattr(fields, "line_items", None):
                errors.append("Invoice has no line items.")

        return {**state, "is_valid": len(errors) == 0, "validation_errors": errors}

    except Exception as exc:  # noqa: BLE001
        return {**state, "error": str(exc), "failed_node": "validate_invoice_node", "is_valid": False}


def mathematical_verification_node(state: InvoiceGraphState) -> InvoiceGraphState:
    """Reuses app.services.math_verification.run_math_verification (Phase 4A) unchanged."""
    if state.get("error") or state.get("is_valid") is False:
        return state  # short-circuit: an earlier node already failed or rejected the input

    try:
        fields = state["invoice_fields"]
        discount = state.get("discount")
        math_result = run_math_verification(fields, discount=discount)
        return {**state, "math_result": math_result}

    except Exception as exc:  # noqa: BLE001
        return {**state, "error": str(exc), "failed_node": "mathematical_verification_node"}


def fraud_analysis_node(state: InvoiceGraphState, pipeline: FraudAnalysisPipeline) -> InvoiceGraphState:
    """
    Reuses app.ml.pipeline.FraudAnalysisPipeline.analyze() (Phase 4C)
    unchanged. `pipeline` must already be trained/loaded -- this node does
    not train a model itself (training is expensive and orchestration-time
    training would make every graph run non-deterministic/slow).
    """
    if state.get("error") or state.get("is_valid") is False:
        return state

    try:
        fields = state["invoice_fields"]
        discount = state.get("discount")
        fraud_result = pipeline.analyze(fields, discount=discount)
        return {**state, "fraud_result": fraud_result}

    except Exception as exc:  # noqa: BLE001
        return {**state, "error": str(exc), "failed_node": "fraud_analysis_node"}


def gst_compliance_node(state: InvoiceGraphState, gst_sandbox_client: GSTSandboxClient | None = None) -> InvoiceGraphState:
    """
    Reuses app.compliance.gst.run_gst_compliance_check (Phase 6A, local
    deterministic checks -- always run, no network) and, for each GSTIN
    present on the invoice, app.compliance.gst_sandbox_service.
    verify_gstin_externally (Phase 6B, optional external lookup) unchanged.

    `gst_sandbox_client`: injected (e.g. in tests, a fake/mock client that
    never touches the network). If None, verify_gstin_externally falls back
    to its own default behavior, which itself degrades gracefully
    (FORMAT_VALID_NOT_API_VERIFIED) when Sandbox credentials aren't
    configured, rather than raising or attempting a doomed network call --
    see gst_sandbox_service.py. So an unconfigured/unavailable Sandbox API
    never crashes this node or the graph.
    """
    if state.get("error") or state.get("is_valid") is False:
        return state

    try:
        fields = state["invoice_fields"]
        compliance_result = run_gst_compliance_check(fields)

        external_verification: dict = {}
        supplier_gstin = getattr(fields, "supplier_gstin", None)
        buyer_gstin = getattr(fields, "buyer_gstin", None)
        if supplier_gstin is not None:
            external_verification["supplier"] = verify_gstin_externally(supplier_gstin, client=gst_sandbox_client)
        if buyer_gstin is not None:
            external_verification["buyer"] = verify_gstin_externally(buyer_gstin, client=gst_sandbox_client)

        return {
            **state,
            "gst_compliance_result": compliance_result,
            "gst_external_verification": external_verification or None,
        }

    except Exception as exc:  # noqa: BLE001
        return {**state, "error": str(exc), "failed_node": "gst_compliance_node"}


def aggregate_decision_node(state: InvoiceGraphState) -> InvoiceGraphState:
    """
    Builds the final structured result. If an earlier node failed, produces
    an honest error result instead of a fabricated success result.

    GST COMPLIANCE POLICY (Phase 6C -- documented here and exercised by
    tests/test_orchestration_gst_compliance.py):
      - GST compliance (Phase 6A: PASS/FAIL/NOT_CHECKABLE) is reported as
        its OWN independent field in the final result. It does NOT modify
        `fraud_score` or `fraud_risk_category` -- those remain exactly
        Phase 4C's existing ML+math-derived output, unchanged. GST
        compliance and fraud risk are two separate signals a caller can
        combine however they choose; this orchestration layer does not
        collapse them into one number.
      - If GST compliance overall == FAIL, each failing rule is added to
        `key_signals` -- exactly how a failing math-verification rule
        already is -- so it's visible as evidence without being silently
        folded into a score.
      - `verification_status` describes whether the ORCHESTRATION
        completed (COMPLETED/ERROR/INVALID_INPUT), not whether the
        invoice passed every check. A GST FAIL does not flip
        verification_status to some "GST_FAILED" value, exactly as a math
        verification FAIL doesn't either -- "the pipeline ran" and "the
        invoice looks compliant" are different questions.
      - External GSTIN verification (Phase 6B) being unavailable/
        unconfigured/timed out is reported via its own honest status value
        (e.g. FORMAT_VALID_NOT_API_VERIFIED, API_UNAVAILABLE, API_TIMEOUT)
        and is NEVER treated as a FAIL or as evidence of fraud -- "we
        couldn't check" is categorically different from "we checked and
        it's wrong". It does not affect fraud_score, fraud_risk_category,
        or the local GST compliance overall status, which Phase 6A
        computes independently of Phase 6B's external result by design.
    """
    if state.get("error"):
        final_result = {
            "verification_status": "ERROR",
            "error": state["error"],
            "failed_node": state.get("failed_node"),
            "math_verification": None,
            "fraud_score": None,
            "fraud_risk_category": None,
            "gst_compliance": None,
            "gst_external_verification": None,
            "key_signals": [],
        }
        return {**state, "final_result": final_result}

    if not state.get("is_valid", False):
        final_result = {
            "verification_status": "INVALID_INPUT",
            "error": None,
            "validation_errors": state.get("validation_errors", []),
            "math_verification": None,
            "fraud_score": None,
            "fraud_risk_category": None,
            "gst_compliance": None,
            "gst_external_verification": None,
            "key_signals": [],
        }
        return {**state, "final_result": final_result}

    math_result = state.get("math_result")
    fraud_result = state.get("fraud_result")
    gst_result = state.get("gst_compliance_result")
    gst_external = state.get("gst_external_verification")

    key_signals: list[str] = []
    if math_result is not None:
        failed_rules = [c.rule for c in math_result.checks if c.result.value == "FAIL"]
        key_signals.extend(f"Math check failed: {rule}" for rule in failed_rules)
    if gst_result is not None:
        failed_gst_rules = [c.rule for c in gst_result.checks if c.result.value == "FAIL"]
        key_signals.extend(f"GST check failed: {rule}" for rule in failed_gst_rules)
    if fraud_result is not None:
        for key, value in fraud_result.breakdown.items():
            key_signals.append(f"{key}: {value}")

    final_result = {
        "verification_status": "COMPLETED",
        "error": None,
        "math_verification": {
            "overall": math_result.overall.value if math_result else None,
            "checks": [
                {
                    "rule": c.rule, "result": c.result.value, "expected": c.expected,
                    "actual": c.actual, "difference": c.difference, "detail": c.detail,
                }
                for c in (math_result.checks if math_result else [])
            ],
        },
        "fraud_score": fraud_result.final_score if fraud_result else None,
        "fraud_risk_category": fraud_result.risk_category.value if fraud_result else None,
        "gst_compliance": gst_result.to_dict() if gst_result is not None else None,
        "gst_external_verification": (
            {k: v.to_dict() for k, v in gst_external.items()} if gst_external else None
        ),
        "key_signals": key_signals,
    }
    return {**state, "final_result": final_result}
