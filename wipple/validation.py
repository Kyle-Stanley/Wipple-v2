"""
Parse node (deterministic) and validate node (wraps validate_wip).

The validate node also serializes ValidationResult into a plain dict so the
graph state stays checkpoint-safe -- numpy never crosses a node boundary.
"""

from __future__ import annotations

import numpy as np

from .parsing import parse_table
from .state import WippleState
from .cc_validator import validate_cc
from .wip_validator import VAR_NAMES, ValidationResult, validate_wip

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
DOCUMENT_SHAPED = {"unexplained_substitution", "sign_error"}

CLASSIFICATION_LABELS = {
    "separator_or_magnitude_error": "scale error",
    "extra_character": "extra digit",
    "dropped_character": "missing digit",
    "digit_transposition": "digit swap",
    "ocr_character_misread": "digit error",
    "formatting_only": "formatting",
    "neighbor_transplant": "wrong cell",
    "unexplained_substitution": "wrong value",
    "sign_error": "sign error",
    "ambiguous_multi_cell": "multiple cells",
    "unresolved": "unresolved",
}


def _trailing_total_evidence(matrix) -> dict | None:
    """Return evidence that the final parsed row is an aggregate total.

    This is deliberately numerical rather than header-based. A candidate row
    must equal the sum of all preceding rows across several additive-looking
    columns. Ratio/percent columns are naturally ignored because their final
    value will not resemble the sum of the preceding percentages.

    Requiring agreement across multiple columns makes it extraordinarily
    unlikely that a real job will be removed merely because one value happens
    to equal a prior-column sum.
    """
    a = np.asarray(matrix, dtype=float)
    if a.ndim != 2 or a.shape[0] < 4 or a.shape[1] < 4:
        return None

    prior = a[:-1]
    candidate = a[-1]
    considered = []
    matched = []

    for j in range(a.shape[1]):
        col = prior[:, j]
        finite = np.isfinite(col)
        if not np.isfinite(candidate[j]) or int(finite.sum()) < 2:
            continue

        vals = col[finite]
        nonzero = np.abs(vals) > 0.51
        if int(nonzero.sum()) < 2:
            continue

        expected = float(vals.sum())
        observed = float(candidate[j])

        # Exclude ratio-like / tiny columns and values that do not have the
        # scale shape of a total. Dollar totals should generally be larger
        # than a typical constituent row.
        typical = float(np.median(np.abs(vals[nonzero])))
        if abs(expected) <= 2.0 or abs(observed) < 1.25 * max(typical, 1.0):
            continue

        tolerance = max(
            1.0,
            0.0005 * max(abs(expected), abs(observed)),
        )
        considered.append(j)
        if abs(observed - expected) <= tolerance:
            matched.append(j)

    if not considered:
        return None

    # Four matching numeric columns is already strong evidence. Also require
    # at least half of the additive-looking columns so a partial coincidence
    # cannot remove a real final job.
    required = max(4, int(np.ceil(0.50 * len(considered))))
    if len(matched) < required:
        return None

    return {
        "row_index": int(a.shape[0] - 1),
        "reason": "trailing_row_sums_predecessors",
        "matching_numeric_columns": [int(j) for j in matched],
        "considered_numeric_columns": [int(j) for j in considered],
        "matches": int(len(matched)),
        "required": int(required),
    }


