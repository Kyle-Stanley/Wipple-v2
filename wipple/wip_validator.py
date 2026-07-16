"""
wip_validator.py
================
Header-blind validation engine for contractor Work-in-Progress (WIP) schedules.

Input: a rectangular table of numeric columns (post table-extraction). Headers
are never read. The engine decides, from the numbers alone, which columns
realize which WIP variables, and whether the table's own values prove it is
internally consistent.

Architecture
------------
1.  The WIP formula universe is a registry of directed rules over the variable
    set {V, C, G, M, D, Q, P, E, B, H, N, U, O, R, RB, PB}. Each rule is
    tagged with an algebraic *family*: rules in the same family are
    consequences of one underlying identity (e.g. N = E-B, U = max(E-B,0),
    O = max(B-E,0) and the implied E = B+U-O all express the single
    net-billing-position cycle).

2.  A *hypothesis* seeds the anchors that cannot be peeled from nothing: a
    Contract Value column V, one physical estimate column X (oriented as
    either C or G; the complement is constructed virtually), and the two
    progress anchors D (cost to date) and B (billings to date). Anchor
    placements are enumerated jointly (never greedily -- evidence for D may
    flow only through the billing side) under cheap economic priors, then
    judged purely by the evidence each placement accumulates.

3.  Identification = peeling (erasure-style decoding). Starting from the
    seeds, any rule whose inputs are all known *predicts* a column vector for
    its unknown output. The prediction is matched against the unassigned
    physical columns under a robust per-row tolerance. A hit assigns the
    column and propagates. Only when a full matching pass stalls is ONE
    still-unknown variable materialized as a virtual node, which may unlock
    further matching. Virtuals are therefore a last resort by construction: a
    physical column always gets first claim on a variable. Because a
    variable's predictor is always built from previously-known variables, the
    predictor of a column can never contain that column: an anchor cannot
    witness itself (the latent coordinates alpha = C/V etc. are derived only
    from the hypothesized seed columns).

4.  Validation = redundancy. Every successful match closes an independent
    cycle in the grounded evidence graph: the prediction path (through the
    seed anchors) and the observed column are two independent routes to the
    same numbers. Matches/checks that close the SAME algebraic cycle (same
    family) are merged and count as ONE unit of evidence -- this is the
    cyclomatic-rank counting (not formula counting), and it is exactly why
    the implied check E = B + U - O contributes nothing once U and O were
    matched: its family already holds an edge. A table is *validatable* only
    if each progress anchor (D and B) lies on at least one evidence cycle. A
    bare {V, C, D, B} core peels everything into virtuals, accumulates zero
    evidence edges, and is therefore `insufficient_information_for_validation`
    -- an exactly-determined system certifies nothing.

5.  Two-phase tolerance. Identification is robust: a column is placed if it
    fits a loose per-row tolerance on all but a small allowed number of rows
    (always at least one row of slack, so a single bad cell in a 5-row table
    cannot eject the true column into a virtual node -- anti-bug 1).
    Certification is strict: every accepted relation is re-checked on every
    row at propagated dollar precision (money) or display-grid precision
    (percents). A ~$37 misprint therefore surfaces as `validation_failed`
    with the exact row, observed, expected, and signed difference.

Only dependency: numpy.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, Optional

import numpy as np

SUCCESS = "success"
INSUFFICIENT = "insufficient_information_for_validation"
FAILED = "validation_failed"


# ---------------------------------------------------------------------------
# Variable registry
# ---------------------------------------------------------------------------

VAR_NAMES = {
    "V":  "Contract Value",
    "C":  "Estimated Total Cost",
    "G":  "Estimated Gross Profit",
    "M":  "Estimated Gross Margin %",
    "D":  "Cost to Date",
    "Q":  "Cost to Complete",
    "P":  "Percent Complete",
    "E":  "Earned Revenue",
    "B":  "Billings to Date",
    "H":  "Earned Gross Profit to Date",
    "N":  "Net Billing Position",
    "U":  "Underbillings",
    "O":  "Overbillings",
    "R":  "Remaining Revenue (Backlog)",
    "RB": "Remaining Billings",
    "PB": "Percent Billed",
}

CORE_VARS = ("V", "C", "G", "D", "B")

# U and O may be represented as either positive magnitudes or signed amounts
# depending on the schedule. Their semantic value is therefore their
# magnitude: sign is ignored for identification, certification, and auditing.
MAGNITUDE_PRESENTATION_VARS = frozenset({"U", "O"})

# Percent display grids considered, COARSEST FIRST (ratio space). Detection
# must return the coarsest grid the data satisfies (anti-bug 5): [0.5,0.6,0.7]
# lives on the 0.1 grid even though it also lies on the 0.01 grid, and percent
# certification must not be stricter than the visible display precision.
GRIDS = (0.1, 0.05, 0.025, 0.02, 0.01, 0.005, 0.0025, 0.002,
         0.001, 0.0005, 0.00025, 0.0001, 0.00005, 0.00001)


# ---------------------------------------------------------------------------
# Configuration: every threshold, tolerance and economic prior lives here.
# ---------------------------------------------------------------------------

@dataclass
class Config:
    # ----- identification (robust phase) -----------------------------------
    # A column is placed if it fits the loose tolerance on all but
    # max(1, floor((1-ident_frac)*rows)) rows. The floor of one allowed bad
    # row is the structural guarantee behind anti-bug 1: a single corrupted
    # cell in a 5-row table (an 80% fit) still places the true physical
    # column, which then FAILS strict certification instead of being silently
    # replaced by a virtual node.
    ident_frac: float = 0.90
    ident_rel: float = 0.01      # loose money tolerance: 1% of predicted value
    ident_abs: float = 1.0       # ...but never tighter than $1
    pct_ident_slack: float = 0.02   # loose percent tolerance: +/- 2 points
    # Shadow audit (money only): after a winner is selected, an unassigned
    # physical column that STRICTLY matches one of the winner's virtual
    # variables on >= shadow_audit_frac of rows proves the virtual stood in
    # while a physical column was the better explanation (anti-bug 1, heavy
    # corruption arm) -> validation_failed on the disagreeing rows, never a
    # silent re-route. Money only: percent vectors are too collision-prone
    # in low dimension for majority-claims to be safe.
    shadow_audit_frac: float = 0.55
    min_rows: int = 3            # fewer usable rows than this => insufficient
    min_informative_rows: int = 2   # clipped witnesses (U, O) need this many
    # nonzero-expected rows to carry evidence: an all-zeros column must not
    # witness an all-underbilled portfolio's overbillings.

    # ----- certification (strict phase) -------------------------------------
    money_obs_tol: float = 0.51  # table money assumed rounded to whole dollars
    cert_slack: float = 0.75     # additive slack on top of propagated rounding
    cert_money_rel: float = 1e-9   # float-noise relative component
    pct_grid_mult: float = 1.0   # percent tolerance = grid * mult (covers
    # truncation, whose error is strictly less than one grid step)
    pct_default_tol: float = 5e-7   # percent column with no detectable grid

    # A failed identity may create a finding, but an ordinary replacement
    # value is suggested only when this many independent algebraic families
    # agree. Exact U/O sign reversals are the narrow exception.
    correction_min_families: int = 2

    # ----- evidence weights --------------------------------------------------
    # Percent identities are weaker evidence than money identities (anti-bug
    # 7): low-dimensional ratio vectors collide far more easily than
    # dollar-precise money vectors, so they are down-weighted, and scaled down
    # further on low-row-count tables.
    pct_weight: float = 0.4
    small_n: int = 8
    assigned_bonus: float = 0.05    # mild preference for explaining more columns
    ambiguity_margin: float = 0.8   # runner-up with a DIFFERENT semantic core
    # scoring >= 80% of the winner => irreducibly ambiguous => insufficient

    # ----- economic priors (the symmetry-breaking observables) --------------
    # V is the normalization axis and is usually among the largest positive
    # money columns portfolio-wide. Used to SHORTLIST candidates, never as
    # proof; the shortlist is expanded if no validatable hypothesis survives.
    v_shortlist: int = 3
    expand_v_on_fail: bool = True
    # The single physical estimate column X is oriented by two priors:
    #   band      -- a contractor's estimated-cost ratio C/V sits high (well
    #                above ~50% of contract value); gross margin G/V sits low.
    #   stability -- a true estimate ratio is portfolio-stable (tight spread
    #                across jobs), whereas progress-bearing columns
    #                (D/V = alpha*P, E/V = P, B/V = PB) spread across 0..1.
    # These are exactly the observables that break the (C,D)<->(G,H) mirror
    # and orient the estimate column; prior rejections are recorded in
    # diagnostics.
    cost_ratio_band: tuple = (0.45, 1.02)
    margin_band: tuple = (0.02, 0.45)
    estimate_iqr_max: float = 0.25
    # Anchor pruning: D should not exceed estimated cost by much; B should not
    # exceed contract value by much. Applied robustly (prior_robust_frac of
    # rows), never as proof. Note D <= C is also what breaks the
    # (V,D) <-> (E,C) pair-swap symmetry of the bilinear identity E*C = V*D.
    d_over_c_slack: float = 1.10
    b_over_v_slack: float = 1.25
    # Scale-free liveness prior: on a WIP of active jobs the MEDIAN job has
    # incurred a non-negligible share of its estimated cost and billed a
    # non-negligible share of its value; a column whose portfolio median is
    # ~0-2% of the denominator (e.g. an Overbillings column auditioning as
    # Cost to Date) is not a progress anchor. Ratio-based, not
    # magnitude-based, so small-dollar money columns remain eligible
    # (anti-bug 3).
    anchor_live_med: float = 0.02
    prior_robust_frac: float = 0.90
    max_anchor_pairs: int = 80


# ---------------------------------------------------------------------------
# Formula universe as directed rules tagged with algebraic families
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Rule:
    name: str                 # human-readable identity, canonical orientation
    out: str                  # output variable
    ins: tuple                # input variables
    fn: Callable              # vectorized evaluator
    family: str               # algebraic cycle family (for evidence merging)
    kind: str                 # 'money' (degree 1 in V) or 'pct' (degree 0)
    clipped: bool = False     # max(.,0)-style rule (informative-row guard)


def _rules() -> list:
    R = Rule
    return [
        # --- estimate complement: G = V - C  <=>  C = V - G ------------------
        R("G = V - C", "G", ("V", "C"), lambda V, C: V - C,
          "est_comp", "money"),
        R("C = V - G", "C", ("V", "G"), lambda V, G: V - G,
          "est_comp", "money"),
        # --- the bilinear earned-revenue identity E*C = V*D ------------------
        R("E = V x D / C", "E", ("V", "D", "C"),
          lambda V, D, C: V * D / C, "earned", "money"),
        # --- cost completion: Q = C - D --------------------------------------
        R("Q = C - D", "Q", ("C", "D"), lambda C, D: C - D,
          "cost_complete", "money"),
        # --- earned profit: H = E - D = G*D/C (same identity, two spellings) -
        R("H = E - D", "H", ("E", "D"), lambda E, D: E - D,
          "earned_profit", "money"),
        R("H = G x D / C", "H", ("G", "D", "C"),
          lambda G, D, C: G * D / C, "earned_profit", "money"),
        # --- net billing position: ONE cycle, several spellings --------------
        # N = E-B, U = max(E-B,0), O = max(B-E,0), and the implied
        # reconstructions E = B+U-O / B = E-U+O are all the SAME algebraic
        # cycle; family merging guarantees they contribute one independent
        # unit of evidence, not three (see _merge_families).
        R("N = E - B", "N", ("E", "B"), lambda E, B: E - B,
          "billing_pos", "money"),
        R("U = max(E - B, 0)", "U", ("E", "B"),
          lambda E, B: np.maximum(E - B, 0.0), "billing_pos", "money",
          clipped=True),
        R("O = max(B - E, 0)", "O", ("E", "B"),
          lambda E, B: np.maximum(B - E, 0.0), "billing_pos", "money",
          clipped=True),
        # --- backlog: R = V - E = V(1-P) = RB - N (one family) ---------------
        R("R = V - E", "R", ("V", "E"), lambda V, E: V - E,
          "backlog", "money"),
        # --- remaining billings ----------------------------------------------
        R("RB = V - B", "RB", ("V", "B"), lambda V, B: V - B,
          "rem_billing", "money"),
        # --- percent-grade identities (degree 0; weaker evidence) ------------
        R("M = G / V", "M", ("G", "V"), lambda G, V: G / V, "margin", "pct"),
        R("P = D / C", "P", ("D", "C"), lambda D, C: D / C,
          "pct_complete", "pct"),
        R("P = E / V", "P", ("E", "V"), lambda E, V: E / V,
          "pct_complete", "pct"),
        R("PB = B / V", "PB", ("B", "V"), lambda B, V: B / V,
          "pct_billed", "pct"),
        # Money-from-percent forms (E = V*P, D = C*P, Q = C*(1-P), B = V*PB,
        # G = V*M, C = V*(1-M), R = V*(1-P), ...) are algebraically inside the
        # families above once the seeds are fixed, and are deliberately NOT
        # separate rules: counting them would double-count cycles the percent
        # rules above already close.
    ]


RULES = _rules()
# Money rules first: dollar-precise matches must claim columns before percent
# matches can steal one on a low-dimensional coincidence.
RULES_ORDERED = sorted(RULES, key=lambda r: 0 if r.kind == "money" else 1)


# ---------------------------------------------------------------------------
# Data carriers
# ---------------------------------------------------------------------------

@dataclass
class VarVal:
    """A known variable: its per-row values (natural units: dollars for money,
    0..1 ratios for percents), per-row observation/derivation uncertainty,
    and the set of physical columns its values were computed from."""
    var: str
    values: np.ndarray
    tol: np.ndarray
    support: frozenset
    col: Optional[int] = None          # physical column index, if observed
    interp_scale: float = 1.0          # 100.0 if column stored as whole-percent
    grid: Optional[float] = None       # detected percent display grid (ratio)
    derivation: str = ""
    defining_family: Optional[str] = None  # family that materialized a virtual
    deps: frozenset = frozenset()      # grounded VARIABLE dependencies:
    # {var} for an observed column; the transitive union of its inputs'
    # deps for a virtual. A failing relation implicates its grounded deps,
    # not its immediate inputs -- corruption propagates through virtuals.


@dataclass
class Edge:
    """One piece of evidence: a prediction that matched a physical column, or
    a cross-family check among already-known variables."""
    rule: Rule
    out_var: str
    col: Optional[int]
    pred_support: frozenset            # physical columns feeding the predictor
    support: frozenset                 # pred_support U observed column
    kind: str
    weight: float
    informative: int
    bad_rows: int
    n_rows: int
    max_resid: float                   # max |obs - pred| over matching rows
    is_check: bool = False


@dataclass
class Witness:
    relation: str
    business_form: str
    column: Optional[int]
    n_rows: int
    n_informative: int
    max_abs_residual: float
    weight: float
    family: str


@dataclass
class RowFailure:
    relation: str
    business_form: str
    variable: str
    column: Optional[int]
    row_index: int                     # index into the ORIGINAL table
    row_label: str
    observed: float
    expected: float
    difference: float                  # observed - expected (signed)
    tolerance: float


@dataclass
class Hypothesis:
    v_col: int
    x_col: int
    orientation: str                   # 'C' or 'G'
    d_col: int
    b_col: int
    key: tuple = ()
    known: dict = field(default_factory=dict)
    edges: list = field(default_factory=list)
    families: dict = field(default_factory=dict)  # family -> (support, weight)
    corr_d: int = 0
    corr_b: int = 0
    evidence: float = 0.0
    score: float = 0.0
    n_assigned: int = 0
    row_index: Optional[np.ndarray] = None


@dataclass
class Finding:
    """Cell-level diagnosis distilled from a row's identity violations:
    which cell is the probable culprit, what value the witnessed identities
    jointly imply for it, and what kind of error the observed/implied pair
    looks like (OCR character misread, separator/magnitude slip, extra
    character such as a currency symbol read as a digit, a value
    transplanted from a neighboring cell, or a flatly wrong number)."""
    row_index: int
    row_label: str
    culprit_column: Optional[int]
    culprit_variable: Optional[str]
    candidate_variables: list
    exonerated_variables: list
    observed: Optional[float]
    proposed_correction: Optional[float]
    correction_basis: list          # relations that independently imply it
    confidence: str                 # 'high' | 'medium' | 'low'
    classification: str
    classification_detail: str
    transplant_sources: list        # [(row_index, col)] cells equal to observed
    failing_relations: list


@dataclass
class ValidationResult:
    status: str                        # SUCCESS | INSUFFICIENT | FAILED
    reason: str = ""
    mapping: dict = field(default_factory=dict)        # col index -> var code
    mapping_named: dict = field(default_factory=dict)  # col index -> name
    estimate_orientation: str = ""
    virtuals: dict = field(default_factory=dict)       # var -> derivation
    core: dict = field(default_factory=dict)           # V/C/G/D/B value arrays
    row_index: Optional[np.ndarray] = None             # rows the core covers
    witnesses: list = field(default_factory=list)
    failures: list = field(default_factory=list)
    findings: list = field(default_factory=list)
    competing_mapping: Optional[dict] = None
    suggested_disambiguator: Optional[str] = None
    diagnostics: dict = field(default_factory=dict)


class InputShapeError(ValueError):
    pass


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def detect_grid(vals: np.ndarray) -> Optional[float]:
    """Coarsest display grid (ratio space) the values satisfy, or None.

    Coarsest-first iteration is load-bearing (anti-bug 5): returning the
    finest satisfying grid would make percent certification stricter than the
    visible display precision supports."""
    v = np.asarray(vals, dtype=float)
    v = v[np.isfinite(v)]
    if v.size == 0:
        return None
    for g in GRIDS:
        k = np.round(v / g)
        if np.all(np.abs(v - k * g) <= 1e-9 + 1e-9 * np.abs(v)):
            return g
    return None


def _prop_tol(fn: Callable, vals: list, tols: list):
    """Evaluate a rule and propagate per-row input uncertainties through it
    by one-sided finite differences:
        tol_out = sum_i |f(.., x_i + tol_i, ..) - f(..)|
    Uniform across linear, bilinear and clipped rules; no per-rule calculus."""
    base = fn(*vals)
    t = np.zeros_like(base)
    for i in range(len(vals)):
        bumped = [vals[k] if k != i else vals[k] + tols[k]
                  for k in range(len(vals))]
        t = t + np.abs(fn(*bumped) - base)
    return base, t


def _allowed_bad(m: int, cfg: Config) -> int:
    """Robust identification allowance. At least one bad row is ALWAYS
    tolerated, so small tables cannot silently re-route a corrupted true
    column into a virtual node (anti-bug 1)."""
    return max(1, int(np.floor((1.0 - cfg.ident_frac) * m)))


def _iqr(x: np.ndarray) -> float:
    q75, q25 = np.percentile(x, [75, 25])
    return float(q75 - q25)


def _business_form(rule: Rule) -> str:
    s = rule.name
    for code in sorted(VAR_NAMES, key=len, reverse=True):
        s = re.sub(rf"\b{code}\b", VAR_NAMES[code], s)
    return s


def _ingest(columns, job_labels):
    """Robust input validation (anti-bug 6). Branches on `is None` explicitly:
    the truthiness of a numpy array raises, so `labels or default` is
    forbidden throughout this module."""
    if columns is None:
        raise InputShapeError("columns is None")
    if isinstance(columns, np.ndarray):
        if columns.ndim != 2:
            raise InputShapeError(
                "expected a 2-D array of shape (rows, cols); "
                f"got ndim={columns.ndim}")
        cols = [np.asarray(columns[:, j], dtype=float)
                for j in range(columns.shape[1])]
    else:
        cols = []
        for i, c in enumerate(columns):
            a = np.asarray(c, dtype=float)
            if a.ndim != 1:
                raise InputShapeError(f"column {i} is not 1-D (ndim={a.ndim})")
            cols.append(a)
    if len(cols) == 0:
        return [], []
    n = cols[0].size
    for i, c in enumerate(cols):
        if c.size != n:
            raise InputShapeError(
                f"column {i} has {c.size} rows; column 0 has {n}")
    if job_labels is None:                      # never `job_labels or ...`
        labels = [f"Job {i + 1}" for i in range(n)]
    else:
        labels = [str(x) for x in list(job_labels)]
        if len(labels) != n:
            raise InputShapeError(
                f"job_labels has {len(labels)} entries for {n} rows")
    return cols, labels


# ---------------------------------------------------------------------------
# Matching: robust identification of a predicted vector among the columns
# ---------------------------------------------------------------------------

def _money_strict(pred, ptol, cfg):
    return (ptol + cfg.money_obs_tol + cfg.cert_slack
            + cfg.cert_money_rel * np.abs(pred))


def _match_candidates(pred, ptol, rule, unassigned, cols, cfg, ab,
                      exact_only=False):
    """All qualifying (sortkey, col, VarVal, stats) for a prediction. The
    predictor support never contains a candidate column (candidates are
    unassigned by construction), so a column can never witness itself.

    exact_only=True is matching tier 1: only columns fitting at STRICT
    (certification) precision on every row may be claimed. Tier 2 (robust + salvage) runs only after tier 1 reaches
    a global fixpoint, so an exact-fit explanation of a column always outranks
    an approximate-fit claim by a different rule (e.g. N = E-B must not steal
    the Underbillings column with one allowed-bad row from U = max(E-B,0),
    which fits it exactly)."""
    m = pred.size
    out = []
    if rule.kind == "money":
        strict = _money_strict(pred, ptol, cfg)
        loose = strict + np.maximum(cfg.ident_abs, cfg.ident_rel * np.abs(pred))
        informative = int((np.abs(pred) > strict + 1e-9).sum())
        for j in unassigned:
            x = cols[j]
            match_x = (np.abs(x)
                       if rule.out in MAGNITUDE_PRESENTATION_VARS else x)
            # Identification answers "which business column is this?"
            # For U/O, sign is presentation validation, not semantic identity.
            # Keep x itself in VarVal below so certification still sees and
            # reports every negative presentation value.
            resid = np.abs(match_x - pred)
            bad = int((resid > loose).sum())
            strict_bad = int((resid > strict).sum())
            if (strict_bad if exact_only else bad) > (0 if exact_only else ab):
                # NB: no majority-fit "salvage" here. Loosening identification
                # lets structurally-coincident predictions of WRONG hypotheses
                # claim real columns (e.g. with B anchored on the U column,
                # E - U literally equals B on every underbilled row). Heavier
                # corruption than the robust allowance is instead caught by
                # the post-selection shadow audit (_audit_shadowed_virtuals).
                continue
            vv = VarVal(var=rule.out, values=x.copy(),
                        tol=np.full(m, cfg.money_obs_tol),
                        support=frozenset([j]), col=j,
                        deps=frozenset([rule.out]))
            sortkey = (strict_bad, bad,
                       float(np.minimum(resid, loose).sum()))
            out.append((sortkey, j, vv,
                        dict(bad=bad, strict_bad=strict_bad,
                             informative=informative,
                             max_resid=float(resid[resid <= loose]
                                             .max(initial=0.0)))))
    else:
        for j in unassigned:
            x = cols[j]
            best = None
            # Homogeneity typing / anti-bugs 3+4: the role of a column is
            # never fixed at ingestion, and low whole-percent values are
            # interpretable both as literal ratios and as percents; both
            # readings are offered and the matcher keeps whichever fits.
            for scale in (1.0, 100.0):
                xr = x / scale
                inside = (xr >= -0.25) & (xr <= 2.0)   # WIP ratios live ~[0,1.1]
                if inside.sum() < 0.9 * m:
                    continue
                grid = detect_grid(xr)
                coltol = (grid * cfg.pct_grid_mult) if grid is not None \
                    else cfg.pct_default_tol
                strict = ptol + coltol + 1e-9
                loose = strict + cfg.pct_ident_slack
                resid = np.abs(xr - pred)
                bad = int((resid > loose).sum())
                strict_bad = int((resid > strict).sum())
                if (strict_bad if exact_only else bad) > \
                        (0 if exact_only else ab):
                    continue
                informative = int((np.abs(pred) > strict + 1e-9).sum())
                cand = ((strict_bad, bad,
                         float(np.minimum(resid, loose).sum())),
                        scale, grid, coltol, informative,
                        float(resid[resid <= loose].max(initial=0.0)))
                if best is None or cand[0] < best[0]:
                    best = cand
            if best is None:
                continue
            sortkey, scale, grid, coltol, informative, max_resid = best
            vv = VarVal(var=rule.out, values=cols[j] / scale,
                        tol=np.full(m, coltol), support=frozenset([j]),
                        col=j, interp_scale=scale, grid=grid,
                        deps=frozenset([rule.out]))
            out.append((sortkey, j, vv,
                        dict(bad=sortkey[1], strict_bad=sortkey[0],
                             informative=informative,
                             max_resid=max_resid)))
    out.sort(key=lambda t: t[0])
    return out


def _edge_weight(rule, informative, strict_bad, m, cfg):
    """Evidence weight of one edge. Percent witnesses are down-weighted,
    further so on small tables (anti-bug 7); the informative-row minimum for
    clipped (max(.,0)) witnesses is applied at the FAMILY level in
    _merge_families, where U and O can jointly establish what neither does
    alone. Evidence is counted at CERTIFICATION precision: a row that fits
    only inside the robust identification slack does not testify, so the
    weight is scaled by the strictly-explained fraction of rows. On
    tiny-dollar 5-row tables this is what stops a wrong hypothesis from
    outscoring the exact-fit truth by accumulating 4-of-5 coincidence
    matches."""
    if informative == 0:
        return 0.0
    base = 1.0 if rule.kind == "money" \
        else cfg.pct_weight * min(1.0, m / float(cfg.small_n))
    return base * (m - strict_bad) / float(m)


# ---------------------------------------------------------------------------
# Peeling: one uniform propagation loop (identification), then checks
# ---------------------------------------------------------------------------

def _peel(cols, seeds, cfg):
    """Erasure-style decoding over the constraint hypergraph.

    Repeats: (a) match phase -- every newly-ready rule predicts its unknown
    output and tries to claim an unassigned column; (b) only when matching
    stalls, materialize ONE virtual node, which may unlock further matches.

    Two invariants make repeated work unnecessary and the search correct:
      * known variables are immutable, so a ready rule's prediction is fixed
        and is attempted exactly once;
      * the unassigned set only shrinks, so a failed match can never succeed
        later. Hence `attempted` is a permanent set.
    """
    known = dict(seeds)
    m = cols[0].size
    ab = _allowed_bad(m, cfg)
    assigned = {vv.col for vv in known.values() if vv.col is not None}
    unassigned = [j for j in range(len(cols)) if j not in assigned]
    edges = []
    attempted = set()

    def ready(rule):
        return rule.out not in known and all(i in known for i in rule.ins)

    def eval_rule(rule):
        ins = [known[i] for i in rule.ins]
        pred, ptol = _prop_tol(rule.fn,
                               [iv.values for iv in ins],
                               [iv.tol for iv in ins])
        psupp = frozenset().union(*[iv.support for iv in ins])
        return pred, ptol, psupp

    def match_tier(exact_only):
        """Run one tier to fixpoint; returns True if anything was claimed.
        Known variables are immutable and the unassigned set only shrinks,
        so each (rule, tier) is attempted at most once, permanently."""
        any_claim = False
        progress = True
        while progress:
            progress = False
            for rule in RULES_ORDERED:
                tag = (rule.name, exact_only)
                if tag in attempted or not ready(rule):
                    continue
                attempted.add(tag)
                if not unassigned:
                    continue
                pred, ptol, psupp = eval_rule(rule)
                cands = _match_candidates(pred, ptol, rule, unassigned,
                                          cols, cfg, ab,
                                          exact_only=exact_only)
                if not cands:
                    continue
                _, j, vv, st = cands[0]
                vv.derivation = f"column {j} matched by {rule.name}"
                known[rule.out] = vv
                unassigned.remove(j)
                edges.append(Edge(
                    rule=rule, out_var=rule.out, col=j,
                    pred_support=psupp, support=psupp | {j},
                    kind=rule.kind,
                    weight=_edge_weight(rule, st["informative"],
                                        st["strict_bad"], m, cfg),
                    informative=st["informative"], bad_rows=st["bad"],
                    n_rows=m, max_resid=st["max_resid"]))
                progress = True
                any_claim = True
        return any_claim

    while True:
        # ---- match phase: exact-fit tier first, robust tier on its stall ----
        if match_tier(True):
            continue
        if match_tier(False):
            continue
        # ---- virtualization phase: ONE variable per stall --------------------
        # Materializing one variable at a time guarantees every physical
        # column gets a matching attempt for each rule before any virtual
        # could leapfrog it (anti-bug 1).
        made = False
        for rule in RULES_ORDERED:
            if not ready(rule):
                continue
            pred, ptol, psupp = eval_rule(rule)
            known[rule.out] = VarVal(
                var=rule.out, values=pred, tol=ptol, support=psupp,
                col=None, derivation=f"virtual: {rule.name}",
                defining_family=rule.family,
                deps=frozenset().union(
                    *[known[i].deps for i in rule.ins]))
            made = True
            break
        if not made:
            break

    # ---- cross-family checks --------------------------------------------
    # A rule whose variables are ALL already known is no longer needed for
    # identification: it is a candidate check. Checks inside a family that
    # already produced an edge are algebraically implied (the E = B + U - O
    # trap) and are skipped; a virtual's own defining family is vacuous.
    # What survives is genuine extra redundancy between distinct cycles.
    fams_with_edges = {e.rule.family for e in edges}
    for rule in RULES_ORDERED:
        if rule.out not in known or not all(i in known for i in rule.ins):
            continue
        if rule.family in fams_with_edges:
            continue
        out = known[rule.out]
        if out.col is None:
            continue                      # virtual vs virtual: no observation
        pred, ptol, psupp = eval_rule_check(rule, known)
        if out.col in psupp:
            continue                      # an observation never witnesses itself
        if rule.kind == "money":
            strict = _money_strict(pred, ptol, cfg)
            loose = strict + np.maximum(cfg.ident_abs,
                                        cfg.ident_rel * np.abs(pred))
        else:
            strict = ptol + out.tol + 1e-9
            loose = strict + cfg.pct_ident_slack
        resid = np.abs(out.values - pred)
        bad = int((resid > loose).sum())
        if bad > ab:
            continue
        strict_bad = int((resid > strict).sum())
        informative = int((np.abs(pred) > strict + 1e-9).sum())
        edges.append(Edge(
            rule=rule, out_var=rule.out, col=out.col,
            pred_support=psupp, support=psupp | out.support,
            kind=rule.kind,
            weight=_edge_weight(rule, informative, strict_bad, m, cfg),
            informative=informative, bad_rows=bad, n_rows=m,
            max_resid=float(resid[resid <= loose].max(initial=0.0)),
            is_check=True))
        fams_with_edges.add(rule.family)

    return known, edges


def eval_rule_check(rule, known):
    ins = [known[i] for i in rule.ins]
    pred, ptol = _prop_tol(rule.fn,
                           [iv.values for iv in ins],
                           [iv.tol for iv in ins])
    psupp = frozenset().union(*[iv.support for iv in ins])
    return pred, ptol, psupp


# ---------------------------------------------------------------------------
# Evidence: family merging == counting independent cycles, not formulas
# ---------------------------------------------------------------------------

def _merge_families(edges, cfg):
    """Merge edges by algebraic family. Each merged family contributes ONE
    independent cycle of evidence (cyclomatic counting): N = E-B,
    U = max(E-B,0) and O = max(B-E,0) collapse into a single unit, while the
    union of their supports still records every column that unit touches.

    The clipped-witness informative-row minimum (anti-bug 7) is enforced
    HERE, jointly: a family witnessed only by max(.,0) rules needs
    min_informative_rows of nonzero-expected rows in total across them.
    Underbillings informative on one job plus Overbillings informative on
    another jointly pin the billing cycle exactly as well as one column
    informative on two jobs -- while an all-zeros column (informative == 0,
    weight 0, contributing 0 rows) still can never witness anything."""
    fams = {}
    for e in edges:
        s, w, inf, unclipped = fams.get(
            e.rule.family, (frozenset(), 0.0, 0, False))
        fams[e.rule.family] = (
            s | e.support, max(w, e.weight),
            inf + (e.informative if e.weight > 0 else 0),
            unclipped or (not e.rule.clipped and e.weight > 0))
    out = {}
    for fam, (s, w, inf, unclipped) in fams.items():
        if not unclipped and inf < cfg.min_informative_rows:
            w = 0.0
        out[fam] = (s, w)
    return out


def _semantic_key(known):
    """Hypotheses are keyed by their SEMANTIC core mapping, not by physical
    column index plus an orientation tag: two readings that map the same
    columns to the same business meaning are the same hypothesis (anti-bug 2:
    a table holding both C and G physically must not look ambiguous)."""
    parts = []
    for v in CORE_VARS:
        vv = known.get(v)
        if vv is None:
            parts.append((v, None))
        elif vv.col is not None:
            parts.append((v, ("col", vv.col)))
        else:
            parts.append((v, ("virtual", tuple(sorted(vv.support)))))
    return tuple(parts)


# ---------------------------------------------------------------------------
# Hypothesis enumeration under economic priors
# ---------------------------------------------------------------------------

def _v_candidates(cols, finite, cfg, shortlist):
    scored = []
    for j, c in enumerate(cols):
        x = c[finite]
        pos = x[x > 0]
        if pos.size < max(cfg.min_rows, int(np.ceil(0.5 * max(1, x.size)))):
            continue
        scored.append((j, float(np.median(pos))))
    scored.sort(key=lambda t: -t[1])
    ranked = [j for j, _ in scored]
    return ranked[:cfg.v_shortlist] if shortlist else ranked


def _x_candidates(cols, vcol, finite, cfg, diag):
    V = cols[vcol]
    mask0 = finite & (V > 0)
    if int(mask0.sum()) < cfg.min_rows:
        return []
    out = []
    clo, chi = cfg.cost_ratio_band
    glo, ghi = cfg.margin_band
    for j, c in enumerate(cols):
        if j == vcol:
            continue
        r = c[mask0] / V[mask0]
        med = float(np.median(r))
        iqr = _iqr(r)
        in_c = clo <= med <= chi
        in_g = glo <= med <= ghi
        if (in_c or in_g) and iqr > cfg.estimate_iqr_max:
            diag["prior_rejections"].append(
                f"col {j} vs V=col {vcol}: ratio median {med:.3f} in an "
                f"estimate band but portfolio-unstable (IQR {iqr:.3f} > "
                f"{cfg.estimate_iqr_max}); progress columns spread, "
                f"estimates do not")
            continue
        if in_c:
            out.append((j, "C"))
        if in_g:
            out.append((j, "G"))
    return out


def _anchor_candidates(cols_m, Vm, Cm, used, cfg):
    """Joint candidate sets for the progress anchors D and B (pruned by
    robust priors; the final choice is made jointly by evidence, never
    greedily)."""
    frac = cfg.prior_robust_frac
    m = Vm.size
    d_c, b_c = [], []
    for j, x in enumerate(cols_m):
        if j in used:
            continue
        nonneg = int((x >= -cfg.money_obs_tol).sum()) >= frac * m
        if not nonneg:
            continue
        if (int((x <= Cm * cfg.d_over_c_slack + 1.0).sum()) >= frac * m
                and float(np.median(x / np.maximum(Cm, 1e-9)))
                >= cfg.anchor_live_med):
            d_c.append(j)
        if (int((x <= Vm * cfg.b_over_v_slack + 1.0).sum()) >= frac * m
                and float(np.median(x / np.maximum(Vm, 1e-9)))
                >= cfg.anchor_live_med):
            b_c.append(j)
    return d_c, b_c


def _build_hypothesis(cols_m, row_index, vcol, xcol, orient, dcol, bcol, cfg):
    m = cols_m[0].size

    def obs(var, col):
        return VarVal(var=var, values=cols_m[col].copy(),
                      tol=np.full(m, cfg.money_obs_tol),
                      support=frozenset([col]), col=col,
                      deps=frozenset([var]))

    seeds = {"V": obs("V", vcol), orient: obs(orient, xcol),
             "D": obs("D", dcol), "B": obs("B", bcol)}
    known, edges = _peel(cols_m, seeds, cfg)
    fams = _merge_families(edges, cfg)
    corr_d = sum(1 for s, w in fams.values() if w > 0 and dcol in s)
    corr_b = sum(1 for s, w in fams.values() if w > 0 and bcol in s)
    evidence = float(sum(w for _, w in fams.values()))
    n_assigned = sum(1 for vv in known.values() if vv.col is not None)
    hyp = Hypothesis(
        v_col=vcol, x_col=xcol, orientation=orient, d_col=dcol, b_col=bcol,
        key=_semantic_key(known), known=known, edges=edges, families=fams,
        corr_d=corr_d, corr_b=corr_b, evidence=evidence,
        score=evidence + cfg.assigned_bonus * n_assigned,
        n_assigned=n_assigned, row_index=row_index)
    return hyp


def _enumerate_hypotheses(cols, finite, cfg, diag, shortlist):
    by_key = {}
    for vcol in _v_candidates(cols, finite, cfg, shortlist):
        for xcol, orient in _x_candidates(cols, vcol, finite, cfg, diag):
            Vfull = cols[vcol]
            Cfull = cols[xcol] if orient == "C" else Vfull - cols[xcol]
            # Degenerate rows (V <= 0 or C <= 0) are masked out, never
            # divided through (anti-bug 8).
            mask = finite & (Vfull > 0) & (Cfull > 0)
            m = int(mask.sum())
            if m < cfg.min_rows:
                diag["prior_rejections"].append(
                    f"V=col {vcol}, X=col {xcol} as {orient}: only {m} "
                    f"non-degenerate rows")
                continue
            cols_m = [c[mask] for c in cols]
            row_index = np.nonzero(mask)[0]
            d_c, b_c = _anchor_candidates(
                cols_m, Vfull[mask], Cfull[mask], {vcol, xcol}, cfg)
            pairs = [(d, b) for d in d_c for b in b_c if d != b]
            if len(pairs) > cfg.max_anchor_pairs:
                diag["notes"].append(
                    f"anchor pair cap hit for V=col {vcol}/X=col {xcol} "
                    f"({len(pairs)} pairs)")
                pairs = pairs[:cfg.max_anchor_pairs]
            for dcol, bcol in pairs:
                hyp = _build_hypothesis(cols_m, row_index, vcol, xcol,
                                        orient, dcol, bcol, cfg)
                diag["hypotheses_examined"] = \
                    diag.get("hypotheses_examined", 0) + 1
                old = by_key.get(hyp.key)
                if old is None or hyp.score > old.score:
                    by_key[hyp.key] = hyp
    return by_key


# ---------------------------------------------------------------------------
# Certification: strict per-row re-check of every witnessed relation
# ---------------------------------------------------------------------------

def _certify(hyp, labels, cfg):
    witnesses, failures = [], []
    for e in hyp.edges:
        rule = e.rule
        ins = [hyp.known[i] for i in rule.ins]
        pred, ptol = _prop_tol(rule.fn,
                               [iv.values for iv in ins],
                               [iv.tol for iv in ins])
        out = hyp.known[e.out_var]
        if rule.kind == "money":
            strict = (ptol + out.tol + cfg.cert_slack
                      + cfg.cert_money_rel * np.abs(pred))
        else:
            strict = ptol + out.tol + 1e-9
        scale = out.interp_scale
        compared = (np.abs(out.values)
                    if e.out_var in MAGNITUDE_PRESENTATION_VARS
                    else out.values)
        resid = compared - pred
        bad_rows = np.nonzero(np.abs(resid) > strict)[0]
        for r in bad_rows:
            orig = int(hyp.row_index[r])
            failures.append(RowFailure(
                relation=rule.name, business_form=_business_form(rule),
                variable=e.out_var, column=e.col,
                row_index=orig, row_label=labels[orig],
                observed=float(out.values[r] * scale),
                expected=float(pred[r] * scale),
                difference=float(resid[r] * scale),
                tolerance=float(strict[r] * scale)))
        ok = np.abs(resid) <= strict
        witnesses.append(Witness(
            relation=rule.name, business_form=_business_form(rule),
            column=e.col, n_rows=int(pred.size),
            n_informative=e.informative,
            max_abs_residual=float(np.abs(resid[ok]).max(initial=0.0)),
            weight=e.weight, family=rule.family))
    return witnesses, failures


PCT_VARS = {"M", "P", "PB"}


def _lev(a: str, b: str) -> int:
    if len(a) < len(b):
        a, b = b, a
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[-1] + 1,
                           prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def _disp(v: float) -> str:
    if abs(v - round(v)) < 1e-6:
        return f"{int(round(v)):,}"
    return f"{v:,.4f}".rstrip("0").rstrip(".")


def _single_deleted_digit_index(longer: str, shorter: str):
    """Return the index of one removable digit, or None.

    This detects a single inserted digit at the beginning, middle, or end.
    """
    if len(longer) != len(shorter) + 1:
        return None
    for i in range(len(longer)):
        if longer[:i] + longer[i + 1:] == shorter:
            return i
    return None


def _classify_error(observed: float, proposed: float):
    """Pattern-match an observed/implied value pair against known OCR
    failure modes. Order matters: exact one-digit insertions/deletions
    are checked before broader scale signatures because they identify the
    more specific OCR failure mode."""
    if proposed != 0 and observed != 0:
        same_magnitude = abs(abs(observed) - abs(proposed)) <= max(
            0.51, 1e-9 * abs(proposed))
        if same_magnitude and np.signbit(observed) != np.signbit(proposed):
            return ("sign_error",
                    "magnitude matches the implied value, but the sign is "
                    "reversed")
    do = re.sub(r"[^0-9]", "", _disp(abs(observed)))
    dp = re.sub(r"[^0-9]", "", _disp(abs(proposed)))

    # Exact edit signatures are more specific than a generic 10x/100x scale
    # signature, and now work for digits inserted or dropped anywhere.
    extra_at = _single_deleted_digit_index(do, dp)
    if extra_at is not None:
        return ("extra_character",
                f"one extra digit ({do[extra_at]}) at position "
                f"{extra_at + 1} of the observed value")
    missing_at = _single_deleted_digit_index(dp, do)
    if missing_at is not None:
        return ("dropped_character",
                f"one digit ({dp[missing_at]}) is missing at position "
                f"{missing_at + 1} of the observed value")

    if proposed != 0 and observed != 0:
        ratio = abs(observed / proposed)
        for k in range(1, 9):
            for r in (10.0 ** k, 10.0 ** -k):
                if abs(ratio - r) < 0.005 * r:
                    return ("separator_or_magnitude_error",
                            f"observed is {r:g}x the implied value -- "
                            "thousands-separator misread (comma/period) or "
                            "dropped/added digits")
    d = _lev(do, dp)
    if d == 0:
        return ("formatting_only",
                "digits identical; discrepancy is formatting or scale")
    if d <= 2 and abs(len(do) - len(dp)) <= 1:
        if sorted(do) == sorted(dp):
            return ("digit_transposition",
                    f"same digits in a different order (edit distance {d})")
        return ("ocr_character_misread",
                f"{d} character edit(s) between observed and implied value")
    return ("unexplained_substitution",
            f"no OCR-like pattern (digit edit distance {d}); "
            "value appears flatly wrong")


def _solve_input(fn, vals, i, target, x0):
    """Secant-solve f(..., x_i, ...) = target for one input. All rules are
    monotone in each input on the economic domain, so this converges or
    cleanly fails (e.g. inside a max(.,0) flat region)."""
    def g(x):
        vv = [np.array([v]) for v in vals]
        vv[i] = np.array([x])
        return float(fn(*vv)[0]) - target
    x1 = x0 * 1.01 + 1.0
    f0, f1 = g(x0), g(x1)
    for _ in range(80):
        if f1 == f0:
            return None
        x2 = x1 - f1 * (x1 - x0) / (f1 - f0)
        if not np.isfinite(x2):
            return None
        x0, f0 = x1, f1
        x1 = x2
        f1 = g(x1)
        if abs(f1) <= 1e-9 * max(1.0, abs(target)):
            return float(x1)
    return float(x1) if abs(f1) <= 1e-6 * max(1.0, abs(target)) else None


def _transplant_sources(cols, orig_r, col, observed, tol=0.51):
    """Cells whose value equals the observed misfit: same row, any other
    column; same column, adjacent rows. The signature of an extractor
    grabbing a neighbor's value."""
    if observed == 0:
        return []
    out = []
    n = cols[0].size
    for j in range(len(cols)):
        if j != col and abs(cols[j][orig_r] - observed) <= tol:
            out.append((int(orig_r), j))
    for dr in (-1, 1):
        rr = orig_r + dr
        if 0 <= rr < n and abs(cols[col][rr] - observed) <= tol:
            out.append((int(rr), col))
    return out


