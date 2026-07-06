"""
Header-blind validation engine for Completed Contracts schedules.

Same court, simpler case. The WIP engine's hypothesis enumeration is bespoke
to WIP anchors and stays untouched; this module supplies the CC schema's own
seed strategy -- which is nearly trivial because a CC table's entire
structure is additive triples:

1. DISCOVER: every ordered column triple (a, b, s) with col_a + col_b = col_s
   on >= ident_frac of rows (one bad row of slack, same anti-bug-1 guarantee).
2. ASSEMBLE: RT is the dominant column appearing as a sum. Its sum-triples
   split into the measure triple (KT, GT) -- the addend pair whose big-share
   sits stably in the cost band -- and the period triple (RP, RC), whose
   shares are unconstrained. Period slices then propagate through the
   remaining triples. Prior<->current is genuinely unresolvable by math, so
   the engine certifies the slice structure and emits BOTH readings plus a
   suggested disambiguator: the existing header-disambiguation organ answers
   one question, exactly as it does for WIP ties.
3. CERTIFY: every lattice identity re-checked on every row at dollar
   precision; violations become RowFailures, and single-culprit rows become
   Findings with proposed corrections classified by the shared OCR taxonomy.

Leftovers: a column ~= RT with no triple participation is Billed to Date
(identified, not verified -- provenance says so honestly); a pair closing
RR = RT - B certifies retainage. Everything else (year completed, memo
columns) participates in no identity and falls out unassigned, which is the
engine's existing definition of noise.

Dominance (Total >= components) is an orientation PRIOR only: per-row
violations -- negative current-year GP on a prior-year job is warranty/
claims fade -- surface as findings, never suppression. The exception to the
heuristic is an underwriting product.
"""

from __future__ import annotations

import numpy as np

from .schemas import CC_LATTICE, CC_VAR_NAMES, PERIOD_SWAP
from .wip_validator import (FAILED, INSUFFICIENT, SUCCESS, Config, Finding,
                            RowFailure, ValidationResult, Witness,
                            _classify_error)


def _fit_triple(a: np.ndarray, b: np.ndarray, s: np.ndarray, cfg: Config):
    """Rows where a + b = s within loose tolerance; returns (ok_mask, n_fin)."""
    resid = a + b - s
    fin = np.isfinite(resid)
    tol = np.maximum(cfg.ident_abs, cfg.ident_rel * np.abs(s))
    ok = fin & (np.abs(np.nan_to_num(resid)) <= np.nan_to_num(tol) + 1.0)
    return ok, int(fin.sum())


def _discover_triples(cols: np.ndarray, cfg: Config) -> list:
    """All (a, b, s) with col_a + col_b ~= col_s, a < b, robust fit."""
    n, m = cols.shape
    allowed_bad = max(1, int(np.floor((1 - cfg.ident_frac) * n)))
    out = []
    for s in range(m):
        for a in range(m):
            if a == s:
                continue
            for b in range(a + 1, m):
                if b == s:
                    continue
                ok, nfin = _fit_triple(cols[:, a], cols[:, b], cols[:, s], cfg)
                if nfin >= cfg.min_rows and nfin - int(ok.sum()) <= allowed_bad:
                    out.append((a, b, s))
    return out


def _median_share(cols, a, s):
    with np.errstate(divide="ignore", invalid="ignore"):
        r = cols[:, a] / cols[:, s]
    r = r[np.isfinite(r)]
    return float(np.median(r)) if r.size else np.nan


