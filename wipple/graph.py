"""
Graph assembly.

    extract -> parse -> validate -+-> emit                       (success)
       ^                          +-> disambiguate -> emit       (ambiguous)
       |                          +-> fallback -> emit           (sparse)
       +------- re_extract <------+                              (ocr-shaped
                                                                  failure, x1)

When this becomes one component of the full financial scanner, the compiled
graph here drops in as a single subgraph node -- that is the maintainability
argument for LangGraph over a hand-rolled async pipeline.

run_pipeline() is the convenience runner: it injects the shared Metrics
object, invokes the graph, and returns (report, metrics_summary).
"""

from __future__ import annotations

from langgraph.graph import END, StateGraph

from .extraction import extract_node, re_extract_node
from .fallback import disambiguate_node, fallback_node
from .model_client import Metrics
from .analysis import analyze_node
from .routing import emit_node, route_after_extract, route_after_validate
from .state import WippleState
from .validation import parse_node, validate_node


def build_graph():
    g = StateGraph(WippleState)

    g.add_node("extract", extract_node)
    g.add_node("parse", parse_node)
    g.add_node("validate", validate_node)
    g.add_node("re_extract", re_extract_node)
    g.add_node("fallback", fallback_node)
    g.add_node("disambiguate", disambiguate_node)
    g.add_node("analyze", analyze_node)
    g.add_node("emit", emit_node)

    # Entry: demo / pre-extracted runs may inject raw_table and skip extract.
    g.set_conditional_entry_point(
        lambda s: "parse" if s.get("raw_table") else "extract",
        {"parse": "parse", "extract": "extract"})
    g.add_conditional_edges("extract", route_after_extract,
                            {"parse": "parse", "emit": "emit"})
    g.add_edge("parse", "validate")
    g.add_conditional_edges("validate", route_after_validate,
                            {"emit": "analyze",
                             "fallback": "fallback",
                             "disambiguate": "disambiguate",
                             "re_extract": "re_extract"})
    g.add_edge("re_extract", "extract")
    g.add_edge("fallback", "analyze")
    g.add_edge("disambiguate", "analyze")
    g.add_edge("analyze", "emit")
    g.add_edge("emit", END)

    return g.compile()


def run_pipeline(pdf_bytes: bytes, source_name: str = "",
                 raw_table: dict | None = None) -> tuple[dict, dict]:
    graph = build_graph()
    metrics = Metrics()
    final = graph.invoke({
        "raw_table": raw_table,
        "pdf_bytes": pdf_bytes,
        "source_name": source_name,
        "extraction_tier": "primary",
        "reextract_count": 0,
        "extraction_attempts": [],
        "_metrics": metrics,
    })
    report = final.get("report", {})
    report["metrics"] = metrics.summary()
    return report, metrics.summary()