def _implied_values(culprit, failing, r, cfg):
    """Implied value of the culprit at row r from each failing relation in
    which it appears as an IMMEDIATE output or input, with a per-relation
    tolerance for the implication (percent-grid implications are coarse).
    Clipped relations sitting in their flat region (observed output within
    tolerance of the clip) are skipped: their inverse is non-unique."""
    out = []
    for (e, rule, ins, outv, pred, strict, gset) in failing:
        if culprit == rule.out:
            out.append((float(pred[r]), float(strict[r]), rule.kind,
                        rule.name, rule.family))
        elif culprit in rule.ins:
            target = float(outv.values[r])
            if rule.clipped and abs(target) <= float(strict[r]):
                continue
            i = rule.ins.index(culprit)
            vals = [iv.values[r] for iv in ins]
            sol = _solve_input(rule.fn, vals, i, target, vals[i])
            if sol is None:
                continue
            solp = _solve_input(rule.fn, vals, i,
                                target + float(strict[r]), vals[i])
            tol = (abs(solp - sol) if solp is not None
                   else float(strict[r]))
            out.append((sol, max(tol, 1e-9), rule.kind, rule.name,
                        rule.family))
    return out


def _consistent(vals):
    """True if >= 2 implications mutually agree, judged PAIRWISE under the
    looser tolerance of each pair (a dollar-exact money implication and a
    grid-coarse percent implication agree if they sit within the percent
    grid's slack). This is the single-error consistency test that separates
    an entangled candidate pair: the true culprit's implications agree, an
    innocent bystander's contradict each other."""
    if len(vals) < 2:
        return False
    for i in range(len(vals)):
        for j in range(i + 1, len(vals)):
            vi, ti = vals[i][0], vals[i][1]
            vj, tj = vals[j][0], vals[j][1]
            if abs(vi - vj) > max(2.0, ti, tj):
                return False
    return True