def _assemble(cols: np.ndarray, triples: list, cfg: Config):
    """Lattice assembly. Returns (mapping, competing, diagnostics) or None."""
    m = cols.shape[1]
    diag = {"triples": triples}
    if not triples:
        return None
    sums = {s for (_, _, s) in triples}
    med = [float(np.nanmedian(np.abs(cols[:, j]))) for j in range(m)]
    # RT: dominant magnitude among sum-side columns (dominance prior --
    # negative total cost/revenue is economic nonsense, so Total dominates).
    rt = max(sums, key=lambda j: med[j])
    rt_triples = [(a, b) for (a, b, s) in triples if s == rt]
    if not rt_triples:
        return None

    def _measure_reading(a, b):
        """(cost, gp) orientation of the pair, or None. A cost share is not
        merely IN the band on average -- it sits there on nearly every row
        and is portfolio-STABLE (tight IQR), the same two priors that orient
        the WIP estimate column. A period split's shares spread across 0..1
        job by job, so a mid-band MEDIAN with a wide spread is exactly the
        impostor this test exists to reject."""
        for big, small in ((a, b), (b, a)):
            with np.errstate(divide="ignore", invalid="ignore"):
                sh = cols[:, big] / cols[:, rt]
                sm = cols[:, small] / cols[:, rt]
            sh = sh[np.isfinite(sh)]
            if sh.size < cfg.min_rows:
                continue
            lo, hi = cfg.cost_ratio_band
            in_cost = np.mean((sh >= lo) & (sh <= hi))
            iqr = float(np.percentile(sh, 75) - np.percentile(sh, 25))
            # Candidacy rests on the BIG addend alone: robustly in the cost
            # band AND portfolio-stable. The small addend is deliberately
            # unconstrained -- retainage dips below any margin floor, and a
            # loss-heavy book prints negative GT; both are still measure
            # pairs. The period impostor is rejected by spread, not by the
            # small side.
            if in_cost >= cfg.prior_robust_frac and \
                    iqr <= cfg.estimate_iqr_max:
                return big, small
        return None

    measure_cands, period = [], None
    for (a, b) in rt_triples:
        reading = _measure_reading(a, b)
        if reading is not None:
            measure_cands.append((reading, _median_share(cols, reading[0],
                                                         rt)))
        else:
            period = (a, b)           # (RP, RC) -- order unknowable from math
    # B + RR = RT is structurally identical to KT + GT = RT. Economic
    # tiebreak: billed-to-date on closed jobs runs nearer RT than cost does,
    # so the LOWER big-addend share is the cost/gp pair; the other pair stays
    # unassigned here and _leftovers certifies it as (BC, RR).
    measure = min(measure_cands, key=lambda t: t[1])[0] if measure_cands \
        else None
    if len(measure_cands) > 1:
        diag["measure_tiebreak"] = [(p, round(s, 4)) for p, s in measure_cands]
    diag["rt"], diag["measure"], diag["period"] = rt, measure, period

    if measure is None and period is None:
        return None
    mapping = {rt: "RT"}
    if measure is not None:
        mapping[measure[0]], mapping[measure[1]] = "KT", "GT"
    if measure is not None and period is None:
        # 3-column (or no period splits printed): unambiguous, no competitor.
        return mapping, None, diag

    if period is not None:
        p1, p2 = period               # slice-1 vs slice-2, labels pending
        mapping[p1], mapping[p2] = "RP", "RC"
        # Propagate slices through remaining triples: KT = KP + KC picks the
        # cost-period pair; RP = KP + GP ties the prior slice together.
        kt = measure[0] if measure else None
        gt = measure[1] if measure else None
        for (a, b, s) in triples:
            if kt is not None and s == kt and {a, b}.isdisjoint(mapping):
                # orient by slice consistency: KP must close RP = KP + GP,
                # tested after both candidates placed; place provisionally
                # and let the slice check below swap if needed.
                mapping[a], mapping[b] = "KP", "KC"
            if gt is not None and s == gt and {a, b}.isdisjoint(mapping):
                mapping[a], mapping[b] = "GP", "GC"
        # Slice-consistency: within-slice measure triples (KP + GP = RP) must
        # hold; if the current-vs-prior assignment inside cost/gp splits is
        # crossed relative to the revenue split, swap the offending pair.
        inv = {v: k for k, v in mapping.items()}
        for kp, gp, rp in (("KP", "GP", "RP"), ("KC", "GC", "RC")):
            if kp in inv and gp in inv and rp in inv:
                ok, nfin = _fit_triple(cols[:, inv[kp]], cols[:, inv[gp]],
                                       cols[:, inv[rp]], cfg)
                bad = nfin - int(ok.sum())
                if nfin >= cfg.min_rows and bad > max(1, int(
                        np.floor((1 - cfg.ident_frac) * nfin))):
                    # crossed slices: swap this measure's period pair
                    a, b = inv[kp], inv[gp]
                    swap = PERIOD_SWAP
                    for j in (a, b):
                        mapping[j] = swap[mapping[j]]
                    inv = {v: k for k, v in mapping.items()}
        competing = {j: PERIOD_SWAP.get(v, v) for j, v in mapping.items()}
        return mapping, competing, diag
    return mapping, None, diag


