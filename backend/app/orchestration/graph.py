"""
LangGraph graph construction (Phase 5 + Phase 6C).

Flow:
    invoice input
      -> validate_invoice_node
      -> mathematical_verification_node   (Phase 4A, reused)
      -> fraud_analysis_node               (Phase 4C, reused)
      -> gst_compliance_node               (Phase 6A/6B, reused)
      -> aggregate_decision_node
      -> END

Every node short-circuits on `state["error"]` (set by an earlier node), so
a failure doesn't cause downstream nodes to run on bad/missing data -- the
graph still reaches `aggregate_decision_node`, which turns that into an
honest ERROR result rather than crashing the whole run.

The fraud analysis node needs a trained `FraudAnalysisPipeline` instance,
which is expensive to build (it trains two ML models) -- so it's injected
via `functools.partial` at graph-build time rather than constructed fresh
per invocation. The GST compliance node similarly accepts an optional,
injected `gst_sandbox_client` (Phase 6B) -- None by default, in which case
external GSTIN verification degrades gracefully rather than attempting a
real network call (see gst_compliance_node/verify_gstin_externally).
"""
from __future__ import annotations

from functools import partial

from langgraph.graph import END, StateGraph

from app.compliance.gst_sandbox_client import GSTSandboxClient
from app.ml.pipeline import FraudAnalysisPipeline
from app.orchestration.nodes import (
    aggregate_decision_node,
    fraud_analysis_node,
    gst_compliance_node,
    mathematical_verification_node,
    validate_invoice_node,
)
from app.orchestration.state import InvoiceGraphState


def build_invoice_verification_graph(pipeline: FraudAnalysisPipeline, gst_sandbox_client: GSTSandboxClient | None = None):
    """
    Builds and compiles the Phase 5 + Phase 6C invoice verification/fraud/
    GST-compliance graph.

    `pipeline` must already be trained (`pipeline.train(...)`) or loaded
    (`FraudAnalysisPipeline.load(...)`) -- this function does not train one
    itself, to keep graph construction cheap and deterministic given a
    fixed pipeline.

    `gst_sandbox_client`: optional injected Phase 6B client for external
    GSTIN verification (e.g. a fake/mock in tests -- never a real network
    call unless an explicitly configured real client is passed). Omit to
    get the default graceful-degradation behavior (local Phase 6A checks
    still run; external verification reports itself as unavailable rather
    than attempting a real call with no configured credentials).
    """
    graph = StateGraph(InvoiceGraphState)

    graph.add_node("validate", validate_invoice_node)
    graph.add_node("verify_math", mathematical_verification_node)
    graph.add_node("analyze_fraud", partial(fraud_analysis_node, pipeline=pipeline))
    graph.add_node("gst_compliance", partial(gst_compliance_node, gst_sandbox_client=gst_sandbox_client))
    graph.add_node("aggregate", aggregate_decision_node)

    graph.set_entry_point("validate")
    graph.add_edge("validate", "verify_math")
    graph.add_edge("verify_math", "analyze_fraud")
    graph.add_edge("analyze_fraud", "gst_compliance")
    graph.add_edge("gst_compliance", "aggregate")
    graph.add_edge("aggregate", END)

    return graph.compile()


def run_invoice_verification(
    pipeline: FraudAnalysisPipeline,
    invoice_fields,
    discount: float | None = None,
    gst_sandbox_client: GSTSandboxClient | None = None,
) -> dict:
    """
    Convenience one-shot entry point: builds the graph, runs it once for a
    single invoice, and returns the `final_result` dict.

    Existing callers that omit `gst_sandbox_client` keep working unchanged
    -- it defaults to None, same graceful-degradation behavior as before.

    For repeated use (e.g. an API serving many requests), prefer calling
    `build_invoice_verification_graph(pipeline, gst_sandbox_client)` once
    and invoking the returned compiled graph's `.invoke(...)` repeatedly,
    to avoid rebuilding the graph structure on every call.
    """
    app = build_invoice_verification_graph(pipeline, gst_sandbox_client=gst_sandbox_client)
    initial_state: InvoiceGraphState = {"invoice_fields": invoice_fields, "discount": discount}
    final_state = app.invoke(initial_state)
    return final_state["final_result"]