def _family_consensus(implied):
    """Collapse correction implications to one vote per algebraic family."""
    grouped = {}
    for value, tol, kind, name, family in implied:
        grouped.setdefault(family, []).append(
            (value, tol, kind, name, family))

    representatives = []
    for family, votes in grouped.items():
        money = [v for v in votes if v[2] == "money"]
        used = money if money else votes
        representatives.append((
            float(np.median([v[0] for v in used])),
            max(v[1] for v in used),
            "money" if money else used[0][2],
            [v[3] for v in votes],
            family,
        ))

    primary = [v for v in representatives if v[2] == "money"]
    primary = primary if primary else representatives
    if not primary:
        return None, [], []

    center = float(np.median([v[0] for v in primary]))
    agreeing = [
        v for v in representatives
        if abs(v[0] - center) <= max(2.0, v[1])
    ]
    basis = [v[3][0] for v in agreeing]
    return center, agreeing, basis


def _diagnose(hyp, cols, labels, cfg, failures):
    """Distill relation-level failures into cell-level findings.

    Per failing row: (1) candidate culprits = intersection of the failing
    relations' GROUNDED variable sets (a relation through a virtual
    implicates the virtual's base variables); (2) exonerate variables that
    participate, sensitively, in a relation passing on that row; (3) if
    several candidates remain, keep those whose implied values are
    self-consistent across the failing relations; (4) for a unique culprit,
    propose the money-implied correction (percent-implied values are
    grid-coarse and serve only as corroboration) and classify the
    observed/implied pair; (5) scan for neighbor transplants."""
    findings = []
    edge_data = []
    for e in hyp.edges:
        rule = e.rule
        ins = [hyp.known[i] for i in rule.ins]
        pred, ptol = _prop_tol(rule.fn,
                               [iv.values for iv in ins],
                               [iv.tol for iv in ins])
        out = hyp.known[e.out_var]
        if rule.kind == "money":
            strict = (ptol + out.tol + cfg.cert_slack
                      + cfg.cert_money_rel * np.abs(pred))
        else:
            strict = ptol + out.tol + 1e-9
        compared = (np.abs(out.values)
                    if e.out_var in MAGNITUDE_PRESENTATION_VARS
                    else out.values)
        ok = np.abs(compared - pred) <= strict
        gset = out.deps | frozenset().union(*[iv.deps for iv in ins])
        edge_data.append((e, rule, ins, out, pred, strict, ok, gset))

    fail_rows = sorted({f.row_index for f in failures
                        if not f.relation.startswith("column ")})
    audit = [f for f in failures if f.relation.startswith("column ")]
    orig_to_m = {int(orig): m for m, orig in enumerate(hyp.row_index)}
    culprits_by_row = {}

    for orig_r in fail_rows:
        r = orig_to_m.get(orig_r)
        if r is None:
            continue
        failing, exonerated = [], set()
        for (e, rule, ins, out, pred, strict, ok, gset) in edge_data:
            if ok[r]:
                exonerated |= out.deps
                for k, iv in enumerate(ins):
                    vals = [x.values[r] for x in ins]
                    d = max(1.0, 0.001 * abs(vals[k]))
                    vv = [np.array([v]) for v in vals]
                    vv[k] = np.array([vals[k] + d])
                    if abs(float(rule.fn(*vv)[0]) - pred[r]) > 1e-9:
                        exonerated |= iv.deps
            else:
                failing.append((e, rule, ins, out, pred, strict, gset))
        if not failing:
            continue

        common = dict(row_index=int(orig_r), row_label=labels[orig_r],
                      exonerated_variables=sorted(exonerated),
                      failing_relations=[f[1].name for f in failing])

        cands = set(frozenset.intersection(*[f[-1] for f in failing])) \
            - exonerated
        if len(cands) > 1:
            ok_cands = [cv for cv in cands
                        if _consistent(_implied_values(cv, failing, r, cfg))]
            if len(ok_cands) == 1:
                cands = set(ok_cands)
        if len(cands) != 1:
            findings.append(Finding(
                **common, culprit_column=None, culprit_variable=None,
                candidate_variables=sorted(cands), observed=None,
                proposed_correction=None, correction_basis=[],
                confidence="low", classification="ambiguous_multi_cell",
                classification_detail=("violations do not isolate a single "
                                       "cell; possibly multiple errors in "
                                       "this row"),
                transplant_sources=[]))
            continue
        culprit = cands.pop()
        culprits_by_row[int(orig_r)] = culprit
        cvv = hyp.known[culprit]
        scale = cvv.interp_scale
        implied = _implied_values(culprit, failing, r, cfg)
        candidate, agreeing_families, basis = _family_consensus(implied)
        proposed = None
        conf = "low"

        if candidate is not None:
            if culprit not in PCT_VARS:
                candidate = round(candidate)
            elif cvv.grid is not None:
                candidate = round(candidate / cvv.grid) * cvv.grid

            if len(agreeing_families) >= cfg.correction_min_families:
                proposed = candidate
                conf = "high"

        observed = (float(cvv.values[r]) * scale
                    if cvv.col is not None else None)
        transplant = (_transplant_sources(cols, orig_r, cvv.col, observed)
                      if cvv.col is not None and observed is not None else [])

        if candidate is not None and observed is not None:
            cls, detail = _classify_error(observed, candidate * scale)
            if proposed is None:
                count = len(agreeing_families)
                detail += (
                    f"; {count} independent validation "
                    f"{'family' if count == 1 else 'families'} agree, but "
                    f"{cfg.correction_min_families} are required before "
                    "suggesting a replacement"
                )
            if transplant and cls in ("unexplained_substitution",
                                      "digit_transposition"):
                src_s = ", ".join(f"(row {a}, col {b})"
                                  for a, b in transplant)
                cls = "neighbor_transplant"
                detail = ("observed value equals neighboring cell(s) "
                          f"{src_s} -- extractor likely grabbed the wrong cell"
                          + ("" if proposed is not None else
                             f"; fewer than {cfg.correction_min_families} "
                             "independent validations support a replacement"))
        else:
            cls, detail = ("unresolved",
                           "culprit identified but no invertible identity "
                           "available to imply its value")

        findings.append(Finding(
            **common, culprit_column=cvv.col, culprit_variable=culprit,
            candidate_variables=[culprit], observed=observed,
            proposed_correction=(proposed * scale
                                 if proposed is not None else None),
            correction_basis=basis,
            confidence=conf,
            classification=cls, classification_detail=detail,
            transplant_sources=transplant))

    for f in audit:
        # If an edge finding at this row already attributed the failure to
        # a base variable of the audited virtual, the audit mismatch is a
        # downstream echo of that culprit -- skip the duplicate.
        vdeps = hyp.known[f.variable].deps if f.variable in hyp.known \
            else frozenset()
        if culprits_by_row.get(f.row_index) in vdeps:
            continue
        expected = (round(f.expected) if f.variable not in PCT_VARS
                    else f.expected)
        cls, detail = _classify_error(f.observed, expected)
        proposed = None
        basis = [f.relation]
        confidence = "low"
        detail += (
            f"; 1 independent validation family supports this value, "
            f"but {cfg.correction_min_families} are required before "
            "suggesting a replacement"
        )

        tr = _transplant_sources(cols, f.row_index, f.column, f.observed)
        if tr and cls in ("unexplained_substitution", "digit_transposition"):
            cls = "neighbor_transplant"
            detail = ("observed value equals neighboring cell(s) "
                      + ", ".join(f"(row {a}, col {b})" for a, b in tr)
                      + " -- extractor likely grabbed the wrong cell"
                      + ("" if proposed is not None else
                         f"; fewer than {cfg.correction_min_families} "
                         "independent validations support a replacement"))
        findings.append(Finding(
            row_index=f.row_index, row_label=f.row_label,
            culprit_column=f.column, culprit_variable=f.variable,
            candidate_variables=[f.variable], exonerated_variables=[],
            observed=f.observed, proposed_correction=proposed,
            correction_basis=basis, confidence=confidence,
            classification=cls, classification_detail=detail,
            transplant_sources=tr,
            failing_relations=[f.relation]))
    return findings