def _leftovers(cols, mapping, triples, cfg):
    """Assign B (and RR when the retainage identity closes) from leftovers."""
    m = cols.shape[1]
    inv = {v: k for k, v in mapping.items()}
    rt = inv.get("RT")
    if rt is None:
        return
    left = [j for j in range(m) if j not in mapping]
    # RR = RT - B as a triple: (B, RR, RT)
    for (a, b, s) in triples:
        if s == rt and a in left and b in left:
            sa = _median_share(cols, a, rt)
            big, small = (a, b) if (np.isfinite(sa) and sa >= 0.5) else (b, a)
            mapping[big], mapping[small] = "BC", "RR"
            return
    for j in left:
        sh = _median_share(cols, j, rt)
        if np.isfinite(sh) and 0.80 <= sh <= 1.02:
            mapping[j] = "BC"          # identified by prior only, unwitnessed
            return


def _certify(cols, mapping, labels, cfg, row_index):
    """Strict per-row recheck of every closed lattice identity."""
    inv = {v: k for k, v in mapping.items()}
    witnesses, failures = [], []
    tol = cfg.money_obs_tol * 3 + cfg.cert_slack   # three observed cells
    for (va, vb, vs) in CC_LATTICE + [("BC", "RR", "RT")]:
        if not all(v in inv for v in (va, vb, vs)):
            continue
        a, b, s = inv[va], inv[vb], inv[vs]
        resid = cols[:, a] + cols[:, b] - cols[:, s]
        fin = np.isfinite(resid)
        if fin.sum() < cfg.min_rows:
            continue
        bad = fin & (np.abs(np.nan_to_num(resid)) > tol)
        rel = f"{vs} = {va} + {vb}"
        witnesses.append(Witness(
            relation=rel, business_form=rel, column=s,
            n_rows=int(fin.sum()), n_informative=int(fin.sum()),
            max_abs_residual=float(np.nanmax(np.abs(resid[fin]))
                                   if fin.any() else 0.0),
            weight=1.0, family=rel))
        for r in np.flatnonzero(bad):
            failures.append(RowFailure(
                relation=rel, business_form=rel, variable=vs, column=s,
                row_index=int(row_index[r]), row_label=labels[r],
                observed=float(cols[r, s]),
                expected=float(cols[r, a] + cols[r, b]),
                difference=float(resid[r]), tolerance=tol))
    return witnesses, failures


