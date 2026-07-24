"""
Conditional-edge routing + the emit node.

Routing after validate (the heart of the graph):

  SUCCESS                          -> emit (provenance: math-verified)
  INSUFFICIENT + competing_mapping -> disambiguate (one tiny LLM question)
  INSUFFICIENT otherwise           -> header fallback (LLM + soft constraints)
  FAILED + OCR-shaped findings     -> re_extract (once, escalated tier)
  FAILED otherwise / retry spent   -> emit (underwriting finding, first-class)

Provenance tiers (load-bearing for underwriter trust, not UI polish):
  math-verified        column sits on a witnessed identity cycle
  math-identified      column placed by peeling but not independently
                       corroborated (no failures either)
  math-constrained-llm LLM assignment that agrees with the validator's
                       uncertified prior
  llm-only             LLM assignment from headers alone
  virtual              not a physical column; derived from identities
"""

from __future__ import annotations

from .state import WippleState
from .validation import OCR_SHAPED
from .schemas import ALL_VAR_NAMES as VAR_NAMES

MAX_REEXTRACTS = 1

SUCCESS = "success"
INSUFFICIENT = "insufficient_information_for_validation"
FAILED = "validation_failed"


def route_after_extract(state: WippleState) -> str:
    raw = state.get("raw_table")
    if not raw or not raw.get("rows"):
        return "emit"          # extraction failed outright; report it
    return "parse"


def route_after_validate(state: WippleState) -> str:
    v = state.get("validation", {})
    status = v.get("status")
    if status == SUCCESS:
        return "emit"
    if status == INSUFFICIENT:
        if v.get("competing_mapping"):
            return "disambiguate"
        return "fallback"
    # FAILED: re-extract only when the diagnosis is transcription-shaped
    # and we have not already spent the retry.
    findings = v.get("findings", [])
    ocr_shaped = any(
        f.get("classification") in OCR_SHAPED or f.get("transplant_sources")
        for f in findings)
    if ocr_shaped and int(state.get("reextract_count", 0)) < MAX_REEXTRACTS:
        return "re_extract"
    return "emit"


# ---------------------------------------------------------------------------
# Emit
# ---------------------------------------------------------------------------

def _witnessed_cols(v: dict) -> set:
    return {w["column"] for w in v.get("witnesses", [])
            if w.get("column") is not None}


def emit_node(state: WippleState) -> dict:
    v = state.get("validation", {}) or {}
    status = v.get("status")
    raw = state.get("raw_table") or {}
    headers = raw.get("headers", [])
    col_map = state.get("numeric_col_map", [])

    def doc_header(mcol: int) -> str:
        j = col_map[mcol] if mcol < len(col_map) else -1
        return headers[j] if 0 <= j < len(headers) else ""

    columns: list = []
    overall: str

    if state.get("raw_table") is None:
        overall = "extraction_failed"
    elif status in (SUCCESS, FAILED):
        mapping = {int(k): val for k, val in (v.get("mapping") or {}).items()}
        witnessed = _witnessed_cols(v)
        for mcol in range(len(col_map)):
            var = mapping.get(mcol)
            if var is None:
                columns.append({"col": mcol, "header": doc_header(mcol),
                                "variable": None, "variable_name": None,
                                "provenance": "unassigned"})
                continue
            prov = "math-verified" if mcol in witnessed else "math-identified"
            columns.append({"col": mcol, "header": doc_header(mcol),
                            "variable": var,
                            "variable_name": VAR_NAMES.get(var, var),
                            "provenance": prov})
        for var, derivation in (v.get("virtuals") or {}).items():
            columns.append({"col": None, "header": None, "variable": var,
                            "variable_name": VAR_NAMES.get(var, var),
                            "provenance": "virtual",
                            "derivation": derivation})
        overall = ("verified" if status == SUCCESS
                   else "verified_mapping_with_findings")
    elif status == INSUFFICIENT and state.get("disambiguation"):
        chosen_key = ("competing_mapping"
                      if state["disambiguation"]["chosen"] == "competing"
                      else "mapping")
        mapping = {int(k): val for k, val in (v.get(chosen_key) or {}).items()}
        for mcol in range(len(col_map)):
            var = mapping.get(mcol)
            columns.append({"col": mcol, "header": doc_header(mcol),
                            "variable": var,
                            "variable_name": (VAR_NAMES.get(var)
                                              if var else None),
                            "provenance": ("math-constrained-llm" if var
                                           else "unassigned")})
        overall = "disambiguated"
    elif status == INSUFFICIENT:
        prior = ((v.get("diagnostics") or {})
                 .get("uncertified_best_mapping") or {})
        prior = {int(k): val for k, val in prior.items()}
        fb = state.get("fallback_mapping", {}) or {}
        fb_conf = state.get("fallback_confidence", {}) or {}
        for mcol in range(len(col_map)):
            var = fb.get(mcol)
            if var is None:
                columns.append({"col": mcol, "header": doc_header(mcol),
                                "variable": None, "variable_name": None,
                                "provenance": "unassigned"})
                continue
            prov = ("math-constrained-llm" if prior.get(mcol) == var
                    else "llm-only")
            columns.append({"col": mcol, "header": doc_header(mcol),
                            "variable": var,
                            "variable_name": VAR_NAMES.get(var, var),
                            "provenance": prov,
                            "llm_confidence": fb_conf.get(mcol)})
        overall = "llm_mapped_unverified" if fb else "unmapped"
    else:
        overall = "unmapped"

    report = {
        "source": state.get("source_name", ""),
        "analysis": state.get("analysis"),
        "table": state.get("table"),
        "overall_status": overall,
        "validator_status": status,
        "validator_reason": v.get("reason", ""),
        "columns": columns,
        "estimate_orientation": v.get("estimate_orientation", ""),
        "findings": v.get("findings", []),
        "failures": v.get("failures", []),
        "witnesses": v.get("witnesses", []),
        "totals_check": (state.get("parse_report") or {}).get("totals_check"),
        "parse": state.get("parse_report", {}),
        "extraction_attempts": state.get("extraction_attempts", []),
        "reextract_count": state.get("reextract_count", 0),
        "fallback_notes": state.get("fallback_notes", ""),
        "disambiguation": state.get("disambiguation"),
    }
    return {"report": report}
