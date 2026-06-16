"""
Deterministic analysis: KPIs + continuously-scored underwriting signals.

No LLM anywhere. Severity is graded, not triggered: each signal scores
0..1 as a continuous function of how far into the bad region a job sits and
how many dollars ride on it -- a 69%-complete job and a 71%-complete job
get nearly identical treatment (no cliff edges).

Every threshold lives in TUNE and is a PLACEHOLDER pending the real
rulebook from someone who has actually sat at a WIP desk. The shapes of the
functions are the design; the constants are not.
"""

from __future__ import annotations

import numpy as np

from .state import WippleState
from .wip_validator import VAR_NAMES

TUNE = {
    "late_stage_p": 0.55,      # completion where underbilling starts to bite
    "ub_frac_full": 0.10,      # U/V that saturates severity at late stage
    "ob_frac_full": 0.15,      # O/V that saturates job-borrow severity
    "loss_margin_full": 0.05,  # negative margin that saturates loss severity
    "overrun_full": 0.10,      # (D-C)/C that saturates overrun severity
    "early_p": 0.15,           # "early stage" completion cutoff
    "early_share_floor": 0.25, # portfolio share where concentration starts
    "early_share_full": 0.70,  # ...and where it saturates
    "outlier_z_floor": 2.0,    # robust z where margin outlier starts
    "outlier_z_full": 5.0,
    "min_signal_severity": 0.12,
}
# NOTE: failing-row density no longer suppresses anything; with no
# auto-application there is nothing to suppress. The validator's own status
# is the only verdict on document quality.


def _clamp(x, lo=0.0, hi=1.0):
    return float(min(max(x, lo), hi))


def _money(x):
    return f"${x:,.0f}"


def _safe_float(x) -> float | None:
    """JSON-safe float: NaN/Inf/unconvertible -> None instead of a literal
    NaN token that breaks standards-compliant JSON.parse() on the frontend.

    _job_rows() below was the one place in this file building per-job dicts
    with bare float(...) calls on core["U"]/core["O"]/etc. -- no guard at
    all, unlike compute_kpis/compute_signals which already go through
    np.nansum/np.where and tolerate NaN gracefully. If a job's U or O value
    is NaN (either a blank/unparseable printed cell when U/O is a *direct*
    mapped column, or propagated from an upstream NaN in E/B when U/O is
    *derived* via reconstruct_core), it was reaching json.dumps untouched.
    """
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if np.isfinite(v) else None


def reconstruct_core(matrix, mapping):
    """Physical columns where mapped; identity-derived otherwise.
    Returns (core: {var: np.array}, derived: set[var]) or (None, ...)."""
    cols = {var: matrix[:, c] for c, var in mapping.items()
            if c < matrix.shape[1]}
    derived = set()

    def need(var, fn, *deps):
        if var in cols:
            return True
        if all(d in cols for d in deps):
            cols[var] = fn(*[cols[d] for d in deps])
            derived.add(var)
            return True
        return False

    need("C", lambda V, G: V - G, "V", "G")
    need("G", lambda V, C: V - C, "V", "C")
    with np.errstate(divide="ignore", invalid="ignore"):
        need("D", lambda C, P: C * P, "C", "P")
        need("E", lambda V, D, C: V * D / C, "V", "D", "C")
        need("E", lambda V, P: V * P, "V", "P")
    need("B", lambda E, U, O: E - U + O, "E", "U", "O")
    need("U", lambda E, B: np.maximum(E - B, 0.0), "E", "B")
    need("O", lambda E, B: np.maximum(B - E, 0.0), "E", "B")
    if not all(v in cols for v in ("V", "C", "D")):
        return None, derived
    return cols, derived