def _findings(cols, mapping, labels, cfg, failures, row_index):
    """Culprit isolation: on a failing row, each involved column is solved
    for from every failing identity; a column whose implied values agree
    across ALL that row's failing identities -- while the identities it does
    NOT appear in pass -- is the culprit, correction attached."""
    inv = {v: k for k, v in mapping.items()}
    rows = {}
    for f in failures:
        rows.setdefault(f.row_index, []).append(f)
    ridx = {int(orig): r for r, orig in enumerate(row_index)}
    out = []
    lattice = [t for t in CC_LATTICE + [("BC", "RR", "RT")]
               if all(v in inv for v in t)]
    for orig_r, fails in rows.items():
        r = ridx[orig_r]
        failing_rels = {f.relation for f in fails}
        candidates = {}
        for var in set(mapping.values()):
            involved = [t for t in lattice if var in t]
            f_in = [t for t in involved
                    if f"{t[2]} = {t[0]} + {t[1]}" in failing_rels]
            if not f_in or len(f_in) != len(failing_rels):
                continue                      # not in every failing identity
            passing = [t for t in involved if t not in f_in]
            implied = []
            for (va, vb, vs) in f_in:
                a, b, s = inv[va], inv[vb], inv[vs]
                if var == vs:
                    implied.append(cols[r, a] + cols[r, b])
                elif var == va:
                    implied.append(cols[r, s] - cols[r, b])
                else:
                    implied.append(cols[r, s] - cols[r, a])
            implied = [v for v in implied if np.isfinite(v)]
            if implied and max(implied) - min(implied) <= 1.02 and passing:
                candidates[var] = float(np.median(implied))
            elif implied and max(implied) - min(implied) <= 1.02:
                candidates[var] = float(np.median(implied))
        if len(candidates) == 1:
            var, proposed = next(iter(candidates.items()))
            col = inv[var]
            observed = float(cols[r, col])
            cls, detail = _classify_error(observed, proposed)
            out.append(Finding(
                row_index=orig_r, row_label=labels[r], culprit_column=col,
                culprit_variable=var, candidate_variables=[var],
                exonerated_variables=sorted(set(mapping.values()) - {var}),
                observed=observed, proposed_correction=proposed,
                correction_basis=sorted(failing_rels), confidence="high",
                classification=cls, classification_detail=detail,
                transplant_sources=[], failing_relations=sorted(failing_rels)))
        else:
            out.append(Finding(
                row_index=orig_r, row_label=labels[r], culprit_column=None,
                culprit_variable=None,
                candidate_variables=sorted(candidates),
                exonerated_variables=[], observed=None,
                proposed_correction=None, correction_basis=[],
                confidence="low", classification="unexplained_substitution",
                classification_detail="multiple identities failing without a "
                "single consistent culprit",
                transplant_sources=[], failing_relations=sorted(failing_rels)))
    return out


def validate_cc(columns, job_labels=None, config=None) -> ValidationResult:
    cfg = config or Config()
    cols = np.asarray(columns, dtype=float)
    if cols.ndim != 2 or cols.shape[0] < cfg.min_rows:
        return ValidationResult(status=INSUFFICIENT,
                                reason="too few rows for CC identification")
    n, m = cols.shape
    labels = list(job_labels or [f"Row {i+1}" for i in range(n)])
    row_index = np.arange(n)

    triples = _discover_triples(cols, cfg)
    asm = _assemble(cols, triples, cfg)
    if asm is None:
        return ValidationResult(
            status=INSUFFICIENT,
            reason="no certifiable additive lattice among the columns",
            diagnostics={"triples": triples})
    mapping, competing, diag = asm
    _leftovers(cols, mapping, triples, cfg)
    witnesses, failures = _certify(cols, mapping, labels, cfg, row_index)
    findings = _findings(cols, mapping, labels, cfg, failures, row_index) \
        if failures else []

    named = {j: CC_VAR_NAMES[v] for j, v in mapping.items()}
    core = {v: cols[:, j] for j, v in mapping.items()}
    diag["schema"] = "cc"
    diag["explained_fraction"] = len(mapping) / m
    if failures:
        status, reason = FAILED, (f"{len(failures)} row-level identity "
                                  "violation(s) at dollar precision")
    elif competing is not None:
        status = INSUFFICIENT
        reason = ("period slices certified but prior-year vs current-year "
                  "order is not decidable from the numbers")
    elif not witnesses:
        status, reason = INSUFFICIENT, ("mapping placed by priors only; no "
                                        "identity closed an evidence cycle")
    else:
        status, reason = SUCCESS, (f"{len(witnesses)} lattice identities "
                                   "witnessed on every row")
    return ValidationResult(
        status=status, reason=reason, mapping=mapping, mapping_named=named,
        estimate_orientation="", virtuals={}, core=core, row_index=row_index,
        witnesses=witnesses, failures=failures, findings=findings,
        competing_mapping=competing,
        suggested_disambiguator=(None if competing is None else
                                 "which period columns are PRIOR YEARS vs "
                                 "CURRENT YEAR (slice A = mapping, slice B = "
                                 "competing)"),
        diagnostics=diag)
