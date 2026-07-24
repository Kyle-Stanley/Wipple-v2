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
    # Billing-position signals are ratios against what is LEFT of the job:
    # underbilling vs revenue left to earn, overbilling vs cost left to
    # spend. Ratio 1.0 means the imbalance fully consumes the remainder --
    # "underbilling% + completion% > 1" falls out as the ub ratio crossing 1.
    "ub_ratio_floor": 0.50,    # U/(V-E) where trapped-cash severity starts
    "ub_ratio_full": 1.00,     # ...and saturates (no room left to bill it)
    "ob_ratio_floor": 0.15,    # O/(C-D) where job-borrow severity starts
    "ob_ratio_full": 0.75,     # ...and saturates
    "min_flag_dollars": 10_000,  # imbalances below this never flag
    "loss_margin_full": 0.05,  # negative margin that saturates loss severity
    "overrun_full": 0.10,      # (D-C)/C that saturates overrun severity
    "early_p": 0.15,           # "early stage" completion cutoff
    "early_share_floor": 0.25, # portfolio share where concentration starts
    "early_share_full": 0.70,  # ...and where it saturates
    "outlier_z_floor": 2.0,    # robust z where margin outlier starts
    "outlier_z_full": 5.0,
    "thin_margin_p": 0.05,     # margin under which backlog counts as fragile
    "thin_share_floor": 0.20,  # share of CTC in thin jobs where sev starts
    "thin_share_full": 0.60,
    "big_job_floor": 0.20,     # largest-job share of TCV where sev starts
    "big_job_full": 0.50,
    "fade_gap_full": 0.08,     # (est final margin - earned margin) saturation
    "fade_min_p": 0.35,        # completion below which the fade proxy is noise
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
        # concentration: the two shares every underwriter asks for anyway
        "largest_job_share": (float(np.nanmax(V)) / tcv if tcv else None),
        "top5_share": (float(np.nansum(np.sort(np.nan_to_num(V))[-5:])) / tcv
                       if tcv else None),
    }
    return out