def compute_kpis(core):
    V, C, D = core["V"], core["C"], core["D"]
    E = core.get("E")
    B = core.get("B")
    tcv = float(np.nansum(V))
    est_gp = float(np.nansum(V - C))
    earned = float(np.nansum(E)) if E is not None else None
    earned_gp = float(np.nansum(E - D)) if E is not None else None
    out = {
        "total_contract_value": tcv,
        "estimated_gross_profit": est_gp,
        "gp_pct": (est_gp / tcv) if tcv else None,
        "cost_to_complete": float(np.nansum(C - D)),
        "earned_revenue": earned,
        "uegp": (est_gp - earned_gp) if earned_gp is not None else None,
        "net_billing_position": (float(np.nansum(B - E))
                                 if (B is not None and E is not None) else None),
        "backlog_revenue": (float(np.nansum(V - E)) if E is not None else None),
        "unbilled_contract": (float(np.nansum(V - B)) if B is not None else None),
        "underbillings_total": (float(np.nansum(core["U"]))
                                if "U" in core else None),
        "overbillings_total": (float(np.nansum(core["O"]))
                               if "O" in core else None),
        "job_count": int(V.shape[0]),
    }
    return out


def compute_signals(core, labels):
    V, C, D = core["V"], core["C"], core["D"]
    with np.errstate(divide="ignore", invalid="ignore"):
        P = np.where(C > 0, D / C, 0.0)
        m = np.where(V > 0, (V - C) / V, 0.0)
    E = core.get("E")
    B = core.get("B")
    U = core.get("U")
    O = core.get("O")
    n = len(labels)
    T = TUNE
    signals = []

    def job(i, dollars, detail):
        return {"label": labels[i], "dollars": round(float(dollars)),
                "detail": detail}

    # -- trapped cash: underbilling on late-stage work -----------------------
    if U is not None:
        rows = []
        for i in range(n):
            w = _clamp((P[i] - T["late_stage_p"]) / (0.95 - T["late_stage_p"]))
            sev = w * _clamp((U[i] / max(V[i], 1)) / T["ub_frac_full"])
            if sev > T["min_signal_severity"]:
                rows.append((sev, i))
        if rows:
            rows.sort(reverse=True)
            dollars = sum(U[i] for _, i in rows)
            k = len(rows)
            signals.append({
                "id": "trapped_cash",
                "severity": round(max(s for s, _ in rows), 3),
                "headline": (f"{k} late-stage job{'s' if k > 1 else ''} "
                             f"carrying {_money(dollars)} in unbilled "
                             "earned revenue"),
                "dollars": round(float(dollars)),
                "jobs": [job(i, U[i],
                             f"{P[i]:.0%} complete, {_money(U[i])} underbilled")
                         for _, i in rows[:5]],
                "why": ("Earned revenue the contractor has not billed. On "
                        "nearly-finished work this usually means unapproved "
                        "change orders or receivables that may never "
                        "collect; in a default the surety inherits the gap."),
            })

    # -- job borrow: overbilling funding other work ---------------------------
    if O is not None:
        rows = []
        for i in range(n):
            sev = _clamp((O[i] / max(V[i], 1)) / T["ob_frac_full"])
            if sev > T["min_signal_severity"]:
                rows.append((sev, i))
        if rows:
            rows.sort(reverse=True)
            dollars = sum(O[i] for _, i in rows)
            signals.append({
                "id": "job_borrow",
                "severity": round(max(s for s, _ in rows), 3),
                "headline": (f"{_money(dollars)} billed ahead of earnings "
                             f"across {len(rows)} "
                             f"job{'s' if len(rows) > 1 else ''}"),
                "dollars": round(float(dollars)),
                "jobs": [job(i, O[i],
                             f"{P[i]:.0%} complete, {_money(O[i])} overbilled")
                         for _, i in rows[:5]],
                "why": ("Cash collected for work not yet performed is "
                        "typically already spent on other jobs. The "
                        "remaining work must be financed from elsewhere -- "
                        "a quiet dependency on everything finishing clean."),
            })

    # -- loss jobs -------------------------------------------------------------
    rows = [( _clamp(-m[i] / T["loss_margin_full"]), i)
            for i in range(n) if m[i] < 0]
    rows = [r for r in rows if r[0] > T["min_signal_severity"]]
    if rows:
        rows.sort(reverse=True)
        dollars = sum(C[i] - V[i] for _, i in rows)
        signals.append({
            "id": "loss_jobs",
            "severity": round(max(s for s, _ in rows), 3),
            "headline": (f"{len(rows)} job{'s' if len(rows) > 1 else ''} "
                         f"estimated to lose {_money(dollars)}"),
            "dollars": round(float(dollars)),
            "jobs": [job(i, C[i] - V[i],
                         f"estimated margin {m[i]:.1%}") for _, i in rows[:5]],
            "why": ("GAAP requires the full expected loss to be recognized "
                    "immediately, not as the work progresses. A loss job on "
                    "the schedule is a direct hit to the indemnitor's "
                    "net worth."),
        })

    # -- cost overruns: spent past the estimate -------------------------------
    rows = [(_clamp(((D[i] - C[i]) / max(C[i], 1)) / T["overrun_full"]), i)
            for i in range(n) if D[i] > C[i]]
    rows = [r for r in rows if r[0] > T["min_signal_severity"]]
    if rows:
        rows.sort(reverse=True)
        dollars = sum(D[i] - C[i] for _, i in rows)
        signals.append({
            "id": "cost_overrun",
            "severity": round(max(s for s, _ in rows), 3),
            "headline": (f"{len(rows)} job{'s' if len(rows) > 1 else ''} "
                         f"already {_money(dollars)} past estimated cost"),
            "dollars": round(float(dollars)),
            "jobs": [job(i, D[i] - C[i],
                         f"costs at {D[i] / max(C[i], 1):.0%} of estimate")
                     for _, i in rows[:5]],
            "why": ("Cost to date exceeding the total estimate means the "
                    "estimate is stale and the stated gross profit is "
                    "fiction until re-estimated."),
        })

    # -- early-stage concentration --------------------------------------------
    early = P < T["early_p"]
    tcv = float(np.nansum(V))
    if tcv > 0 and early.any():
        share = float(np.nansum(V[early])) / tcv
        sev = _clamp((share - T["early_share_floor"])
                     / (T["early_share_full"] - T["early_share_floor"]))
        if sev > T["min_signal_severity"]:
            signals.append({
                "id": "early_concentration",
                "severity": round(sev, 3),
                "headline": (f"{share:.0%} of contract value is on jobs "
                             f"under {T['early_p']:.0%} complete"),
                "dollars": round(float(np.nansum(V[early]))),
                "jobs": [job(i, V[i], f"{P[i]:.0%} complete")
                         for i in np.where(early)[0][:5]],
                "why": ("Early-stage estimates are unproven. A book "
                        "concentrated at the front of the lifecycle has "
                        "margins that exist mostly on paper."),
            })

    # -- margin outlier vs the contractor's own book (portfolio-as-prior) -----
    if n >= 6:
        med = float(np.median(m))
        iqr = float(np.subtract(*np.percentile(m, [75, 25]))) or 0.01
        rows = []
        for i in range(n):
            z = abs(m[i] - med) / iqr
            sev = _clamp((z - T["outlier_z_floor"])
                         / (T["outlier_z_full"] - T["outlier_z_floor"]))
            if sev > T["min_signal_severity"] and m[i] > med:
                rows.append((sev, i, z))
        if rows:
            rows.sort(reverse=True)
            signals.append({
                "id": "margin_outlier",
                "severity": round(max(s for s, _, _ in rows), 3),
                "headline": (f"{len(rows)} job{'s' if len(rows) > 1 else ''} "
                             "claiming margin far above this contractor's "
                             "own norm"),
                "dollars": round(float(sum(V[i] - C[i] for _, i, _ in rows))),
                "jobs": [job(i, V[i] - C[i],
                             f"{m[i]:.1%} margin vs book median {med:.1%}")
                         for _, i, _ in rows[:5]],
                "why": ("Judged against this contractor's own bidding "
                        "history on this schedule, not an industry "
                        "benchmark. An outlier margin at mid-completion is "
                        "the classic shape of profit fade that has not been "
                        "recognized yet."),
            })

    signals.sort(key=lambda s: (s["severity"], s["dollars"]), reverse=True)
    return signals