def parse_node(state: WippleState) -> dict:
    raw = state.get("raw_table")
    if not raw or not raw.get("rows"):
        return {"matrix": None, "job_labels": [], "numeric_col_map": [],
                "parse_report": {"notes": ["no extracted table"]}}

    rows = list(raw["rows"])
    result = parse_table(rows, headers=raw.get("headers"))

    # Parse once with every row so the detector can inspect the complete
    # numeric table. If the final row is proven to be a total, reparse without
    # it. The original raw table remains untouched for stated-total checks.
    total_evidence = _trailing_total_evidence(result.matrix)
    if total_evidence is not None and rows:
        rows = rows[:-1]
        result = parse_table(rows, headers=raw.get("headers"))

    report = result.report()
    if total_evidence is not None:
        report.setdefault("notes", []).append(
            "final aggregate row excluded from job validation: "
            f"{total_evidence['matches']} numeric columns equal the sum "
            "of their predecessors"
        )
        report["excluded_rows"] = [total_evidence]

    return {
        "matrix": result.matrix,
        "job_labels": result.job_labels,
        "numeric_col_map": result.numeric_col_map,
        "parse_report": report,
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


def _safe_float(x) -> float | None:
    """Convert to a JSON-safe float, or None if NaN/Inf/unconvertible.

    Plain float() happily returns NaN/Inf as-is, and Python's default
    json.dumps (allow_nan=True) then writes the literal tokens NaN/Infinity
    into the JSON string -- which is invalid per the JSON spec and breaks
    any standards-compliant JSON.parse() on the frontend (e.g. browsers).

    This is the float-side counterpart to _np_to_py: _np_to_py already
    guards numpy arrays/scalars passed through diagnostics, but the
    witnesses/failures/findings blocks below call float(...) directly on
    dataclass fields, so a NaN already present on r.witnesses[i].* or
    r.failures[i].* (e.g. from a 0/0 division upstream in wip_validator)
    was passing through untouched. Route every such field through here.
    """
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if np.isfinite(v) else None


def serialize_validation(r: ValidationResult) -> dict:
    return {
        "status": r.status,
        "reason": r.reason,
        "mapping": {int(k): v for k, v in r.mapping.items()},
        "mapping_named": {int(k): v for k, v in r.mapping_named.items()},
        "variable_names": dict(VAR_NAMES),
        "estimate_orientation": r.estimate_orientation,
        "virtuals": dict(r.virtuals),
        "row_index": (None if r.row_index is None
                      else [int(i) for i in r.row_index]),
        "witnesses": [
            {"relation": w.relation, "business_form": w.business_form,
             "column": w.column, "n_rows": w.n_rows,
             "max_abs_residual": _safe_float(w.max_abs_residual),
             "weight": _safe_float(w.weight)}
            for w in r.witnesses
        ],
        "failures": [
            {"row_index": f.row_index, "row_label": f.row_label,
             "column": f.column, "variable": f.variable,
             "relation": f.relation, "observed": _safe_float(f.observed),
             "expected": _safe_float(f.expected),
             "difference": _safe_float(f.difference),
             "tolerance": _safe_float(f.tolerance),
             # Surfaces *why* a value is null instead of silently dropping
             # the signal -- a 0/0 division is a real finding (the row's
             # math is undefined), not the same as "no data here."
             "undefined_relation": not (
                 np.isfinite(f.observed) and np.isfinite(f.expected)
                 and np.isfinite(f.difference)
             )}
            for f in r.failures
        ],
        "findings": [
            {"row_index": g.row_index, "row_label": g.row_label,
             "culprit_column": g.culprit_column,
             "culprit_variable": g.culprit_variable,
             "candidate_variables": list(g.candidate_variables),
             "exonerated_variables": list(g.exonerated_variables),
             "observed": _safe_float(g.observed) if g.observed is not None else None,
             "proposed_correction": (None if g.proposed_correction is None
                                     else _safe_float(g.proposed_correction)),
             "correction_basis": list(g.correction_basis),
             "confidence": g.confidence,
             "classification": g.classification,
             "classification_label": CLASSIFICATION_LABELS.get(
                 g.classification, g.classification.replace("_", " ")),
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
    labels = state.get("job_labels")
    chosen, race = run_schema_race(matrix, labels)
    out = serialize_validation(chosen)
    out["schema"] = race["chosen"]
    out.setdefault("diagnostics", {})["schema_race"] = race
    return {"validation": out}


def _race_rank(r: ValidationResult) -> int:
    """SUCCESS and FAILED both mean the mapping CERTIFIED (failed = certified
    mapping, wrong values -- exactly the state that carries findings); a
    mapping without witnesses ranks below both; nothing ranks last."""
    if r.mapping and r.witnesses:
        return 2
    if r.mapping:
        return 1
    return 0


def _race_score(r: ValidationResult, n_cols: int) -> float:
    """Witnessed evidence weight x explained column fraction: the parsimony
    term is what stops the CC engine claiming the G = V - C corner of a WIP
    table it explains 3 columns of."""
    w = sum(x.weight for x in r.witnesses)
    return w * (len(r.mapping) / max(n_cols, 1))


def run_schema_race(matrix, labels):
    """Both engines run on every logical table; certification decides the
    schema. No classifier, no header semantics -- the numbers vote."""
    wip = validate_wip(matrix, job_labels=labels)
    cc = validate_cc(matrix, job_labels=labels)
    m = matrix.shape[1]
    kw = (_race_rank(wip), _race_score(wip, m))
    kc = (_race_rank(cc), _race_score(cc, m))
    chosen, name = (wip, "wip") if kw >= kc else (cc, "cc")
    return chosen, {"chosen": name,
                    "wip": {"status": wip.status, "rank": kw[0],
                            "score": round(kw[1], 3),
                            "explained": len(wip.mapping)},
                    "cc": {"status": cc.status, "rank": kc[0],
                           "score": round(kc[1], 3),
                           "explained": len(cc.mapping)}}