# ---------------------------------------------------------------------------
# Ambiguity helper
# ---------------------------------------------------------------------------

def _audit_shadowed_virtuals(hyp, cols, labels, cfg):
    """Anti-bug 1, heavy-corruption arm. A virtual node may only stand in
    for a variable when no physical column was a better explanation. If an
    unassigned column strictly matches a virtual variable's implied values on
    a majority of rows (i.e. the true column was too corrupted for robust
    identification and got routed around), that is an inconsistency of the
    table, reported row-by-row -- never silently accepted."""
    failures = []
    assigned = {vv.col for vv in hyp.known.values() if vv.col is not None}
    cols_m = [c[hyp.row_index] for c in cols]
    m = cols_m[0].size if cols_m else 0
    unassigned = [j for j in range(len(cols_m)) if j not in assigned]
    if not unassigned or m == 0:
        return failures
    need = int(np.ceil(cfg.shadow_audit_frac * m))
    for j in unassigned:
        x = cols_m[j]
        best = None
        for var, vv in hyp.known.items():
            if vv.col is not None or var in PCT_VARS:
                continue
            strict = vv.tol + cfg.money_obs_tol + cfg.cert_slack \
                + cfg.cert_money_rel * np.abs(vv.values)
            match_x = (np.abs(x)
                       if var in MAGNITUDE_PRESENTATION_VARS else x)
            match_resid = np.abs(match_x - vv.values)
            fit = int((match_resid <= strict).sum())
            qualifies = need <= fit < m
            if qualifies and (best is None or fit > best[0]):
                best = (fit, var, vv, strict, match_resid, match_x)
        if best is None:
            continue
        fit, var, vv, strict, match_resid, match_x = best
        for r in np.nonzero(match_resid > strict)[0]:
            orig = int(hyp.row_index[r])
            failures.append(RowFailure(
                relation=f"column {j} realizes {var} "
                         f"({vv.derivation}) but disagrees",
                business_form=(f"unmapped column {j} matches "
                               f"{VAR_NAMES[var]} on {fit}/{m} rows"),
                variable=var, column=j, row_index=orig,
                row_label=labels[orig],
                observed=float(x[r]), expected=float(vv.values[r]),
                difference=float(match_x[r] - vv.values[r]),
                tolerance=float(strict[r])))
    return failures


