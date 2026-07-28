"""
Second-generation header-blind WIP validator.

The public contract intentionally mirrors :mod:`wipple.accounting.wip`, but
the implementation does not reuse its anchor enumerator or peeler.  A table is
prepared once, cheap algebraic motifs propose partial semantic mappings, and a
single factor-graph closure engine performs physical matching, virtual
materialization, evidence analysis, certification, and diagnosis.

The permanent data model is two-dimensional and immutable.  Three-dimensional
NumPy arrays are short-lived scoring workspaces only:

    ready predictions x candidate columns x rows

Provenance bitmasks describe which physical observations ground a numeric
proof.  They never stand in for numeric equality; complete multi-row vectors
are compared with propagated and presentation-aware tolerances.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from itertools import combinations
from typing import Callable, Iterable, Optional

import numpy as np

from .wip import (
    FAILED,
    INSUFFICIENT,
    SUCCESS,
    CORE_VARS,
    MAGNITUDE_PRESENTATION_VARS,
    VAR_NAMES,
    Config as _LegacyConfig,
    Finding,
    InputShapeError,
    RowFailure,
    ValidationResult,
    Witness,
    detect_grid,
    render_report,
)

PCT_VARS = frozenset({"M", "P", "PB"})
ESTIMATE_REGION = frozenset({"V", "C", "G", "M"})
PROGRESS_REGION = frozenset({"D", "Q", "P", "E", "H", "R"})
BILLING_REGION = frozenset({"B", "N", "U", "O", "RB", "PB"})
REGIONS = {
    "estimate": ESTIMATE_REGION,
    "progress": PROGRESS_REGION,
    "billing": BILLING_REGION,
}
VAR_ORDER = tuple(VAR_NAMES)
VAR_BIT = {var: 1 << i for i, var in enumerate(VAR_ORDER)}


@dataclass
class Config(_LegacyConfig):
    """Compatibility plus the small set of wip2 search controls."""

    discovery_rows: int = 64
    discovery_v_hubs: int = 6
    discovery_estimate_pairs: int = 10
    discovery_d_keep: int = 7
    discovery_b_keep: int = 6
    discovery_states: int = 48
    match_batch: int = 32
    min_region_checkable: int = 1


# ---------------------------------------------------------------------------
# Constraint registry: identities are evidence; derivations are computation.
# ---------------------------------------------------------------------------


ArrayFn = Callable[..., np.ndarray]
TolFn = Callable[[tuple[np.ndarray, ...], tuple[np.ndarray, ...]], np.ndarray]


def _tol_sum(
    values: tuple[np.ndarray, ...], tolerances: tuple[np.ndarray, ...]
) -> np.ndarray:
    del values
    return np.sum(np.stack(tolerances), axis=0)


def _tol_mul(
    values: tuple[np.ndarray, ...], tolerances: tuple[np.ndarray, ...]
) -> np.ndarray:
    a, b = values
    ta, tb = tolerances
    return np.abs(b) * ta + np.abs(a) * tb + ta * tb


def _tol_div(
    values: tuple[np.ndarray, ...], tolerances: tuple[np.ndarray, ...]
) -> np.ndarray:
    a, b = values
    ta, tb = tolerances
    floor = np.maximum(np.abs(b) - tb, 1e-12)
    return ta / floor + (np.abs(a) + ta) * tb / (floor * floor)


def _tol_mul_div(
    values: tuple[np.ndarray, ...], tolerances: tuple[np.ndarray, ...]
) -> np.ndarray:
    a, b, c = values
    ta, tb, tc = tolerances
    product = a * b
    product_tol = np.abs(b) * ta + np.abs(a) * tb + ta * tb
    return _tol_div((product, c), (product_tol, tc))


def _tol_one_minus_mul(
    values: tuple[np.ndarray, ...], tolerances: tuple[np.ndarray, ...]
) -> np.ndarray:
    a, p = values
    ta, tp = tolerances
    return np.abs(1.0 - p) * ta + np.abs(a) * tp + ta * tp


@dataclass(frozen=True)
class Derivation:
    id: str
    identity_id: str
    out: str
    inputs: tuple[str, ...]
    fn: ArrayFn
    tol_fn: TolFn
    kind: str
    phases: frozenset[str] = frozenset(
        {"identify", "evidence", "certify", "repair"}
    )
    clipped: bool = False


@dataclass(frozen=True)
class Identity:
    id: str
    variables: tuple[str, ...]
    region: str
    derivations: tuple[Derivation, ...]


def _d(
    identity: str,
    out: str,
    inputs: tuple[str, ...],
    fn: ArrayFn,
    tol_fn: TolFn,
    kind: str = "money",
    *,
    phases: Iterable[str] = ("identify", "evidence", "certify", "repair"),
    clipped: bool = False,
) -> Derivation:
    return Derivation(
        id=f"{out}_from_{'_'.join(inputs)}",
        identity_id=identity,
        out=out,
        inputs=inputs,
        fn=fn,
        tol_fn=tol_fn,
        kind=kind,
        phases=frozenset(phases),
        clipped=clipped,
    )


def _identity_registry() -> tuple[Identity, ...]:
    I = []
    I.append(
        Identity(
            "estimate_complement",
            ("V", "C", "G"),
            "estimate",
            (
                _d("estimate_complement", "V", ("C", "G"), lambda C, G: C + G, _tol_sum),
                _d("estimate_complement", "C", ("V", "G"), lambda V, G: V - G, _tol_sum),
                _d("estimate_complement", "G", ("V", "C"), lambda V, C: V - C, _tol_sum),
            ),
        )
    )
    I.append(
        Identity(
            "cost_completion",
            ("C", "D", "Q"),
            "progress",
            (
                _d("cost_completion", "C", ("D", "Q"), lambda D, Q: D + Q, _tol_sum),
                _d("cost_completion", "D", ("C", "Q"), lambda C, Q: C - Q, _tol_sum),
                _d("cost_completion", "Q", ("C", "D"), lambda C, D: C - D, _tol_sum),
            ),
        )
    )
    I.append(
        Identity(
            "earned_revenue",
            ("E", "V", "D", "C"),
            "progress",
            (
                _d("earned_revenue", "E", ("V", "D", "C"), lambda V, D, C: V * D / C, _tol_mul_div),
                _d("earned_revenue", "D", ("E", "C", "V"), lambda E, C, V: E * C / V, _tol_mul_div),
                _d("earned_revenue", "C", ("V", "D", "E"), lambda V, D, E: V * D / E, _tol_mul_div),
                _d("earned_revenue", "V", ("E", "C", "D"), lambda E, C, D: E * C / D, _tol_mul_div),
            ),
        )
    )
    I.append(
        Identity(
            "earned_profit",
            ("H", "E", "D"),
            "progress",
            (
                _d("earned_profit", "H", ("E", "D"), lambda E, D: E - D, _tol_sum),
                _d("earned_profit", "E", ("H", "D"), lambda H, D: H + D, _tol_sum),
                _d("earned_profit", "D", ("E", "H"), lambda E, H: E - H, _tol_sum),
            ),
        )
    )
    I.append(
        Identity(
            "net_billing",
            ("N", "E", "B"),
            "billing",
            (
                _d("net_billing", "N", ("E", "B"), lambda E, B: E - B, _tol_sum),
                _d("net_billing", "E", ("N", "B"), lambda N, B: N + B, _tol_sum),
                _d("net_billing", "B", ("E", "N"), lambda E, N: E - N, _tol_sum),
            ),
        )
    )
    I.append(
        Identity(
            "billing_split",
            ("E", "B", "U", "O"),
            "billing",
            (
                _d("billing_split", "U", ("E", "B"), lambda E, B: np.maximum(E - B, 0.0), _tol_sum, clipped=True),
                _d("billing_split", "O", ("E", "B"), lambda E, B: np.maximum(B - E, 0.0), _tol_sum, clipped=True),
                _d("billing_split", "E", ("B", "U", "O"), lambda B, U, O: B + U - O, _tol_sum),
                _d("billing_split", "B", ("E", "U", "O"), lambda E, U, O: E - U + O, _tol_sum),
            ),
        )
    )
    I.append(
        Identity(
            "backlog",
            ("R", "V", "E"),
            "progress",
            (
                _d("backlog", "R", ("V", "E"), lambda V, E: V - E, _tol_sum),
                _d("backlog", "V", ("R", "E"), lambda R, E: R + E, _tol_sum),
                _d("backlog", "E", ("V", "R"), lambda V, R: V - R, _tol_sum),
            ),
        )
    )
    I.append(
        Identity(
            "remaining_billings",
            ("RB", "V", "B"),
            "billing",
            (
                _d("remaining_billings", "RB", ("V", "B"), lambda V, B: V - B, _tol_sum),
                _d("remaining_billings", "V", ("RB", "B"), lambda RB, B: RB + B, _tol_sum),
                _d("remaining_billings", "B", ("V", "RB"), lambda V, RB: V - RB, _tol_sum),
            ),
        )
    )
    I.append(
        Identity(
            "margin",
            ("M", "G", "V"),
            "estimate",
            (
                _d("margin", "M", ("G", "V"), lambda G, V: G / V, _tol_div, "pct"),
                _d("margin", "G", ("V", "M"), lambda V, M: V * M, _tol_mul),
                _d("margin", "V", ("G", "M"), lambda G, M: G / M, _tol_div),
            ),
        )
    )
    I.append(
        Identity(
            "percent_complete_cost",
            ("P", "D", "C"),
            "progress",
            (
                _d("percent_complete_cost", "P", ("D", "C"), lambda D, C: D / C, _tol_div, "pct"),
                _d("percent_complete_cost", "D", ("C", "P"), lambda C, P: C * P, _tol_mul),
                _d("percent_complete_cost", "C", ("D", "P"), lambda D, P: D / P, _tol_div),
            ),
        )
    )
    I.append(
        Identity(
            "percent_complete_revenue",
            ("P", "E", "V"),
            "progress",
            (
                _d("percent_complete_revenue", "P", ("E", "V"), lambda E, V: E / V, _tol_div, "pct"),
                _d("percent_complete_revenue", "E", ("V", "P"), lambda V, P: V * P, _tol_mul),
                _d("percent_complete_revenue", "V", ("E", "P"), lambda E, P: E / P, _tol_div),
            ),
        )
    )
    I.append(
        Identity(
            "percent_billed",
            ("PB", "B", "V"),
            "billing",
            (
                _d("percent_billed", "PB", ("B", "V"), lambda B, V: B / V, _tol_div, "pct"),
                _d("percent_billed", "B", ("V", "PB"), lambda V, PB: V * PB, _tol_mul),
                _d("percent_billed", "V", ("B", "PB"), lambda B, PB: B / PB, _tol_div),
            ),
        )
    )
    return tuple(I)


IDENTITIES = _identity_registry()
DERIVATIONS = tuple(d for identity in IDENTITIES for d in identity.derivations)
DERIVATIONS_BY_OUT = {
    var: tuple(d for d in DERIVATIONS if d.out == var) for var in VAR_ORDER
}


# ---------------------------------------------------------------------------
# Immutable prepared table and batched matcher
# ---------------------------------------------------------------------------


def _readonly(a: np.ndarray) -> np.ndarray:
    a = np.ascontiguousarray(a)
    a.flags.writeable = False
    return a


def _ingest(columns, job_labels):
    if columns is None:
        raise InputShapeError("columns is None")
    if isinstance(columns, np.ndarray):
        if columns.ndim != 2:
            raise InputShapeError(
                f"expected a 2-D array of shape (rows, cols); got ndim={columns.ndim}"
            )
        matrix = np.asarray(columns, dtype=float)
    else:
        arrays = []
        for i, column in enumerate(columns):
            a = np.asarray(column, dtype=float)
            if a.ndim != 1:
                raise InputShapeError(f"column {i} is not 1-D (ndim={a.ndim})")
            arrays.append(a)
        if not arrays:
            matrix = np.empty((0, 0), dtype=float)
        else:
            n = arrays[0].size
            if any(a.size != n for a in arrays):
                raise InputShapeError("all columns must have the same row count")
            matrix = np.column_stack(arrays)
    n = matrix.shape[0]
    if job_labels is None:
        labels = [f"Job {i + 1}" for i in range(n)]
    else:
        labels = [str(label) for label in list(job_labels)]
        if len(labels) != n:
            raise InputShapeError(
                f"job_labels has {len(labels)} entries for {n} rows"
            )
    return matrix, labels


@dataclass(frozen=True)
class PreparedTable:
    full: np.ndarray
    identify: np.ndarray
    row_index: np.ndarray
    raw: np.ndarray
    magnitude: np.ndarray
    ratio: np.ndarray
    whole_percent: np.ndarray
    finite: np.ndarray
    active: np.ndarray
    positive: np.ndarray
    negative: np.ndarray
    med_abs: np.ndarray
    duplicate_representative: np.ndarray
    representatives: tuple[int, ...]
    grids_ratio: tuple[Optional[float], ...]
    grids_whole: tuple[Optional[float], ...]

    @classmethod
    def build(cls, matrix: np.ndarray, cfg: Config) -> "PreparedTable":
        full = _readonly(np.asarray(matrix, dtype=float))
        complete_rows = np.all(np.isfinite(full), axis=1)
        row_index = np.flatnonzero(complete_rows)
        identify = _readonly(full[complete_rows])
        raw = _readonly(identify.T)
        magnitude = _readonly(np.abs(raw))
        ratio = raw
        whole = _readonly(raw / 100.0)
        finite = _readonly(np.isfinite(raw))
        active = _readonly(magnitude > cfg.money_obs_tol + cfg.cert_slack)
        positive = _readonly(raw > cfg.money_obs_tol)
        negative = _readonly(raw < -cfg.money_obs_tol)
        med_abs = _readonly(np.nanmedian(magnitude, axis=1))

        by_bytes: dict[tuple, int] = {}
        reps = np.empty(raw.shape[0], dtype=int)
        for col, values in enumerate(raw):
            key = (values.dtype.str, values.shape, values.tobytes())
            representative = by_bytes.setdefault(key, col)
            reps[col] = representative
        representatives = tuple(int(c) for c in np.flatnonzero(reps == np.arange(raw.shape[0])))
        grids_ratio = tuple(detect_grid(raw[c]) for c in range(raw.shape[0]))
        grids_whole = tuple(detect_grid(whole[c]) for c in range(raw.shape[0]))
        return cls(
            full=full,
            identify=identify,
            row_index=_readonly(row_index),
            raw=raw,
            magnitude=magnitude,
            ratio=ratio,
            whole_percent=whole,
            finite=finite,
            active=active,
            positive=positive,
            negative=negative,
            med_abs=med_abs,
            duplicate_representative=_readonly(reps),
            representatives=representatives,
            grids_ratio=grids_ratio,
            grids_whole=grids_whole,
        )


@dataclass(frozen=True)
class NumericValue:
    id: int
    variable: str
    values: np.ndarray
    tolerance: np.ndarray
    support: int
    column: Optional[int] = None
    scale: float = 1.0
    grid: Optional[float] = None
    derivation: Optional[str] = None
    inputs: tuple[int, ...] = ()


@dataclass(frozen=True)
class Prediction:
    derivation: Derivation
    value: NumericValue


@dataclass(frozen=True)
class Match:
    prediction: Prediction
    column: int
    strict_bad: int
    loose_bad: int
    residual: float
    scale: float
    grid: Optional[float]


class Matcher:
    def __init__(self, table: PreparedTable, cfg: Config):
        self.table = table
        self.cfg = cfg
        self.calls = 0
        self.predictions_scored = 0

    def _score_representation(
        self,
        predictions: list[Prediction],
        columns: np.ndarray,
        observed: np.ndarray,
        observed_tolerance: np.ndarray,
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        p = np.stack([item.value.values for item in predictions])
        pt = np.stack([item.value.tolerance for item in predictions])
        residual = np.abs(p[:, None, :] - observed[columns][None, :, :])
        strict = (
            pt[:, None, :]
            + observed_tolerance[None, :, None]
            + 1e-9
        )
        money = predictions[0].derivation.kind == "money"
        if money:
            strict = (
                strict
                + self.cfg.cert_slack
                + self.cfg.cert_money_rel * np.abs(p[:, None, :])
            )
            loose = strict + np.maximum(
                self.cfg.ident_abs,
                self.cfg.ident_rel * np.abs(p[:, None, :]),
            )
        else:
            loose = strict + self.cfg.pct_ident_slack
        return (
            np.sum(residual > strict, axis=2),
            np.sum(residual > loose, axis=2),
            np.sum(np.minimum(residual, loose), axis=2),
        )

    def score_many(
        self, predictions: list[Prediction], available: Iterable[int]
    ) -> list[Match]:
        if not predictions:
            return []
        columns = np.asarray(sorted(set(available)), dtype=int)
        if columns.size == 0:
            return []
        self.calls += 1
        self.predictions_scored += len(predictions)
        result: list[Match] = []

        for kind in ("money", "pct"):
            group = [p for p in predictions if p.derivation.kind == kind]
            if not group:
                continue
            for start in range(0, len(group), self.cfg.match_batch):
                chunk = group[start : start + self.cfg.match_batch]
                if kind == "money":
                    ordinary = self.table.raw
                    magnitude = self.table.magnitude
                    # U/O are presentation magnitudes; other money is signed.
                    for use_magnitude in (False, True):
                        selected = [
                            p
                            for p in chunk
                            if (p.derivation.out in MAGNITUDE_PRESENTATION_VARS)
                            == use_magnitude
                        ]
                        if not selected:
                            continue
                        obs = magnitude if use_magnitude else ordinary
                        otol = np.full(columns.size, self.cfg.money_obs_tol)
                        sb, lb, rs = self._score_representation(
                            selected, columns, obs, otol
                        )
                        self._append_matches(
                            result, selected, columns, sb, lb, rs, 1.0, None
                        )
                else:
                    choices = []
                    for scale, obs, grids in (
                        (1.0, self.table.ratio, self.table.grids_ratio),
                        (100.0, self.table.whole_percent, self.table.grids_whole),
                    ):
                        valid_cols = np.asarray(
                            [
                                c
                                for c in columns
                                if np.mean(
                                    (obs[c] >= -0.25) & (obs[c] <= 2.0)
                                )
                                >= 0.90
                            ],
                            dtype=int,
                        )
                        if valid_cols.size == 0:
                            continue
                        otol = np.asarray(
                            [
                                (grids[c] * self.cfg.pct_grid_mult)
                                if grids[c] is not None
                                else self.cfg.pct_default_tol
                                for c in valid_cols
                            ]
                        )
                        sb, lb, rs = self._score_representation(
                            chunk, valid_cols, obs, otol
                        )
                        choices.append((scale, valid_cols, grids, sb, lb, rs))
                    best: dict[tuple[int, int], Match] = {}
                    for scale, valid_cols, grids, sb, lb, rs in choices:
                        for pi, prediction in enumerate(chunk):
                            for ci, col in enumerate(valid_cols):
                                match = Match(
                                    prediction,
                                    int(col),
                                    int(sb[pi, ci]),
                                    int(lb[pi, ci]),
                                    float(rs[pi, ci]),
                                    scale,
                                    grids[int(col)],
                                )
                                key = (id(prediction), int(col))
                                old = best.get(key)
                                if old is None or _match_key(match) < _match_key(old):
                                    best[key] = match
                    result.extend(best.values())
        return result

    @staticmethod
    def _append_matches(
        result,
        predictions,
        columns,
        strict_bad,
        loose_bad,
        residual,
        scale,
        grids,
    ):
        for pi, prediction in enumerate(predictions):
            informative = np.sum(
                np.abs(prediction.value.values)
                > prediction.value.tolerance + 1e-9
            )
            if informative == 0:
                continue
            for ci, col in enumerate(columns):
                result.append(
                    Match(
                        prediction,
                        int(col),
                        int(strict_bad[pi, ci]),
                        int(loose_bad[pi, ci]),
                        float(residual[pi, ci]),
                        scale,
                        None if grids is None else grids[int(col)],
                    )
                )


def _match_key(match: Match):
    return (
        match.strict_bad,
        match.loose_bad,
        match.residual,
        match.column,
        match.prediction.derivation.id,
    )


def _allowed_bad(rows: int, cfg: Config) -> int:
    return max(1, int(np.floor((1.0 - cfg.ident_frac) * rows)))


def _strict_tolerance(value: NumericValue, cfg: Config) -> np.ndarray:
    if value.variable in PCT_VARS:
        return value.tolerance + 1e-9
    return (
        value.tolerance
        + cfg.money_obs_tol
        + cfg.cert_slack
        + cfg.cert_money_rel * np.abs(value.values)
    )


def _proofs_agree(
    a: NumericValue, b: NumericValue, cfg: Config, *, robust: bool = False
) -> bool:
    valid = np.isfinite(a.values) & np.isfinite(b.values)
    if int(valid.sum()) < cfg.min_informative_rows:
        return False
    tol = a.tolerance + b.tolerance + 1e-9
    if a.variable not in PCT_VARS:
        tol = (
            tol
            + cfg.cert_slack
            + cfg.cert_money_rel * np.maximum(np.abs(a.values), np.abs(b.values))
        )
    if robust:
        tol = tol + (
            cfg.pct_ident_slack
            if a.variable in PCT_VARS
            else np.maximum(cfg.ident_abs, cfg.ident_rel * np.abs(a.values))
        )
    bad = int(np.sum(np.abs(a.values[valid] - b.values[valid]) > tol[valid]))
    return bad <= (_allowed_bad(int(valid.sum()), cfg) if robust else 0)


# ---------------------------------------------------------------------------
# Motif discovery and canonical graph-state assembly
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SeedState:
    assignments: tuple[tuple[str, int], ...]
    motif_score: int
    residual: float
    sources: frozenset[str] = frozenset()

    @property
    def mapping(self) -> dict[str, int]:
        return dict(self.assignments)


def _canonical(assignments: dict[str, int]) -> tuple[tuple[str, int], ...]:
    return tuple(sorted(assignments.items(), key=lambda item: VAR_ORDER.index(item[0])))


def _money_fit(pred: np.ndarray, obs: np.ndarray, cfg: Config):
    strict = (
        2 * cfg.money_obs_tol
        + cfg.cert_slack
        + cfg.cert_money_rel * np.abs(pred)
    )
    loose = strict + np.maximum(cfg.ident_abs, cfg.ident_rel * np.abs(pred))
    residual = np.abs(pred - obs)
    return (
        int(np.sum(residual > strict)),
        int(np.sum(residual > loose)),
        float(np.sum(np.minimum(residual, loose))),
    )


def _sample_rows(table: PreparedTable, cfg: Config) -> np.ndarray:
    n = table.raw.shape[1]
    if n <= cfg.discovery_rows:
        return np.arange(n)
    return np.unique(np.linspace(0, n - 1, cfg.discovery_rows).astype(int))


def _estimate_motifs(table: PreparedTable, cfg: Config) -> list[SeedState]:
    reps = table.representatives
    if len(reps) < 2:
        return []
    rows = _sample_rows(table, cfg)
    raw = table.raw[:, rows]
    positive_rate = np.mean(raw > -cfg.money_obs_tol, axis=1)
    hubs = sorted(
        (c for c in reps if positive_rate[c] >= cfg.prior_robust_frac),
        key=lambda c: (-table.med_abs[c], c),
    )[: cfg.discovery_v_hubs]
    allowed = _allowed_bad(len(rows), cfg)
    candidates: dict[tuple[tuple[str, int], ...], SeedState] = {}

    for vcol in hubs:
        V = raw[vcol]
        for acol, bcol in combinations((c for c in reps if c != vcol), 2):
            sb, lb, residual = _money_fit(
                V, raw[acol] + raw[bcol], cfg
            )
            if lb > allowed:
                continue
            ratios = []
            for ccol, gcol in ((acol, bcol), (bcol, acol)):
                with np.errstate(divide="ignore", invalid="ignore"):
                    ratio = raw[ccol] / V
                finite = ratio[np.isfinite(ratio)]
                if finite.size == 0:
                    continue
                med = float(np.median(finite))
                spread = float(np.percentile(finite, 75) - np.percentile(finite, 25))
                if (
                    cfg.cost_ratio_band[0] <= med <= cfg.cost_ratio_band[1]
                    and spread <= cfg.estimate_iqr_max
                ):
                    ratios.append((ccol, gcol))
            if not ratios:
                # The larger stable component is the least-assumptive
                # orientation when a nonstandard contractor falls outside the
                # usual cost-ratio band.
                pair = sorted((acol, bcol), key=lambda c: -table.med_abs[c])
                ratios.append((pair[0], pair[1]))
            for ccol, gcol in ratios:
                key = _canonical({"V": vcol, "C": ccol, "G": gcol})
                state = SeedState(
                    key,
                    motif_score=4 if sb == 0 else 3,
                    residual=residual,
                    sources=frozenset({"estimate_complement"}),
                )
                old = candidates.get(key)
                if old is None or (state.motif_score, -state.residual) > (
                    old.motif_score,
                    -old.residual,
                ):
                    candidates[key] = state

    # Sparse fallback: stable C/V pairs remain proposals, never proof.
    if not candidates:
        for vcol in hubs[:3]:
            V = raw[vcol]
            for ccol in reps:
                if ccol == vcol:
                    continue
                with np.errstate(divide="ignore", invalid="ignore"):
                    ratio = raw[ccol] / V
                finite = ratio[np.isfinite(ratio)]
                if finite.size == 0:
                    continue
                med = float(np.median(finite))
                spread = float(np.percentile(finite, 75) - np.percentile(finite, 25))
                if (
                    cfg.cost_ratio_band[0] <= med <= cfg.cost_ratio_band[1]
                    and spread <= cfg.estimate_iqr_max
                ):
                    key = _canonical({"V": vcol, "C": ccol})
                    candidates[key] = SeedState(
                        key, 1, spread, frozenset({"estimate_prior"})
                    )
    return sorted(
        candidates.values(),
        key=lambda state: (-state.motif_score, state.residual, state.assignments),
    )[: cfg.discovery_estimate_pairs]


def _observed_value(
    table: PreparedTable,
    variable: str,
    column: int,
    cfg: Config,
    value_id: int,
    *,
    full: bool = False,
    scale: float = 1.0,
    grid: Optional[float] = None,
) -> NumericValue:
    values = table.full[:, column] if full else table.raw[column]
    if scale != 1.0:
        values = values / scale
    if variable in MAGNITUDE_PRESENTATION_VARS:
        values = np.abs(values)
    if variable in PCT_VARS:
        if grid is None:
            grid = detect_grid(values)
        tol = (grid * cfg.pct_grid_mult) if grid is not None else cfg.pct_default_tol
    else:
        tol = cfg.money_obs_tol
    return NumericValue(
        value_id,
        variable,
        _readonly(np.asarray(values, dtype=float)),
        _readonly(np.full(np.asarray(values).shape, tol, dtype=float)),
        1 << column,
        column,
        scale,
        grid,
    )


def _derived_value(
    derivation: Derivation,
    inputs: tuple[NumericValue, ...],
    value_id: int,
) -> Optional[NumericValue]:
    values = tuple(v.values for v in inputs)
    tolerances = tuple(v.tolerance for v in inputs)
    with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
        out = np.asarray(derivation.fn(*values), dtype=float)
        tol = np.asarray(derivation.tol_fn(values, tolerances), dtype=float)
    if int(np.isfinite(out).sum()) == 0:
        return None
    return NumericValue(
        value_id,
        derivation.out,
        _readonly(out),
        _readonly(np.maximum(tol, 1e-12)),
        int(np.bitwise_or.reduce([v.support for v in inputs])),
        derivation=derivation.id,
        inputs=tuple(v.id for v in inputs),
    )


def _prediction_value(
    derivation: Derivation,
    known: dict[str, NumericValue],
    intern: dict[tuple, NumericValue],
    next_id: list[int],
) -> Optional[NumericValue]:
    inputs = tuple(known[var] for var in derivation.inputs)
    key = (derivation.id, tuple(v.id for v in inputs))
    value = intern.get(key)
    if value is None:
        value = _derived_value(derivation, inputs, next_id[0])
        next_id[0] += 1
        if value is not None:
            intern[key] = value
    return value


def _rank_prediction_groups(
    matcher: Matcher,
    groups: dict[int, list[tuple[Derivation, NumericValue]]],
    available: set[int],
    cfg: Config,
) -> dict[int, tuple[int, float]]:
    """Score every candidate graph fragment in one batched matcher query."""
    predictions = []
    owner = {}
    for group_id, specs in groups.items():
        for derivation, value in specs:
            prediction = Prediction(derivation, value)
            predictions.append(prediction)
            owner[id(prediction)] = group_id
    matches = matcher.score_many(predictions, available)
    allowed = _allowed_bad(matcher.table.raw.shape[1], cfg)
    best_by_group_out: dict[tuple[int, str], Match] = {}
    for match in matches:
        if match.loose_bad > allowed:
            continue
        group_id = owner[id(match.prediction)]
        # A proposed D or B column cannot corroborate itself under a different
        # output name.
        if match.column == group_id:
            continue
        out = match.prediction.derivation.out
        key = (group_id, out)
        old = best_by_group_out.get(key)
        if old is None or _match_key(match) < _match_key(old):
            best_by_group_out[key] = match
    ranked = {}
    for group_id in groups:
        selected = [
            match
            for (owner_id, _), match in best_by_group_out.items()
            if owner_id == group_id
        ]
        ranked[group_id] = (
            len(selected),
            sum(match.residual for match in selected),
        )
    return ranked


def _complete_seed_states(
    table: PreparedTable,
    estimates: list[SeedState],
    matcher: Matcher,
    cfg: Config,
) -> list[SeedState]:
    states: dict[tuple[tuple[str, int], ...], SeedState] = {}
    next_id = [1]
    all_reps = set(table.representatives)

    for estimate in estimates:
        base = estimate.mapping
        known = {
            var: _observed_value(table, var, col, cfg, next_id[0])
            for var, col in base.items()
        }
        next_id[0] += len(known)
        V, C = known["V"], known["C"]
        used = set(base.values())
        d_groups = {}
        d_values = {}
        for dcol in all_reps - used:
            D = _observed_value(table, "D", dcol, cfg, next_id[0])
            next_id[0] += 1
            with np.errstate(divide="ignore", invalid="ignore"):
                progress = D.values / C.values
            finite = progress[np.isfinite(progress)]
            if finite.size == 0:
                continue
            if (
                np.mean((finite >= -0.02) & (finite <= cfg.d_over_c_slack))
                < cfg.prior_robust_frac
                or float(np.median(np.abs(finite))) < cfg.anchor_live_med
            ):
                continue
            specs = []
            for derivation in (
                next(d for d in DERIVATIONS if d.id == "Q_from_C_D"),
                next(d for d in DERIVATIONS if d.id == "E_from_V_D_C"),
                next(d for d in DERIVATIONS if d.id == "P_from_D_C"),
            ):
                local = {**known, "D": D}
                value = _prediction_value(derivation, local, {}, next_id)
                if value is not None:
                    specs.append((derivation, value))
            d_groups[dcol] = specs
            d_values[dcol] = D
        d_scores = _rank_prediction_groups(
            matcher, d_groups, all_reps - used, cfg
        )
        d_ranked = []
        for dcol, specs in d_groups.items():
            count, residual = d_scores[dcol]
            if count:
                d_ranked.append(
                    (-count, residual, dcol, d_values[dcol], specs)
                )
        d_ranked.sort(key=lambda item: (item[0], item[1], item[2]))

        for neg_count, d_residual, dcol, D, specs in d_ranked[: cfg.discovery_d_keep]:
            e_spec = next(
                (value for derivation, value in specs if derivation.out == "E"),
                None,
            )
            if e_spec is None:
                continue
            used_d = used | {dcol}
            b_groups = {}
            for bcol in all_reps - used_d:
                B = _observed_value(table, "B", bcol, cfg, next_id[0])
                next_id[0] += 1
                with np.errstate(divide="ignore", invalid="ignore"):
                    billed = B.values / V.values
                finite = billed[np.isfinite(billed)]
                if finite.size == 0:
                    continue
                if (
                    np.mean(
                        (finite >= -0.05) & (finite <= cfg.b_over_v_slack)
                    )
                    < cfg.prior_robust_frac
                    or float(np.median(np.abs(finite))) < cfg.anchor_live_med
                ):
                    continue
                local = {**known, "D": D, "E": e_spec, "B": B}
                b_specs = []
                for derivation in DERIVATIONS:
                    if derivation.id not in {
                        "N_from_E_B",
                        "U_from_E_B",
                        "O_from_E_B",
                        "RB_from_V_B",
                        "PB_from_B_V",
                    }:
                        continue
                    value = _prediction_value(derivation, local, {}, next_id)
                    if value is not None:
                        b_specs.append((derivation, value))
                b_groups[bcol] = b_specs
            b_scores = _rank_prediction_groups(
                matcher, b_groups, all_reps - used_d, cfg
            )
            b_ranked = []
            for bcol in b_groups:
                count, residual = b_scores[bcol]
                if count:
                    b_ranked.append((-count, residual, bcol))
            b_ranked.sort()
            for neg_b_count, b_residual, bcol in b_ranked[: cfg.discovery_b_keep]:
                mapping = {**base, "D": dcol, "B": bcol}
                key = _canonical(mapping)
                state = SeedState(
                    key,
                    estimate.motif_score - neg_count - neg_b_count,
                    estimate.residual + d_residual + b_residual,
                    estimate.sources
                    | frozenset({"progress_projection", "billing_bridge"}),
                )
                old = states.get(key)
                if old is None or (
                    state.motif_score,
                    -state.residual,
                ) > (old.motif_score, -old.residual):
                    states[key] = state
    return sorted(
        states.values(),
        key=lambda state: (-state.motif_score, state.residual, state.assignments),
    )[: cfg.discovery_states]


# ---------------------------------------------------------------------------
# One physical-first graph closure engine
# ---------------------------------------------------------------------------


@dataclass
class ClosedGraph:
    seed: SeedState
    known: dict[str, NumericValue]
    column_to_var: dict[int, str]
    physical_matches: list[Match]
    conflicts: list[str]
    matcher_calls: int
    derived_evaluations: int
    checkable: frozenset[str] = frozenset()
    coverage: dict[str, int] = field(default_factory=dict)
    active_derivations: frozenset[str] = frozenset()
    redundancy: int = 0
    minimum_seeds: int = 0


def _select_physical_matches(
    matches: list[Match],
    rows: int,
    cfg: Config,
    *,
    exact: bool,
) -> list[Match]:
    allowed = 0 if exact else _allowed_bad(rows, cfg)
    qualified = [
        m
        for m in matches
        if (m.strict_bad if exact else m.loose_bad) <= allowed
    ]
    if not qualified:
        return []
    by_prediction: dict[tuple[str, str], list[Match]] = {}
    by_column: dict[int, list[Match]] = {}
    for match in qualified:
        key = (
            match.prediction.derivation.out,
            match.prediction.derivation.id,
        )
        by_prediction.setdefault(key, []).append(match)
        by_column.setdefault(match.column, []).append(match)
    for values in by_prediction.values():
        values.sort(key=_match_key)
    for values in by_column.values():
        values.sort(key=_match_key)

    accepted = []
    used_vars = set()
    used_cols = set()
    # Mutual best matches remove almost every ordinary conflict without an
    # assignment solver.  Remaining overwhelmingly best matches are handled
    # by the same deterministic global order.
    for values in by_prediction.values():
        best = values[0]
        if by_column[best.column][0] is best:
            out = best.prediction.derivation.out
            if out not in used_vars and best.column not in used_cols:
                accepted.append(best)
                used_vars.add(out)
                used_cols.add(best.column)
    if accepted:
        return accepted
    for match in sorted(qualified, key=_match_key):
        out = match.prediction.derivation.out
        if out not in used_vars and match.column not in used_cols:
            accepted.append(match)
            used_vars.add(out)
            used_cols.add(match.column)
    return accepted[:1]


def _close_graph(
    table: PreparedTable, seed: SeedState, matcher: Matcher, cfg: Config
) -> ClosedGraph:
    known: dict[str, NumericValue] = {}
    column_to_var: dict[int, str] = {}
    next_id = [1]
    for var, col in seed.assignments:
        value = _observed_value(table, var, col, cfg, next_id[0])
        next_id[0] += 1
        known[var] = value
        column_to_var[col] = var

    intern: dict[tuple, NumericValue] = {}
    matches_used: list[Match] = []
    conflicts: list[str] = []
    representatives = set(table.representatives)

    for _ in range(len(VAR_ORDER) * 3):
        predictions: list[Prediction] = []
        agreeing_by_unknown: dict[str, list[Prediction]] = {}
        for derivation in DERIVATIONS:
            if "identify" not in derivation.phases:
                continue
            if not all(var in known for var in derivation.inputs):
                continue
            value = _prediction_value(derivation, known, intern, next_id)
            if value is None:
                continue
            prediction = Prediction(derivation, value)
            if derivation.out in known:
                if not _proofs_agree(value, known[derivation.out], cfg, robust=True):
                    conflicts.append(derivation.identity_id)
                continue
            predictions.append(prediction)
            agreeing_by_unknown.setdefault(derivation.out, []).append(prediction)

        if not predictions:
            break
        available = representatives - set(column_to_var)
        scored = matcher.score_many(predictions, available)
        accepted = _select_physical_matches(
            scored, table.raw.shape[1], cfg, exact=True
        )
        if not accepted:
            accepted = _select_physical_matches(
                scored, table.raw.shape[1], cfg, exact=False
            )
        if accepted:
            for match in accepted:
                var = match.prediction.derivation.out
                if var in known or match.column in column_to_var:
                    continue
                value = _observed_value(
                    table,
                    var,
                    match.column,
                    cfg,
                    next_id[0],
                    scale=match.scale,
                    grid=match.grid,
                )
                next_id[0] += 1
                known[var] = value
                column_to_var[match.column] = var
                matches_used.append(match)
            continue

        # Physical fixpoint: materialize the entire agreeing virtual frontier.
        made = False
        for var, candidates in sorted(agreeing_by_unknown.items()):
            values = [candidate.value for candidate in candidates]
            base = values[0]
            if all(_proofs_agree(base, other, cfg, robust=True) for other in values[1:]):
                known[var] = base
                made = True
            else:
                conflicts.append(f"conflicting derivations for {var}")
        if not made:
            break

    return ClosedGraph(
        seed=seed,
        known=known,
        column_to_var=column_to_var,
        physical_matches=matches_used,
        conflicts=conflicts,
        matcher_calls=matcher.calls,
        derived_evaluations=len(intern),
    )


# ---------------------------------------------------------------------------
# Numeric provenance, simultaneous leave-one-out, and graph redundancy
# ---------------------------------------------------------------------------


def _proof_analysis(graph: ClosedGraph, cfg: Config) -> ClosedGraph:
    physical = {
        var: value for var, value in graph.known.items() if value.column is not None
    }
    if not physical:
        return graph
    all_columns = 0
    for value in physical.values():
        all_columns |= 1 << int(value.column)
    independent = {
        var: all_columns & ~(1 << int(value.column))
        for var, value in physical.items()
    }
    for var in graph.known:
        independent.setdefault(var, 0)

    active: set[str] = set()
    changed = True
    while changed:
        changed = False
        for derivation in DERIVATIONS:
            if not all(var in graph.known for var in derivation.inputs):
                continue
            inputs = tuple(graph.known[var] for var in derivation.inputs)
            candidate = _derived_value(derivation, inputs, -1)
            target = graph.known.get(derivation.out)
            if candidate is None or target is None:
                continue
            if not _proofs_agree(candidate, target, cfg, robust=True):
                continue
            mask = all_columns
            for var in derivation.inputs:
                mask &= independent.get(var, 0)
            active.add(derivation.id)
            new = independent.get(derivation.out, 0) | mask
            if new != independent.get(derivation.out, 0):
                independent[derivation.out] = new
                changed = True

    checkable = frozenset(
        var
        for var, value in physical.items()
        if independent.get(var, 0) & (1 << int(value.column))
    )
    coverage = {
        region: len(checkable & variables) for region, variables in REGIONS.items()
    }
    minimum = _minimum_seed_count(physical, active)
    graph.checkable = checkable
    graph.coverage = coverage
    graph.active_derivations = frozenset(active)
    graph.minimum_seeds = minimum
    graph.redundancy = max(0, len(physical) - minimum)
    return graph


def _minimum_seed_count(
    physical: dict[str, NumericValue], active_derivations: set[str]
) -> int:
    observed = tuple(sorted(physical, key=VAR_ORDER.index))
    target = 0
    for var in observed:
        target |= VAR_BIT[var]
    active = tuple(d for d in DERIVATIONS if d.id in active_derivations)
    cache: dict[int, int] = {}

    def closure(seed: int) -> int:
        known = cache.get(seed)
        if known is not None:
            return known
        known = seed
        changed = True
        while changed:
            changed = False
            for derivation in active:
                required = 0
                for var in derivation.inputs:
                    required |= VAR_BIT[var]
                if known & required == required:
                    new = known | VAR_BIT[derivation.out]
                    if new != known:
                        known = new
                        changed = True
        cache[seed] = known
        return known

    for size in range(len(observed) + 1):
        for selected in combinations(observed, size):
            seed = 0
            for var in selected:
                seed |= VAR_BIT[var]
            if closure(seed) & target == target:
                return size
    return len(observed)


def _coverage_ok(graph: ClosedGraph, cfg: Config) -> bool:
    return all(
        graph.coverage.get(region, 0) >= cfg.min_region_checkable
        for region in REGIONS
    )


# ---------------------------------------------------------------------------
# Strict full-document certification and graph-powered diagnosis
# ---------------------------------------------------------------------------


def _physical_full_values(
    table: PreparedTable, graph: ClosedGraph, cfg: Config
) -> dict[str, NumericValue]:
    values = {}
    next_id = 1
    for var, identified in graph.known.items():
        if identified.column is None:
            continue
        values[var] = _observed_value(
            table,
            var,
            int(identified.column),
            cfg,
            next_id,
            full=True,
            scale=identified.scale,
            grid=identified.grid,
        )
        next_id += 1
    return values


def _reconstruct_without(
    target: str,
    physical: dict[str, NumericValue],
    active: frozenset[str],
    cfg: Config,
) -> tuple[Optional[NumericValue], list[str]]:
    known = {var: value for var, value in physical.items() if var != target}
    next_id = [max((v.id for v in known.values()), default=0) + 1]
    intern: dict[tuple, NumericValue] = {}
    proof = []
    for _ in range(len(VAR_ORDER) * 2):
        pending: dict[str, list[tuple[Derivation, NumericValue]]] = {}
        for derivation in DERIVATIONS:
            if derivation.id not in active:
                continue
            if derivation.out in known:
                continue
            if not all(var in known for var in derivation.inputs):
                continue
            value = _prediction_value(derivation, known, intern, next_id)
            if value is not None:
                pending.setdefault(derivation.out, []).append((derivation, value))
        if not pending:
            break
        made = False
        for var, candidates in pending.items():
            base_d, base = candidates[0]
            agreeing = [
                (d, value)
                for d, value in candidates
                if _proofs_agree(base, value, cfg, robust=True)
            ]
            if len(agreeing) != len(candidates):
                continue
            known[var] = base
            if var == target:
                proof = [base_d.id]
            made = True
        if target in known:
            return known[target], proof
        if not made:
            break
    return None, []


def _certify(
    table: PreparedTable,
    labels: list[str],
    graph: ClosedGraph,
    cfg: Config,
) -> tuple[list[Witness], list[RowFailure], list[dict]]:
    physical = _physical_full_values(table, graph, cfg)
    witnesses: list[Witness] = []
    failures: list[RowFailure] = []
    incomplete: list[dict] = []

    for var in sorted(graph.checkable, key=VAR_ORDER.index):
        observed = physical[var]
        expected, proof = _reconstruct_without(
            var, physical, graph.active_derivations, cfg
        )
        if expected is None:
            continue
        observed_values = (
            np.abs(observed.values)
            if var in MAGNITUDE_PRESENTATION_VARS
            else observed.values
        )
        expected_values = expected.values
        valid = np.isfinite(observed_values) & np.isfinite(expected_values)
        if var in PCT_VARS:
            tolerance = observed.tolerance + expected.tolerance + 1e-9
        else:
            tolerance = (
                observed.tolerance
                + expected.tolerance
                + cfg.cert_slack
                + cfg.cert_money_rel * np.abs(expected_values)
            )
        residual = np.abs(observed_values - expected_values)
        relation = proof[0] if proof else f"{var} reconstructed from graph"
        identity_id = next(
            (
                derivation.identity_id
                for derivation in DERIVATIONS
                if derivation.id == relation
            ),
            "grounded_graph",
        )
        witnesses.append(
            Witness(
                relation=relation,
                business_form=f"{VAR_NAMES[var]} reconstructed independently",
                column=observed.column,
                n_rows=int(valid.sum()),
                n_informative=int(
                    np.sum(valid & (np.abs(expected_values) > tolerance + 1e-9))
                ),
                max_abs_residual=float(
                    residual[valid].max(initial=0.0)
                ),
                weight=1.0,
                family=identity_id,
            )
        )
        for row in np.flatnonzero(valid & (residual > tolerance)):
            failures.append(
                RowFailure(
                    relation=relation,
                    business_form=f"{VAR_NAMES[var]} reconstructed independently",
                    variable=var,
                    column=observed.column,
                    row_index=int(row),
                    row_label=labels[int(row)],
                    observed=float(observed_values[row]),
                    expected=float(expected_values[row]),
                    difference=float(observed_values[row] - expected_values[row]),
                    tolerance=float(tolerance[row]),
                )
            )
        missing = np.flatnonzero(~valid)
        if missing.size:
            incomplete.append(
                {
                    "variable": var,
                    "column": observed.column,
                    "rows": [int(row) for row in missing],
                }
            )
    return witnesses, failures, incomplete


def _diagnose(failures: list[RowFailure]) -> list[Finding]:
    by_row: dict[int, list[RowFailure]] = {}
    for failure in failures:
        by_row.setdefault(failure.row_index, []).append(failure)
    findings = []
    for row, row_failures in sorted(by_row.items()):
        by_column: dict[Optional[int], list[RowFailure]] = {}
        for failure in row_failures:
            by_column.setdefault(failure.column, []).append(failure)
        if len(by_column) == 1:
            column, related = next(iter(by_column.items()))
            variable = related[0].variable
            proposed = float(np.median([failure.expected for failure in related]))
            findings.append(
                Finding(
                    row_index=row,
                    row_label=related[0].row_label,
                    culprit_column=column,
                    culprit_variable=variable,
                    candidate_variables=[variable],
                    exonerated_variables=[],
                    observed=float(related[0].observed),
                    proposed_correction=proposed,
                    correction_basis=sorted({f.relation for f in related}),
                    confidence="high",
                    classification="internally inconsistent value",
                    classification_detail=(
                        "The same constraint graph reconstructs this printed "
                        "cell without using it."
                    ),
                    transplant_sources=[],
                    failing_relations=sorted({f.relation for f in related}),
                    proof_kind="inherited",
                )
            )
        else:
            variables = sorted({failure.variable for failure in row_failures})
            findings.append(
                Finding(
                    row_index=row,
                    row_label=row_failures[0].row_label,
                    culprit_column=None,
                    culprit_variable=None,
                    candidate_variables=variables,
                    exonerated_variables=[],
                    observed=None,
                    proposed_correction=None,
                    correction_basis=[],
                    confidence="low",
                    classification="multiple inconsistent observations",
                    classification_detail=(
                        "More than one printed observation fails independent "
                        "reconstruction on this row."
                    ),
                    transplant_sources=[],
                    failing_relations=sorted(
                        {failure.relation for failure in row_failures}
                    ),
                    proof_kind="joint",
                )
            )
    return findings


# ---------------------------------------------------------------------------
# Public orchestration
# ---------------------------------------------------------------------------


def validate_wip(columns, job_labels=None, config=None) -> ValidationResult:
    """Identify and validate a numeric WIP table without reading headers."""

    cfg = config if config is not None else Config()
    matrix, labels = _ingest(columns, job_labels)
    diagnostics = {
        "engine": "wip2_constraint_graph",
        "notes": [],
        "prepared_once": True,
    }
    if matrix.shape[1] == 0:
        return ValidationResult(
            status=INSUFFICIENT,
            reason="empty input: no columns provided",
            diagnostics=diagnostics,
        )
    if matrix.shape[1] < 4:
        return ValidationResult(
            status=INSUFFICIENT,
            reason=(
                f"only {matrix.shape[1]} column(s); too few physical "
                "observations to ground estimate, progress, and billing"
            ),
            diagnostics=diagnostics,
        )
    complete = np.all(np.isfinite(matrix), axis=1)
    if int(complete.sum()) < cfg.min_rows:
        return ValidationResult(
            status=INSUFFICIENT,
            reason=(
                f"only {int(complete.sum())} usable row(s) of {matrix.shape[0]}; "
                f"need at least {cfg.min_rows}"
            ),
            diagnostics=diagnostics,
        )
    if int((~complete).sum()):
        diagnostics["notes"].append(
            f"{int((~complete).sum())} row(s) excluded from identification; "
            "retained for full-document certification"
        )

    table = PreparedTable.build(matrix, cfg)
    matcher = Matcher(table, cfg)
    estimates = _estimate_motifs(table, cfg)
    seeds = _complete_seed_states(table, estimates, matcher, cfg)
    diagnostics["discovery"] = {
        "representative_columns": len(table.representatives),
        "duplicate_columns": matrix.shape[1] - len(table.representatives),
        "estimate_motifs": len(estimates),
        "canonical_seed_states": len(seeds),
    }
    if not seeds:
        return ValidationResult(
            status=INSUFFICIENT,
            reason=(
                "could not assemble a coherent estimate/progress/billing "
                "constraint graph from the observed columns"
            ),
            diagnostics=diagnostics,
        )

    completed = []
    base_calls = matcher.calls
    for seed in seeds:
        graph = _proof_analysis(_close_graph(table, seed, matcher, cfg), cfg)
        completed.append(graph)
    diagnostics["graph_states_closed"] = len(completed)
    diagnostics["matcher"] = {
        "batched_calls": matcher.calls,
        "closure_calls": matcher.calls - base_calls,
        "predictions_scored": matcher.predictions_scored,
    }

    coherent = [
        graph
        for graph in completed
        if all(var in graph.known for var in CORE_VARS)
        and not any(conflict.startswith("conflicting") for conflict in graph.conflicts)
    ]
    if not coherent:
        return ValidationResult(
            status=INSUFFICIENT,
            reason="candidate graphs did not close to the required semantic core",
            diagnostics=diagnostics,
        )
    coherent.sort(
        key=lambda graph: (
            not _coverage_ok(graph, cfg),
            -graph.redundancy,
            -len(graph.column_to_var),
            len(graph.conflicts),
            -graph.seed.motif_score,
            graph.seed.residual,
            graph.seed.assignments,
        )
    )
    best = coherent[0]
    if not _coverage_ok(best, cfg):
        diagnostics["best_coverage"] = best.coverage
        diagnostics["uncertified_best_mapping"] = {
            col: var for col, var in sorted(best.column_to_var.items())
        }
        missing = [
            region
            for region in REGIONS
            if best.coverage.get(region, 0) < cfg.min_region_checkable
        ]
        return ValidationResult(
            status=INSUFFICIENT,
            reason=(
                "identifiable but not independently validatable across the "
                f"required business regions: {', '.join(missing)}"
            ),
            diagnostics=diagnostics,
        )

    # Equal, clean graph evidence is genuine ambiguity.  Defer the final
    # decision until strict certification has had a chance to refute rivals.
    best_rank = (best.redundancy, len(best.column_to_var))
    witnesses, failures, incomplete = _certify(table, labels, best, cfg)
    competing = None
    if not failures and not incomplete:
        for rival in coherent[1:]:
            if (rival.redundancy, len(rival.column_to_var)) != best_rank:
                break
            if rival.column_to_var == best.column_to_var:
                continue
            _, rival_failures, rival_incomplete = _certify(
                table, labels, rival, cfg
            )
            if not rival_failures and not rival_incomplete:
                competing = {
                    col: var for col, var in sorted(rival.column_to_var.items())
                }
                break

    mapping = {col: var for col, var in sorted(best.column_to_var.items())}
    diagnostics.update(
        {
            "evidence": {
                "physical_observations": len(mapping),
                "minimum_generating_seeds": best.minimum_seeds,
                "grounded_graph_redundancy": best.redundancy,
                "checkable_columns": sorted(best.checkable),
                "business_region_coverage": best.coverage,
            },
            "winning_sources": sorted(best.seed.sources),
            "derived_vectors": best.derived_evaluations,
            "conflicts_seen": sorted(set(best.conflicts)),
        }
    )
    if competing is not None:
        return ValidationResult(
            status=INSUFFICIENT,
            reason=(
                "irreducibly ambiguous: two distinct physical-to-semantic "
                "graphs have equal grounded redundancy and both certify"
            ),
            mapping=mapping,
            mapping_named={col: VAR_NAMES[var] for col, var in mapping.items()},
            competing_mapping=competing,
            diagnostics=diagnostics,
        )

    findings = _diagnose(failures)
    if incomplete:
        diagnostics["incomplete_full_document_checks"] = incomplete
    failed = bool(failures or incomplete)
    orientation = ""
    if "C" in best.known and best.known["C"].column is not None:
        orientation = (
            f"estimate column (col {best.known['C'].column}) read as "
            f"{VAR_NAMES['C']} (C)"
        )
    virtuals = {
        var: value.derivation or "constraint-graph closure"
        for var, value in best.known.items()
        if value.column is None
    }
    reason = ""
    if failed:
        parts = []
        if failures:
            parts.append(f"{len(failures)} row-level identity violation(s)")
        if incomplete:
            parts.append(
                f"{len(incomplete)} independently checkable relation(s) "
                "could not be evaluated on every row"
            )
        reason = "; ".join(parts) + "; the schedule is not fully internally certified"

    return ValidationResult(
        status=FAILED if failed else SUCCESS,
        reason=reason,
        mapping=mapping,
        mapping_named={col: VAR_NAMES[var] for col, var in mapping.items()},
        estimate_orientation=orientation,
        virtuals=virtuals,
        core={
            var: best.known[var].values.copy()
            for var in CORE_VARS
            if var in best.known
        },
        row_index=table.row_index.copy(),
        witnesses=witnesses,
        failures=failures,
        findings=findings,
        diagnostics=diagnostics,
    )


__all__ = [
    "Config",
    "FAILED",
    "INSUFFICIENT",
    "SUCCESS",
    "Finding",
    "InputShapeError",
    "RowFailure",
    "ValidationResult",
    "Witness",
    "validate_wip",
    "render_report",
]