_PROV_RANK = ["math-verified", "math-identified", "derived",
              "math-constrained-llm", "llm-only"]


def analyze_node(state: WippleState) -> dict:
    matrix = state.get("matrix")
    v = state.get("validation", {}) or {}
    labels = state.get("job_labels", [])
    if matrix is None or getattr(matrix, "size", 0) == 0:
        return {"analysis": {"kpis": None, "signals": [],
                             "basis": "none"}, "table": None}

    # Final mapping: validator's, swapped if disambiguation chose the rival,
    # or the LLM fallback for sparse documents.
    mapping = {int(k): val for k, val in (v.get("mapping") or {}).items()}
    basis = "validator"
    if v.get("competing_mapping") and \
       (state.get("disambiguation") or {}).get("chosen") == "competing":
        mapping = {int(k): val for k, val in v["competing_mapping"].items()}
        basis = "validator+disambiguation"
    if not mapping and state.get("fallback_mapping"):
        mapping = dict(state["fallback_mapping"])
        basis = "llm-headers"

    # Corrections are PROPOSALS, never applied server-side. The page loads
    # as-printed; the user applies suggestions explicitly (individually or
    # "apply all"), and the client recomputes figures and the CSV export.
    findings_list = v.get("findings", [])
    rows_n = matrix.shape[0]
    failing_rows = {f.get("row_index") for f in v.get("failures", [])}
    failing_rows |= {f.get("row_index") for f in findings_list}
    failing_rows.discard(None)
    pr = state.get("parse_report") or {}
    tc_cols = (pr.get("totals_check") or {}).get("columns") or {}
    col_map = state.get("numeric_col_map", [])

    def _totals_corroborates(mcol, observed, implied):
        j = col_map[mcol] if mcol < len(col_map) else None
        c = tc_cols.get(j) if j is not None else None
        if not c or c.get("matches"):
            return False
        return abs(abs(observed - implied) - abs(c["difference"])) \
            <= max(2.0, 0.01 * abs(c["difference"]))

    corrections = []
    work = matrix
    for f in findings_list:
        r, c, p = f.get("row_index"), f.get("culprit_column"), \
            f.get("proposed_correction")
        if r is None or c is None or p is None \
                or r >= matrix.shape[0] or c >= matrix.shape[1]:
            continue
        corrections.append({
            "row": int(r), "label": labels[r] if r < len(labels) else "",
            "col": int(c),
            "variable": mapping.get(int(c)),
            "printed": float(matrix[r, c]), "implied": float(p),
            "classification": f.get("classification"),
            "confidence": f.get("confidence"),
            "corroborated": _totals_corroborates(c, float(matrix[r, c]),
                                                 float(p)),
            "basis": list(f.get("correction_basis") or []),
            "checks": len(f.get("correction_basis") or []) or 1,
        })

    core, derived_vars = (reconstruct_core(work, mapping)
                          if mapping else (None, set()))

    if core is None:
        return {"analysis": {"kpis": None, "signals": [], "basis": basis},
                "table": _table(state, mapping)}

    # provenance per variable -> weakest constituent per KPI
    witnessed = {w["column"] for w in v.get("witnesses", [])
                 if w.get("column") is not None}
    var_prov = {}
    for c, var in mapping.items():
        if basis == "llm-headers":
            prior = ((v.get("diagnostics") or {})
                     .get("uncertified_best_mapping") or {})
            var_prov[var] = ("math-constrained-llm"
                             if prior.get(c) == var else "llm-only")
        else:
            var_prov[var] = ("math-verified" if c in witnessed
                             else "math-identified")
    for var in derived_vars:
        var_prov.setdefault(var, "derived")

    def kpi_prov(*vars_):
        ranks = [_PROV_RANK.index(var_prov.get(x, "derived"))
                 for x in vars_ if x in var_prov or x in derived_vars]
        return _PROV_RANK[max(ranks)] if ranks else "derived"

    kpis = compute_kpis(core)
    kpi_provenance = {
        "total_contract_value": kpi_prov("V"),
        "estimated_gross_profit": kpi_prov("V", "C"),
        "gp_pct": kpi_prov("V", "C"),
        "cost_to_complete": kpi_prov("C", "D"),
        "earned_revenue": kpi_prov("E"),
        "uegp": kpi_prov("V", "C", "E", "D"),
        "net_billing_position": kpi_prov("E", "B"),
    }
    signals = compute_signals(core, labels)
    return {
        "analysis": {"kpis": kpis, "kpi_provenance": kpi_provenance,
                     "signals": signals, "basis": basis,
                     "coverage": {"witnesses": len(v.get("witnesses", [])),
                                  "mapped_cols": len(mapping),
                                  "numeric_cols": int(matrix.shape[1]),
                                  "failing_rows": len(failing_rows),
                                  "rows": rows_n},
                     "corrections": corrections,
                     "jobs": _job_rows(core, labels),
                     "tuning": dict(TUNE)},
        "table": _table(state, mapping),
    }