def _certify_full(hyp, cols, labels, cfg):
    witnesses, failures = _certify(hyp, labels, cfg)
    failures += _audit_shadowed_virtuals(hyp, cols, labels, cfg)
    return witnesses, failures


def _suggest_disambiguator(h1, h2):
    """If two semantic readings survive, name a single additional column that
    would break the residual symmetry: a variable that is virtual in both
    readings but whose predicted values materially diverge between them."""
    priority = ["P", "E", "Q", "U", "O", "N", "PB", "H", "M", "R", "RB"]
    for v in priority:
        a, b = h1.known.get(v), h2.known.get(v)
        if a is None or b is None or a.col is not None or b.col is not None:
            continue
        if a.values.size != b.values.size:
            continue
        denom = np.maximum(1.0, np.maximum(np.abs(a.values), np.abs(b.values)))
        if float(np.median(np.abs(a.values - b.values) / denom)) > 0.05:
            return (f"a physical {VAR_NAMES[v]} ({v}) column would "
                    f"distinguish the two readings")
    return None


def _mapping_of(hyp):
    return {vv.col: var for var, vv in hyp.known.items() if vv.col is not None}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def validate_wip(columns, job_labels=None, config=None) -> ValidationResult:
    """Identify and validate a header-blind WIP table.

    columns    : 2-D numpy array (rows x cols) or a sequence of 1-D arrays.
    job_labels : optional sequence of row labels (len == n rows).
    Returns a ValidationResult with exactly one of three statuses.
    """
    cfg = config if config is not None else Config()
    cols, labels = _ingest(columns, job_labels)
    diag = {"prior_rejections": [], "notes": [], "hypotheses_examined": 0}

    if len(cols) == 0:
        return ValidationResult(
            status=INSUFFICIENT, reason="empty input: no columns provided",
            diagnostics=diag)
    if len(cols) < 4:
        return ValidationResult(
            status=INSUFFICIENT,
            reason=(f"only {len(cols)} column(s); the required physical core "
                    "{V, estimate (C or G), D, B} needs at least 4"),
            diagnostics=diag)

    n = cols[0].size
    finite = np.all(np.isfinite(np.vstack(cols)), axis=0)
    dropped = int((~finite).sum())
    if dropped:
        diag["notes"].append(f"{dropped} row(s) excluded: non-finite values")
    if int(finite.sum()) < cfg.min_rows:
        return ValidationResult(
            status=INSUFFICIENT,
            reason=(f"only {int(finite.sum())} usable row(s) of {n}; "
                    f"need at least {cfg.min_rows}"),
            diagnostics=diag)

    by_key = _enumerate_hypotheses(cols, finite, cfg, diag, shortlist=True)
    if (not any(h.corr_d >= 1 and h.corr_b >= 1 for h in by_key.values())
            and cfg.expand_v_on_fail):
        # The largest-column prior is a shortlist, not proof: widen V search.
        wide = _enumerate_hypotheses(cols, finite, cfg, diag, shortlist=False)
        for k, h in wide.items():
            old = by_key.get(k)
            if old is None or h.score > old.score:
                by_key[k] = h

    hyps = list(by_key.values())
    diag["semantic_hypotheses"] = len(hyps)

    if not hyps:
        return ValidationResult(
            status=INSUFFICIENT,
            reason=("could not identify the required core: no (Contract "
                    "Value, estimate) column pair satisfied the economic "
                    "priors, or no anchor placement was admissible; see "
                    "diagnostics.prior_rejections"),
            diagnostics=diag)

    # identifiable = the peeled core exists; validatable = every progress
    # anchor lies on at least one independent evidence cycle.
    validatable = [h for h in hyps if h.corr_d >= 1 and h.corr_b >= 1]
    if not validatable:
        best = max(hyps, key=lambda h: h.score)
        diag["uncertified_best_mapping"] = {
            int(c): v for c, v in _mapping_of(best).items()}
        missing = []
        if best.corr_d < 1:
            missing.append("Cost to Date (D)")
        if best.corr_b < 1:
            missing.append("Billings to Date (B)")
        return ValidationResult(
            status=INSUFFICIENT,
            reason=("identifiable but not validatable: the assigned core "
                    "spans the constraint graph as a tree (exactly "
                    "determined), leaving no redundant identity to check"
                    + (f"; {' and '.join(missing)} would go unverified"
                       if missing else "")
                    + ". Adding a derived column such as Earned Revenue, "
                      "Underbillings/Overbillings/Net position, Remaining "
                      "Billings, or Percent Complete would create a "
                      "checkable cycle."),
            diagnostics=diag)

    validatable.sort(key=lambda h: -h.score)
    best = validatable[0]
    # The best hypothesis is certified FIRST and is never abandoned for a
    # rival because of its failures (anti-bug 1: no silent re-routing).
    witnesses, failures = _certify_full(best, cols, labels, cfg)
    findings = _diagnose(best, cols, labels, cfg, failures) if failures else []
    if not failures:
        # Ambiguity gate: a rival "remains" only if it disagrees on the
        # SEMANTIC core, scores within the margin, AND survives strict
        # certification itself -- a reading refuted row-by-row by the data
        # is not a remaining candidate.
        refuted = 0
        best_assigned = frozenset(
            vv.col for vv in best.known.values() if vv.col is not None)
        for rival in validatable[1:]:
            if rival.key == best.key:
                continue
            if rival.score < best.score * cfg.ambiguity_margin:
                break
            rival_assigned = frozenset(
                vv.col for vv in rival.known.values() if vv.col is not None)
            if rival_assigned < best_assigned:
                # Coverage dominance: a reading that explains a strict
                # subset of the columns the winner explains (every column it
                # accounts for, the winner also accounts for, plus more) is
                # not an incomparable alternative -- it is the same
                # explanation with a hole. Genuine ambiguity requires
                # incomparable explanations.
                continue
            _, rf = _certify_full(rival, cols, labels, cfg)
            if rf:
                refuted += 1
                continue
            diag["rivals_refuted_by_certification"] = refuted
            return ValidationResult(
                status=INSUFFICIENT,
                reason=("irreducibly ambiguous: two distinct semantic "
                        "mappings explain the observed columns comparably "
                        f"well (scores {best.score:.2f} vs "
                        f"{rival.score:.2f}) and both certify cleanly; the "
                        "observed columns plus priors do not break the "
                        "residual symmetry"),
                mapping={int(c): v for c, v in _mapping_of(best).items()},
                competing_mapping={int(c): v
                                   for c, v in _mapping_of(rival).items()},
                suggested_disambiguator=_suggest_disambiguator(best, rival),
                diagnostics=diag)
        if refuted:
            diag["rivals_refuted_by_certification"] = refuted

    mapping = {int(c): v for c, v in sorted(_mapping_of(best).items())}
    other = "G" if best.orientation == "C" else "C"
    orientation = (
        f"estimate column (col {best.x_col}) read as "
        f"{VAR_NAMES[best.orientation]} ({best.orientation}); "
        f"{VAR_NAMES[other]} ({other}) "
        + ("observed physically"
           if best.known[other].col is not None
           else f"constructed virtually as V - {best.orientation}"))
    virtuals = {var: vv.derivation for var, vv in best.known.items()
                if vv.col is None}
    diag["winning_score"] = round(best.score, 3)
    diag["evidence_units"] = round(best.evidence, 3)
    diag["corroboration"] = {"D": best.corr_d, "B": best.corr_b}
    diag["families_witnessed"] = sorted(
        f for f, (_, w) in best.families.items() if w > 0)

    return ValidationResult(
        status=FAILED if failures else SUCCESS,
        reason=("" if not failures else
                f"{len(failures)} row-level identity violation(s); the "
                "schedule is internally inconsistent at the cells listed"),
        mapping=mapping,
        mapping_named={c: VAR_NAMES[v] for c, v in mapping.items()},
        estimate_orientation=orientation,
        virtuals=virtuals,
        core={v: best.known[v].values.copy() for v in CORE_VARS},
        row_index=best.row_index.copy(),
        witnesses=witnesses,
        failures=failures,
        findings=findings,
        diagnostics=diag)