def compute_signals(core, labels, derived=frozenset()):
    V, C, D = core["V"], core["C"], core["D"]
    with np.errstate(divide="ignore", invalid="ignore"):
        P = np.where(C > 0, D / C, 0.0)
        m = np.where(V > 0, (V - C) / V, 0.0)
    E = core.get("E")
    U = core.get("U")
    O = core.get("O")
    n = len(labels)
    T = TUNE
    signals = []

    def job(i, dollars, detail):
        return {"label": labels[i], "dollars": round(float(dollars)),
                "detail": detail}

    # -- trapped cash: underbilling vs the revenue left to earn ---------------
    # Severity is U / (V - E): the fraction of the job's remaining revenue
    # that the catch-up billing would consume. At ratio 1 there is literally
    # not enough job left to bill it through -- equivalently, the shorthand
    # "underbilling% + completion% > 1". Completion needs no separate weight;
    # a shrinking denominator IS the late-stage escalation, and it cannot
    # promote pocket change (dollar floor) or early-stage timing noise.
    if U is not None and E is not None:
        rows = []
        for i in range(n):
            if U[i] < T["min_flag_dollars"]:
                continue
            rem_rev = float(V[i] - E[i])
            if P[i] >= .995 or rem_rev <= max(1.0, .005 * abs(float(V[i]))):
                continue
            ratio = float(U[i]) / rem_rev
            sev = _clamp((ratio - T["ub_ratio_floor"])
                         / (T["ub_ratio_full"] - T["ub_ratio_floor"]))
            if sev > T["min_signal_severity"]:
                rows.append((sev, i, ratio))
        if rows:
            rows.sort(reverse=True)
            dollars = sum(U[i] for _, i, _ in rows)
            k = len(rows)
            signals.append({
                "id": "trapped_cash",
                "severity": round(max(s for s, _, _ in rows), 3),
                "headline": ("Significant under billings with limited time "
                             "to recover"),
                "dollars": round(float(dollars)),
                "jobs": [job(i, U[i],
                             f"{P[i]:.0%} complete, {_money(U[i])} underbilled "
                             f"= {ratio:.0%} of remaining revenue")
                         for _, i, ratio in rows[:5]],
                "why": ("Earned revenue the contractor has not billed. On "
                        "nearly-finished work this usually means unapproved "
                        "change orders or receivables that may not convert "
                        "to cash."),
            })
    # -- job borrow: overbilling vs the cost left to finish -------------------
    # Severity is O / (C - D): what fraction of the remaining work is being
    # funded by cash already collected (and typically already spent). Early
    # front-loading -- large O against a huge CTC -- correctly scores near
    # zero; it is normal and usually GOOD for the surety. A job that is
    # overbilled AND already past its cost estimate has no denominator left
    # and saturates outright.
    if O is not None:
        rows = []
        for i in range(n):
            if O[i] < T["min_flag_dollars"]:
                continue
            ctc = float(C[i] - D[i])
            ratio = float(O[i]) / ctc if ctc > 0 else float("inf")
            sev = _clamp((ratio - T["ob_ratio_floor"])
                         / (T["ob_ratio_full"] - T["ob_ratio_floor"]))
            if sev > T["min_signal_severity"]:
                rows.append((sev, i, min(ratio, 99.0)))
        if rows:
            rows.sort(reverse=True)
            dollars = sum(O[i] for _, i, _ in rows)
            signals.append({
                "id": "job_borrow",
                "severity": round(max(s for s, _, _ in rows), 3),
                "headline": ("Significant over billings vs. Cost to "
                             "Complete"),
                "dollars": round(float(dollars)),
                "jobs": [job(i, O[i],
                             f"{P[i]:.0%} complete, {_money(O[i])} overbilled "
                             f"= {ratio:.0%} of cost to finish")
                         for _, i, ratio in rows[:5]],
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

    # -- fragile backlog: remaining work riding on thin margin ----------------
    # Loss jobs (m < 0) are excluded -- they have their own signal above.
    ctc_all = np.maximum(C - D, 0.0)
    total_ctc = float(np.nansum(ctc_all))
    thin = (m >= 0) & (m < T["thin_margin_p"]) & (ctc_all > 0)
    if total_ctc > 0 and thin.any():
        thin_ctc = float(np.nansum(ctc_all[thin]))
        share = thin_ctc / total_ctc
        sev = _clamp((share - T["thin_share_floor"])
                     / (T["thin_share_full"] - T["thin_share_floor"]))
        if sev > T["min_signal_severity"]:
            order = sorted(np.where(thin)[0], key=lambda i: -ctc_all[i])
            signals.append({
                "id": "thin_margin_backlog",
                "severity": round(sev, 3),
                "headline": (f"{share:.0%} of remaining cost sits on jobs "
                             f"with margin under {T['thin_margin_p']:.0%}"),
                "dollars": round(thin_ctc),
                "jobs": [job(i, ctc_all[i],
                             f"{m[i]:.1%} margin, {_money(ctc_all[i])} "
                             "still to build")
                         for i in order[:5]],
                "why": ("Thin-margin work has no cushion: a small overrun "
                        "flips it to a loss. When much of the remaining "
                        "book is fragile, one bad quarter can erase the "
                        "schedule's stated profit."),
            })

    # -- single-job concentration ----------------------------------------------
    if tcv > 0 and n >= 2:
        top = int(np.nanargmax(V))
        share = float(V[top]) / tcv
        sev = _clamp((share - T["big_job_floor"])
                     / (T["big_job_full"] - T["big_job_floor"]))
        if sev > T["min_signal_severity"]:
            signals.append({
                "id": "job_concentration",
                "severity": round(sev, 3),
                "headline": (f"Largest job is {share:.0%} of the program"),
                "dollars": round(float(V[top])),
                "jobs": [job(top, V[top],
                             f"{P[top]:.0%} complete, {m[top]:.1%} margin")],
                "why": ("The program's outcome is coupled to one job. "
                        "Whatever happens on it -- fade, dispute, slow pay "
                        "-- happens to the contractor."),
            })

    # -- unrecognized fade proxy: earned margin lagging the estimate ----------
    # Only meaningful when E is a PHYSICAL column: with E derived cost-to-cost
    # (E = V*D/C), earned-to-date margin equals estimated margin identically
    # and this comparison is a tautology. When the contractor's own revenue
    # recognition runs below the stated final margin on well-progressed work,
    # the schedule is implicitly claiming the REMAINING work will be more
    # profitable than the work done so far -- the classic shape of fade that
    # has not been booked yet. Single-document stand-in until multi-period
    # WIPs land; the real fade analysis compares schedules across time.
    if E is not None and "E" not in derived:
        rows = []
        for i in range(n):
            if P[i] < T["fade_min_p"] or E[i] <= 0:
                continue
            earned_m = float((E[i] - D[i]) / E[i])
            gap = float(m[i]) - earned_m
            sev = _clamp(P[i]) * _clamp(gap / T["fade_gap_full"])
            if sev > T["min_signal_severity"] \
                    and gap * E[i] >= T["min_flag_dollars"]:
                rows.append((sev, i, earned_m, gap))
        if rows:
            rows.sort(reverse=True)
            dollars = sum(g * E[i] for _, i, _, g in rows)
            signals.append({
                "id": "unrecognized_fade",
                "severity": round(max(s for s, *_ in rows), 3),
                "headline": (f"{len(rows)} job{'s' if len(rows) > 1 else ''} "
                             "earning below the stated final margin"),
                "dollars": round(float(dollars)),
                "jobs": [job(i, g * E[i],
                             f"{P[i]:.0%} complete, earned {em:.1%} to date "
                             f"vs {m[i]:.1%} estimated final")
                         for _, i, em, g in rows[:5]],
                "why": ("For the stated margin to hold, the remaining work "
                        "must out-earn everything built so far. Margin "
                        "estimates that survive on the back half of a job "
                        "are rare; this is where fade hides before it is "
                        "recognized."),
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

    if (v.get("schema") == "cc"):
        return _analyze_cc(matrix, v, labels)

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

    # Corrections are PROPOSALS and never mutate the server-side source table.
    # The review UI includes supported proposals by default, lets the reviewer
    # restore any printed value, and recomputes figures and exports from those
    # reversible choices.
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
            "variable": mapping.get(int(c)) or f.get("culprit_variable"),
            "printed": float(matrix[r, c]), "implied": float(p),
            "classification": f.get("classification"),
            "confidence": f.get("confidence"),
            "proof_kind": f.get("proof_kind", "direct"),
            "corroborated": _totals_corroborates(c, float(matrix[r, c]),
                                                 float(p)),
            "basis": list(f.get("correction_basis") or []),
            "checks": len(f.get("correction_basis") or []) or 1,
        })

    # Totals are downstream checks, never correction anchors. Re-evaluate
    # each stated total against a working column sum after every supported row
    # correction in that column. This handles multiple repaired cells in one
    # column and prevents a bad printed total from suppressing valid row math.
    totals_after_corrections = {}
    for raw_col, total in tc_cols.items():
        try:
            raw_j = int(raw_col)
        except (TypeError, ValueError):
            continue
        matrix_cols = [i for i, original in enumerate(col_map)
                       if int(original) == raw_j]
        if not matrix_cols:
            continue
        matrix_col = matrix_cols[0]
        column_corrections = [
            c for c in corrections if c["col"] == matrix_col]
        corrected = float(total.get("computed", 0.0)) + sum(
            c["implied"] - c["printed"] for c in column_corrections)
        stated = total.get("stated")
        if stated is None:
            continue
        tolerance = max(1.0, 0.51 * rows_n + 1.0,
                        1e-9 * abs(corrected))
        matches = abs(float(stated) - corrected) <= tolerance
        totals_after_corrections[raw_j] = {
            "stated": float(stated),
            "computed_from_printed_rows": float(total.get("computed", 0.0)),
            "computed_after_corrections": round(corrected, 2),
            "difference_after_corrections": round(
                float(stated) - corrected, 2),
            "matches_after_corrections": bool(matches),
            "row_corrections": len(column_corrections),
        }
        before_gap = abs(float(stated) - float(total.get("computed", 0.0)))
        after_gap = abs(float(stated) - corrected)
        if column_corrections and matches and after_gap + 1.0 < before_gap:
            for correction in column_corrections:
                correction["corroborated"] = True

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
    signals = compute_signals(core, labels, derived=derived_vars)
    return {
        "analysis": {"kpis": kpis, "kpi_provenance": kpi_provenance,
                     "signals": signals, "basis": basis,
                     "coverage": {"witnesses": len(v.get("witnesses", [])),
                                  "mapped_cols": len(mapping),
                                  "numeric_cols": int(matrix.shape[1]),
                                  "failing_rows": len(failing_rows),
                                  "rows": rows_n},
                     "corrections": corrections,
                     "totals_after_corrections": totals_after_corrections,
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


def _analyze_cc(matrix, v, labels):
    """Completed-contract analysis: totals, realized margin, and the one
    signal unique to closed work -- profit recognized in prior years given
    back in the current year (fade on completed jobs: warranty, claims,
    late costs). reconstruct_core is WIP-shaped; CC needs none of it."""
    mapping = {int(k): val for k, val in (v.get("mapping") or {}).items()}
    inv = {val: k for k, val in mapping.items()}
    col = lambda code: (matrix[:, inv[code]] if code in inv else None)
    RT, KT, GT = col("RT"), col("KT"), col("GT")
    GC = col("GC")
    kpis, signals = {}, []
    if RT is not None:
        kpis["total_revenue"] = float(np.nansum(RT))
    if KT is not None:
        kpis["total_cost"] = float(np.nansum(KT))
    if GT is not None:
        kpis["total_gross_profit"] = float(np.nansum(GT))
        if RT is not None and np.nansum(RT):
            kpis["realized_margin"] = float(np.nansum(GT) / np.nansum(RT))
        for i in np.flatnonzero(np.nan_to_num(GT) < 0):
            signals.append({
                "signal": "loss_on_completed_contract",
                "job": labels[i] if i < len(labels) else f"Row {i+1}",
                "severity": 0.9,
                "detail": f"contract closed at a loss of {_money(-GT[i])}"})
    if GC is not None:
        for i in np.flatnonzero(np.nan_to_num(GC) < 0):
            sev = _clamp(-float(GC[i]) / (abs(float(GT[i])) + 1.0)
                         if GT is not None else 0.5)
            if sev >= TUNE["min_signal_severity"]:
                signals.append({
                    "signal": "profit_fade_on_completed_work",
                    "job": labels[i] if i < len(labels) else f"Row {i+1}",
                    "severity": round(sev, 3),
                    "detail": f"{_money(-GC[i])} of previously recognized "
                              "profit given back in the current year "
                              "(warranty/claims/late costs on closed work)"})
    return {"analysis": {"kpis": kpis or None, "signals": signals,
                         "basis": "validator", "schema": "cc"},
            "table": _table_cc(matrix, mapping, labels)}


def _table_cc(matrix, mapping, labels):
    from .schemas import CC_VAR_NAMES
    cols = [{"index": int(k), "variable": val,
             "name": CC_VAR_NAMES.get(val, val)}
            for k, val in sorted(mapping.items())]
    rows = []
    for i in range(matrix.shape[0]):
        rows.append({"label": labels[i] if i < len(labels) else f"Row {i+1}",
                     "values": {val: _safe_float(matrix[i, k])
                                for k, val in mapping.items()}})
    return {"columns": cols, "rows": rows}