def _job_rows(core, labels):
    """Per-job values (as analyzed, i.e. with auto corrections applied).
    Carries the raw variables so the client can recompute KPIs when the
    user accepts or rejects individual corrections.

    Every numeric field goes through _safe_float(). This was the one
    spot in the pipeline with no NaN guard at all: a blank/unparseable
    printed cell (when U/O is a direct mapped column) or a propagated
    upstream NaN (when U/O is derived from E/B) was reaching bare
    float(...) here and then json.dumps -- which by default happily
    emits the literal token NaN, breaking JSON.parse on the frontend.
    """
    V, C, D = core["V"], core["C"], core["D"]
    E, B = core.get("E"), core.get("B")
    with np.errstate(divide="ignore", invalid="ignore"):
        P = np.where(C > 0, D / C, 0.0)
        m = np.where(V > 0, (V - C) / V, 0.0)
    out = []
    for i, lab in enumerate(labels):
        row = {"label": lab, "V": _safe_float(V[i]), "C": _safe_float(C[i]),
               "D": _safe_float(D[i]), "P": round(float(P[i]), 4),
               "margin": round(float(m[i]), 4)}
        if E is not None:
            row["E"] = _safe_float(E[i])
        if B is not None:
            row["B"] = _safe_float(B[i])
        if E is not None and B is not None:
            net = _safe_float(B[i] - E[i])
            row["net"] = round(net) if net is not None else None
            if core.get("U") is not None:
                row["U"] = _safe_float(core["U"][i])
            if core.get("O") is not None:
                row["O"] = _safe_float(core["O"][i])
        out.append(row)
    return out


def _table(state, mapping):
    matrix = state.get("matrix")
    raw = state.get("raw_table") or {}
    headers = raw.get("headers", [])
    col_map = state.get("numeric_col_map", [])
    cols = []
    for mcol in range(len(col_map)):
        j = col_map[mcol]
        var = mapping.get(mcol)
        cols.append({
            "header": headers[j] if j < len(headers) else f"col {mcol}",
            "variable": var,
            "variable_name": VAR_NAMES.get(var) if var else None,
        })
    values = [[None if not np.isfinite(x) else float(x) for x in row]
              for row in matrix] if matrix is not None else []
    return {"job_labels": state.get("job_labels", []),
            "columns": cols, "values": values}