# ---------------------------------------------------------------------------
# Human-readable certificate
# ---------------------------------------------------------------------------

def render_report(result: ValidationResult) -> str:
    L = []
    bar = "=" * 72
    L.append(bar)
    L.append(f"WIP VALIDATION REPORT -- status: {result.status.upper()}")
    L.append(bar)
    if result.reason:
        L.append(f"Reason: {result.reason}")
    if result.mapping:
        L.append("")
        L.append("Column identification (header-blind):")
        for c in sorted(result.mapping):
            v = result.mapping[c]
            L.append(f"  column {c:>2}  ->  {VAR_NAMES[v]} ({v})")
    if result.estimate_orientation:
        L.append("")
        L.append(f"Estimate orientation: {result.estimate_orientation}")
    if result.virtuals:
        L.append("")
        L.append("Virtually constructed variables:")
        for v, d in sorted(result.virtuals.items()):
            L.append(f"  {VAR_NAMES[v]} ({v}): {d}")
    if result.witnesses:
        L.append("")
        L.append("Witnessed identities (certificate):")
        for w in result.witnesses:
            where = f"col {w.column}" if w.column is not None else "virtual"
            L.append(f"  {w.business_form}")
            L.append(f"      [{w.relation}]  {where}, {w.n_rows} rows, "
                     f"max residual {w.max_abs_residual:,.4f}, "
                     f"evidence weight {w.weight:.2f}")
    if result.failures:
        L.append("")
        L.append("ROW-LEVEL FAILURES:")
        for f in result.failures:
            L.append(f"  {f.row_label} (row {f.row_index}), col {f.column} "
                     f"[{VAR_NAMES[f.variable]}] via {f.relation}:")
            L.append(f"      observed {f.observed:,.2f}  expected "
                     f"{f.expected:,.2f}  difference {f.difference:+,.2f}  "
                     f"(tolerance +/-{f.tolerance:,.2f})")
    if result.findings:
        L.append("")
        L.append("DIAGNOSIS (probable cell-level causes):")
        for g in result.findings:
            if g.culprit_column is not None:
                head = (f"  {g.row_label} (row {g.row_index}): column "
                        f"{g.culprit_column} "
                        f"[{VAR_NAMES.get(g.culprit_variable, g.culprit_variable)}]")
            else:
                head = (f"  {g.row_label} (row {g.row_index}): "
                        f"unresolved among {g.candidate_variables}")
            L.append(head)
            if g.observed is not None and g.proposed_correction is not None:
                L.append(f"      observed {_disp(g.observed)}  ->  proposed "
                         f"{_disp(g.proposed_correction)}   "
                         f"(confidence {g.confidence}; implied by "
                         f"{', '.join(g.correction_basis)})")
            L.append(f"      {g.classification}: {g.classification_detail}")
    if result.competing_mapping is not None:
        L.append("")
        L.append("Competing semantic mapping:")
        for c in sorted(result.competing_mapping):
            v = result.competing_mapping[c]
            L.append(f"  column {c:>2}  ->  {VAR_NAMES[v]} ({v})")
    if result.suggested_disambiguator:
        L.append(f"Suggestion: {result.suggested_disambiguator}")
    d = result.diagnostics
    if d:
        L.append("")
        L.append(f"Diagnostics: {d.get('hypotheses_examined', 0)} anchor "
                 f"placements examined, "
                 f"{d.get('semantic_hypotheses', 0)} distinct semantic "
                 f"hypotheses"
                 + (f", winning evidence {d.get('evidence_units')} unit(s), "
                    f"anchor corroboration D={d['corroboration']['D']} "
                    f"B={d['corroboration']['B']}"
                    if "corroboration" in d else ""))
        for note in d.get("notes", []):
            L.append(f"  note: {note}")
    L.append(bar)
    return "\n".join(L)
