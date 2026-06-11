"""
Parse node (deterministic) and validate node (wraps validate_wip).

The validate node also serializes ValidationResult into a plain dict so the
graph state stays checkpoint-safe -- numpy never crosses a node boundary.
"""

from __future__ import annotations

import numpy as np

from .parsing import parse_table
from .state import WippleState
from .wip_validator import ValidationResult, validate_wip

# Finding classifications that point at the EXTRACTION as the likely culprit
# (transcription-shaped errors) vs. the document itself. Drives the
# failed-branch routing: re-extract once for these, emit a finding otherwise.
OCR_SHAPED = {
    "separator_or_magnitude_error",
    "extra_character",
    "dropped_character",
    "digit_transposition",
    "ocr_character_misread",
    "formatting_only",
}
DOCUMENT_SHAPED = {"unexplained_substitution"}


def parse_node(state: WippleState) -> dict:
    raw = state.get("raw_table")
    if not raw or not raw.get("rows"):
        return {"matrix": None, "job_labels": [], "numeric_col_map": [],
                "parse_report": {"notes": ["no extracted table"]}}
    result = parse_table(raw["rows"], headers=raw.get("headers"))
    return {
        "matrix": result.matrix,
        "job_labels": result.job_labels,
        "numeric_col_map": result.numeric_col_map,
        "parse_report": result.report(),
    }


def _np_to_py(x):
    if isinstance(x, np.ndarray):
        return [None if not np.isfinite(v) else float(v) for v in x.tolist()]
    if isinstance(x, (np.floating, np.integer)):
        return x.item()
    if isinstance(x, dict):
        return {k: _np_to_py(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_np_to_py(v) for v in x]
    return x


def serialize_validation(r: ValidationResult) -> dict:
    return {
        "status": r.status,
        "reason": r.reason,
        "mapping": {int(k): v for k, v in r.mapping.items()},
        "mapping_named": {int(k): v for k, v in r.mapping_named.items()},
        "estimate_orientation": r.estimate_orientation,
        "virtuals": dict(r.virtuals),
        "row_index": (None if r.row_index is None
                      else [int(i) for i in r.row_index]),
        "witnesses": [
            {"relation": w.relation, "business_form": w.business_form,
             "column": w.column, "n_rows": w.n_rows,
             "max_abs_residual": float(w.max_abs_residual),
             "weight": float(w.weight)}
            for w in r.witnesses
        ],
        "failures": [
            {"row_index": f.row_index, "row_label": f.row_label,
             "column": f.column, "variable": f.variable,
             "relation": f.relation, "observed": float(f.observed),
             "expected": float(f.expected),
             "difference": float(f.difference),
             "tolerance": float(f.tolerance)}
            for f in r.failures
        ],
        "findings": [
            {"row_index": g.row_index, "row_label": g.row_label,
             "culprit_column": g.culprit_column,
             "culprit_variable": g.culprit_variable,
             "candidate_variables": list(g.candidate_variables),
             "exonerated_variables": list(g.exonerated_variables),
             "observed": None if g.observed is None else float(g.observed),
             "proposed_correction": (None if g.proposed_correction is None
                                     else float(g.proposed_correction)),
             "correction_basis": list(g.correction_basis),
             "confidence": g.confidence,
             "classification": g.classification,
             "classification_detail": g.classification_detail,
             "transplant_sources": [list(t) for t in g.transplant_sources],
             "failing_relations": list(g.failing_relations)}
            for g in r.findings
        ],
        "competing_mapping": (None if r.competing_mapping is None
                              else {int(k): v
                                    for k, v in r.competing_mapping.items()}),
        "suggested_disambiguator": r.suggested_disambiguator,
        "diagnostics": _np_to_py(r.diagnostics),
    }


def validate_node(state: WippleState) -> dict:
    matrix = state.get("matrix")
    if matrix is None or getattr(matrix, "size", 0) == 0:
        return {"validation": {
            "status": "insufficient_information_for_validation",
            "reason": "no numeric matrix produced by parse",
            "mapping": {}, "findings": [], "failures": [],
            "competing_mapping": None, "suggested_disambiguator": None,
            "diagnostics": {},
        }}
    result = validate_wip(matrix, job_labels=state.get("job_labels"))
    return {"validation": serialize_validation(result)}
