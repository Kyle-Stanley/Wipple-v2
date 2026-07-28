"""
Header-blind WIP validation built around search compression.

The hot path is deliberately asymmetric:

* immutable table preparation happens once;
* specialized vectorized motif kernels compress the physical search space;
* canonical fragments are assembled and deduplicated before graph closure;
* one validation-run value pool interns every repeated calculation;
* matching caches strict and loose all-column scores together;
* the generic constraint engine closes only a few unique mappings;
* redundancy, certification, and diagnosis run only on finalists.

The public result shape mirrors ``accounting.wip`` so both engines can be
A/B-tested without changing their callers.  The old validator is not invoked.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field, replace
from itertools import combinations
from operator import or_
from functools import lru_cache, reduce
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
    _allowed_bad,
    _classify_error,
    _ingest,
    _transplant_sources,
    detect_grid,
    render_report,
)

PCT_VARS = frozenset({"M", "P", "PB"})
VAR_ORDER = tuple(VAR_NAMES)
VAR_INDEX = {var: i for i, var in enumerate(VAR_ORDER)}
VAR_BIT = {var: 1 << i for i, var in enumerate(VAR_ORDER)}
REGIONS = {
    "estimate": frozenset({"V", "C", "G", "M"}),
    "progress": frozenset({"D", "Q", "P", "E", "H", "R"}),
    "billing": frozenset({"B", "N", "U", "O", "RB", "PB"}),
}


@dataclass
class Config(_LegacyConfig):
    """Legacy thresholds plus wip2's bounded structural search controls."""

    motif_rows: int = 16
    motif_hubs: int = 5
    estimate_fragments: int = 5
    progress_per_estimate: int = 4
    billing_per_progress: int = 3
    assembled_states: int = 18
    initial_closures: int = 4
    finalist_limit: int = 3
    min_region_checkable: int = 1


# ---------------------------------------------------------------------------
# Fixed semantic factor graph
# ---------------------------------------------------------------------------


ArrayFn = Callable[..., np.ndarray]
TolFn = Callable[[tuple[np.ndarray, ...], tuple[np.ndarray, ...]], np.ndarray]


def _tol_sum(
    values: tuple[np.ndarray, ...],
    tolerances: tuple[np.ndarray, ...],
) -> np.ndarray:
    del values
    out = np.zeros_like(tolerances[0])
    for tolerance in tolerances:
        out = out + tolerance
    return out


def _tol_mul(
    values: tuple[np.ndarray, ...],
    tolerances: tuple[np.ndarray, ...],
) -> np.ndarray:
    a, b = values
    ta, tb = tolerances
    return np.abs(b) * ta + np.abs(a) * tb + ta * tb


def _tol_div(
    values: tuple[np.ndarray, ...],
    tolerances: tuple[np.ndarray, ...],
) -> np.ndarray:
    a, b = values
    ta, tb = tolerances
    floor = np.maximum(np.abs(b) - tb, 1e-12)
    return ta / floor + (np.abs(a) + ta) * tb / (floor * floor)


def _tol_mul_div(
    values: tuple[np.ndarray, ...],
    tolerances: tuple[np.ndarray, ...],
) -> np.ndarray:
    a, b, c = values
    ta, tb, tc = tolerances
    product = a * b
    product_tol = np.abs(b) * ta + np.abs(a) * tb + ta * tb
    return _tol_div((product, c), (product_tol, tc))


@dataclass(frozen=True)
class Derivation:
    id: str
    identity_id: str
    out: str
    inputs: tuple[str, ...]
    fn: ArrayFn
    tol_fn: TolFn
    kind: str = "money"


@dataclass(frozen=True)
class Identity:
    id: str
    variables: tuple[str, ...]
    derivations: tuple[Derivation, ...]
    verification_outputs: tuple[str, ...] = ()
    evidence: bool = True
    statement: str = ""


def _derivation(
    identity: str,
    out: str,
    inputs: tuple[str, ...],
    fn: ArrayFn,
    tol_fn: TolFn,
    kind: str = "money",
) -> Derivation:
    return Derivation(
        id=f"{out}_from_{'_'.join(inputs)}",
        identity_id=identity,
        out=out,
        inputs=inputs,
        fn=fn,
        tol_fn=tol_fn,
        kind=kind,
    )


def _additive_identity(
    identity: str,
    variables: tuple[str, str, str],
    total: str,
    left: str,
    right: str,
    statement: str,
) -> Identity:
    formulas = {
        total: ((left, right), np.add),
        left: ((total, right), np.subtract),
        right: ((total, left), np.subtract),
    }
    return Identity(
        identity,
        variables,
        tuple(
            _derivation(
                identity,
                out,
                formulas[out][0],
                formulas[out][1],
                _tol_sum,
            )
            for out in variables
        ),
        statement=statement,
    )


def _ratio_identity(
    identity: str,
    ratio: str,
    numerator: str,
    denominator: str,
    statement: str,
) -> Identity:
    return Identity(
        identity,
        (ratio, numerator, denominator),
        (
            _derivation(
                identity, ratio, (numerator, denominator),
                np.divide, _tol_div, "pct",
            ),
            _derivation(
                identity, numerator, (denominator, ratio),
                np.multiply, _tol_mul,
            ),
            _derivation(
                identity, denominator, (numerator, ratio),
                np.divide, _tol_div,
            ),
        ),
        statement=statement,
    )


def _product_identity(
    identity: str,
    variables: tuple[str, ...],
    left: tuple[str, str],
    right: tuple[str, str],
    outputs: tuple[str, ...],
    statement: str,
    *,
    evidence: bool = True,
) -> Identity:
    a, b = left
    c, d = right
    formulas = {
        a: (c, d, b),
        b: (c, d, a),
        c: (a, b, d),
        d: (a, b, c),
    }
    return Identity(
        identity,
        variables,
        tuple(
            _derivation(
                identity,
                out,
                formulas[out],
                lambda x, y, z: x * y / z,
                _tol_mul_div,
            )
            for out in outputs
        ),
        evidence=evidence,
        statement=statement,
    )


def _registry() -> tuple[Identity, ...]:
    additive = _additive_identity
    ratio = _ratio_identity
    product = _product_identity
    return (
        additive("estimate_complement", ("V", "C", "G"), "V", "C", "G", "V = C + G"),
        additive("cost_completion", ("C", "D", "Q"), "C", "D", "Q", "C = D + Q"),
        product(
            "earned_revenue", ("E", "V", "D", "C"), ("E", "C"),
            ("V", "D"), ("E", "D", "C", "V"), "E x C = V x D",
        ),
        additive("earned_profit", ("H", "E", "D"), "E", "H", "D", "H = E - D"),
        product(
            "earned_profit_margin", ("H", "G", "D", "C"), ("H", "C"),
            ("G", "D"), ("H", "D", "G", "C"), "H x C = G x D",
            evidence=False,
        ),
        additive("net_billing", ("N", "E", "B"), "E", "N", "B", "N = E - B"),
        Identity(
            "billing_split",
            ("E", "B", "U", "O"),
            (
                _derivation(
                    "billing_split", "U", ("E", "B"),
                    lambda E, B: np.maximum(E - B, 0.0), _tol_sum,
                ),
                _derivation(
                    "billing_split", "O", ("E", "B"),
                    lambda E, B: np.maximum(B - E, 0.0), _tol_sum,
                ),
                _derivation(
                    "billing_split", "E", ("B", "U", "O"),
                    lambda B, U, O: B + U - O, _tol_sum,
                ),
                _derivation(
                    "billing_split", "B", ("E", "U", "O"),
                    lambda E, U, O: E - U + O, _tol_sum,
                ),
            ),
            ("U", "O"),
            statement="U/O = split(E - B)",
        ),
        additive("backlog", ("R", "V", "E"), "V", "R", "E", "R = V - E"),
        additive("remaining_billings", ("RB", "V", "B"), "V", "RB", "B", "RB = V - B"),
        ratio("margin", "M", "G", "V", "M = G / V"),
        ratio("percent_complete_cost", "P", "D", "C", "P = D / C"),
        ratio("percent_complete_revenue", "P", "E", "V", "P = E / V"),
        ratio("percent_billed", "PB", "B", "V", "PB = B / V"),
    )


IDENTITIES = _registry()
DERIVATION_BY_ID = {
    derivation.id: derivation
    for identity in IDENTITIES
    for derivation in identity.derivations
}
IDENTITY_BY_ID = {identity.id: identity for identity in IDENTITIES}
WAITING_ON = {
    var: tuple(
        derivation
        for derivation in DERIVATION_BY_ID.values()
        if var in derivation.inputs
    )
    for var in VAR_ORDER
}


# ---------------------------------------------------------------------------
# Immutable prepared workspace
# ---------------------------------------------------------------------------


def _readonly(values: np.ndarray) -> np.ndarray:
    array = np.ascontiguousarray(values)
    array.flags.writeable = False
    return array


@dataclass(frozen=True)
class PreparedTable:
    full: np.ndarray
    row_index: np.ndarray
    raw: np.ndarray
    magnitude: np.ndarray
    whole_percent: np.ndarray
    positive: np.ndarray
    percent_ratio_valid: np.ndarray
    percent_whole_valid: np.ndarray
    percent_ratio_tol: np.ndarray
    percent_whole_tol: np.ndarray
    percent_ratio_grid: tuple[Optional[float], ...]
    percent_whole_grid: tuple[Optional[float], ...]
    median_abs: np.ndarray
    representatives: np.ndarray
    active_overlap: np.ndarray
    sample_index: np.ndarray

    @classmethod
    def build(cls, matrix: np.ndarray, cfg: Config) -> "PreparedTable":
        full = _readonly(np.asarray(matrix, dtype=float))
        complete = np.all(np.isfinite(full), axis=1)
        row_index = _readonly(np.flatnonzero(complete))
        raw = _readonly(full[complete].T)
        magnitude = _readonly(np.abs(raw))
        whole_percent = _readonly(raw / 100.0)
        active = _readonly(
            magnitude > cfg.money_obs_tol + cfg.cert_slack
        )
        positive = _readonly(raw > cfg.money_obs_tol)
        ratio_valid = _readonly(
            np.mean((raw >= -0.25) & (raw <= 2.0), axis=1) >= 0.90
        )
        whole_valid = _readonly(
            np.mean(
                (whole_percent >= -0.25) & (whole_percent <= 2.0),
                axis=1,
            )
            >= 0.90
        )

        ratio_grids = tuple(
            detect_grid(raw[col]) if ratio_valid[col] else None
            for col in range(raw.shape[0])
        )
        whole_grids = tuple(
            (
                detect_grid(whole_percent[col])
                if whole_valid[col]
                else None
            )
            for col in range(raw.shape[0])
        )
        ratio_tol = _readonly(
            np.asarray(
                [
                    grid * cfg.pct_grid_mult
                    if grid is not None
                    else cfg.pct_default_tol
                    for grid in ratio_grids
                ],
                dtype=float,
            )
        )
        whole_tol = _readonly(
            np.asarray(
                [
                    grid * cfg.pct_grid_mult
                    if grid is not None
                    else cfg.pct_default_tol
                    for grid in whole_grids
                ],
                dtype=float,
            )
        )
        median_abs = _readonly(np.median(magnitude, axis=1))

        representative_by_bytes: dict[tuple, int] = {}
        duplicate_representative = np.empty(raw.shape[0], dtype=int)
        for column, values in enumerate(raw):
            key = (values.dtype.str, values.shape, values.tobytes())
            duplicate_representative[column] = representative_by_bytes.setdefault(
                key, column
            )
        representatives = _readonly(
            np.flatnonzero(
                duplicate_representative == np.arange(raw.shape[0])
            )
        )
        active_i = active.astype(np.int32, copy=False)
        active_overlap = _readonly(active_i @ active_i.T)
        if raw.shape[1] <= cfg.motif_rows:
            sample_index = np.arange(raw.shape[1], dtype=int)
        else:
            sample_index = np.unique(
                np.linspace(
                    0,
                    raw.shape[1] - 1,
                    cfg.motif_rows,
                ).astype(int)
            )

        return cls(
            full=full,
            row_index=row_index,
            raw=raw,
            magnitude=magnitude,
            whole_percent=whole_percent,
            positive=positive,
            percent_ratio_valid=ratio_valid,
            percent_whole_valid=whole_valid,
            percent_ratio_tol=ratio_tol,
            percent_whole_tol=whole_tol,
            percent_ratio_grid=ratio_grids,
            percent_whole_grid=whole_grids,
            median_abs=median_abs,
            representatives=representatives,
            active_overlap=active_overlap,
            sample_index=_readonly(sample_index),
        )


# ---------------------------------------------------------------------------
# Validation-run value pool and all-column score cache
# ---------------------------------------------------------------------------


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
    full: bool = False


@dataclass(frozen=True)
class ScoreVectors:
    strict_bad: np.ndarray
    loose_bad: np.ndarray
    residual: np.ndarray
    scale: np.ndarray


class RunContext:
    """Owns every cache for one immutable document and configuration."""

    def __init__(self, table: PreparedTable, cfg: Config):
        self.table = table
        self.cfg = cfg
        self._next_id = 1
        self.observed_cache: dict[tuple, NumericValue] = {}
        self.derived_cache: dict[tuple, NumericValue] = {}
        self.score_cache: dict[tuple, ScoreVectors] = {}
        self.derived_hits = 0
        self.derived_misses = 0
        self.score_hits = 0
        self.score_misses = 0
        self.score_batches = 0

    def _id(self) -> int:
        value_id = self._next_id
        self._next_id += 1
        return value_id

    def observed(
        self,
        variable: str,
        column: int,
        *,
        scale: float = 1.0,
        full: bool = False,
    ) -> NumericValue:
        key = (variable, int(column), float(scale), bool(full))
        cached = self.observed_cache.get(key)
        if cached is not None:
            return cached
        values = (
            self.table.full[:, column]
            if full
            else self.table.raw[column]
        )
        if scale != 1.0:
            values = values / scale
        if variable in MAGNITUDE_PRESENTATION_VARS:
            values = np.abs(values)
        if variable in PCT_VARS:
            if scale == 100.0:
                grid = self.table.percent_whole_grid[column]
                tolerance = self.table.percent_whole_tol[column]
            else:
                grid = self.table.percent_ratio_grid[column]
                tolerance = self.table.percent_ratio_tol[column]
        else:
            grid = None
            tolerance = self.cfg.money_obs_tol
        value = NumericValue(
            id=self._id(),
            variable=variable,
            values=_readonly(np.asarray(values, dtype=float)),
            tolerance=_readonly(
                np.full(np.asarray(values).shape, tolerance, dtype=float)
            ),
            support=1 << int(column),
            column=int(column),
            scale=float(scale),
            grid=grid,
            full=full,
        )
        self.observed_cache[key] = value
        return value

    def derive(
        self,
        derivation: Derivation,
        known: dict[str, NumericValue],
    ) -> Optional[NumericValue]:
        inputs = tuple(known[var] for var in derivation.inputs)
        full = inputs[0].full
        if any(value.full != full for value in inputs):
            raise ValueError("cannot combine identification and full-row values")
        key = (
            bool(full),
            derivation.id,
            tuple(value.id for value in inputs),
        )
        cached = self.derived_cache.get(key)
        if cached is not None:
            self.derived_hits += 1
            return cached
        self.derived_misses += 1
        values = tuple(value.values for value in inputs)
        tolerances = tuple(value.tolerance for value in inputs)
        with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
            output = np.asarray(derivation.fn(*values), dtype=float)
            tolerance = np.asarray(
                derivation.tol_fn(values, tolerances),
                dtype=float,
            )
        if not np.any(np.isfinite(output)):
            return None
        support = reduce(or_, (value.support for value in inputs), 0)
        result = NumericValue(
            id=self._id(),
            variable=derivation.out,
            values=_readonly(output),
            tolerance=_readonly(np.maximum(tolerance, 1e-12)),
            support=support,
            derivation=derivation.id,
            full=full,
        )
        self.derived_cache[key] = result
        return result

    def score_arrays(
        self,
        predictions: np.ndarray,
        predicted_tolerances: np.ndarray,
        kind: str,
        columns: np.ndarray,
        rows: np.ndarray,
        *,
        magnitude: bool = False,
    ) -> ScoreVectors:
        """Canonical strict/loose tensor kernel for discovery and closure."""
        prediction = predictions[:, None, :]
        tolerance = predicted_tolerances[:, None, :]
        if kind == "money":
            source = self.table.magnitude if magnitude else self.table.raw
            observed = source[columns][:, rows]
            residual = np.abs(prediction - observed[None, :, :])
            strict = (
                tolerance
                + self.cfg.money_obs_tol
                + self.cfg.cert_slack
                + self.cfg.cert_money_rel * np.abs(prediction)
            )
            loose = strict + np.maximum(
                self.cfg.ident_abs,
                self.cfg.ident_rel * np.abs(prediction),
            )
            return ScoreVectors(
                strict_bad=np.sum(residual > strict, axis=2),
                loose_bad=np.sum(residual > loose, axis=2),
                residual=np.sum(np.minimum(residual, loose), axis=2),
                scale=np.ones((predictions.shape[0], columns.size)),
            )

        shape = (predictions.shape[0], columns.size)
        best = ScoreVectors(
            strict_bad=np.full(shape, 10**9, dtype=np.int32),
            loose_bad=np.full(shape, 10**9, dtype=np.int32),
            residual=np.full(shape, np.inf),
            scale=np.ones(shape),
        )
        for scale, source, valid, column_tolerance in (
            (
                1.0,
                self.table.raw,
                self.table.percent_ratio_valid,
                self.table.percent_ratio_tol,
            ),
            (
                100.0,
                self.table.whole_percent,
                self.table.percent_whole_valid,
                self.table.percent_whole_tol,
            ),
        ):
            observed = source[columns][:, rows]
            residual = np.abs(prediction - observed[None, :, :])
            strict = (
                tolerance
                + column_tolerance[columns][None, :, None]
                + 1e-9
            )
            loose = strict + self.cfg.pct_ident_slack
            strict_bad = np.sum(residual > strict, axis=2)
            loose_bad = np.sum(residual > loose, axis=2)
            clipped = np.sum(np.minimum(residual, loose), axis=2)
            valid_columns = valid[columns][None, :]
            strict_bad = np.where(valid_columns, strict_bad, 10**9)
            loose_bad = np.where(valid_columns, loose_bad, 10**9)
            clipped = np.where(valid_columns, clipped, np.inf)
            better = (
                (strict_bad < best.strict_bad)
                | (
                    (strict_bad == best.strict_bad)
                    & (loose_bad < best.loose_bad)
                )
                | (
                    (strict_bad == best.strict_bad)
                    & (loose_bad == best.loose_bad)
                    & (clipped < best.residual)
                )
            )
            best = ScoreVectors(
                strict_bad=np.where(
                    better, strict_bad, best.strict_bad
                ),
                loose_bad=np.where(better, loose_bad, best.loose_bad),
                residual=np.where(better, clipped, best.residual),
                scale=np.where(better, scale, best.scale),
            )
        return best

    def score_many(
        self,
        requests: Iterable[tuple[NumericValue, Derivation]],
    ) -> list[ScoreVectors]:
        """Score a ready frontier with one tensor kernel per presentation."""
        requested = list(requests)
        keys = []
        misses: dict[
            tuple[str, bool],
            list[tuple[tuple, NumericValue]],
        ] = {}
        for value, derivation in requested:
            if value.full:
                raise ValueError(
                    "full-row values are not physical-search predictions"
                )
            magnitude = derivation.out in MAGNITUDE_PRESENTATION_VARS
            key = (value.id, derivation.kind, magnitude)
            keys.append(key)
            if key in self.score_cache:
                self.score_hits += 1
                continue
            misses.setdefault(
                (derivation.kind, magnitude),
                [],
            ).append((key, value))

        for (kind, magnitude), group in misses.items():
            # A value can appear through more than one pending derivation.
            # Structural cache identity makes that one numeric request.
            unique = dict(group)
            group = list(unique.items())
            self.score_misses += len(group)
            self.score_batches += 1
            predictions = np.stack(
                [value.values for _, value in group]
            )
            predicted_tolerances = np.stack(
                [value.tolerance for _, value in group]
            )
            scores = self.score_arrays(
                predictions,
                predicted_tolerances,
                kind,
                np.arange(self.table.raw.shape[0]),
                np.arange(self.table.raw.shape[1]),
                magnitude=magnitude,
            )

            for index, (key, _) in enumerate(group):
                self.score_cache[key] = ScoreVectors(
                    strict_bad=_readonly(
                        np.asarray(
                            scores.strict_bad[index],
                            dtype=np.int32,
                        )
                    ),
                    loose_bad=_readonly(
                        np.asarray(
                            scores.loose_bad[index],
                            dtype=np.int32,
                        )
                    ),
                    residual=_readonly(
                        np.asarray(scores.residual[index], dtype=float)
                    ),
                    scale=_readonly(
                        np.asarray(scores.scale[index], dtype=float)
                    ),
                )

        return [self.score_cache[key] for key in keys]


def _robust_prior(condition: np.ndarray, cfg: Config) -> np.ndarray:
    """Apply economic priors as bounded bad-row filters, never exact gates."""
    rows = condition.shape[-1]
    allowed = max(
        _allowed_bad(rows, cfg),
        int(np.floor((1.0 - cfg.prior_robust_frac) * rows)),
    )
    return np.sum(~condition, axis=-1) <= allowed


def _values_agree(
    left: NumericValue,
    right: NumericValue,
    cfg: Config,
    *,
    robust: bool,
) -> bool:
    valid = np.isfinite(left.values) & np.isfinite(right.values)
    if int(valid.sum()) < cfg.min_informative_rows:
        return False
    tolerance = left.tolerance + right.tolerance + 1e-9
    if left.variable not in PCT_VARS:
        tolerance = (
            tolerance
            + cfg.cert_slack
            + cfg.cert_money_rel
            * np.maximum(np.abs(left.values), np.abs(right.values))
        )
    if robust:
        tolerance = tolerance + (
            cfg.pct_ident_slack
            if left.variable in PCT_VARS
            else np.maximum(cfg.ident_abs, cfg.ident_rel * np.abs(left.values))
        )
    bad = np.sum(
        np.abs(left.values[valid] - right.values[valid])
        > tolerance[valid]
    )
    return int(bad) <= (
        _allowed_bad(int(valid.sum()), cfg)
        if robust
        else 0
    )


# ---------------------------------------------------------------------------
# Simultaneous structural motif discovery and canonical assembly
# ---------------------------------------------------------------------------


@dataclass(frozen=True, order=True)
class Assignment:
    variable: str
    column: int
    scale: float = 1.0


@dataclass(frozen=True)
class StructuralMatch:
    column: int
    scale: float
    strict_bad: int
    loose_bad: int
    residual: float

    @property
    def rank(self):
        return (
            self.strict_bad,
            self.loose_bad,
            self.residual,
            self.column,
        )


@dataclass(frozen=True)
class Fragment:
    assignments: tuple[Assignment, ...]
    score: int
    residual: float
    sources: frozenset[str]

    @property
    def mapping(self) -> dict[str, Assignment]:
        return {
            assignment.variable: assignment
            for assignment in self.assignments
        }

    @property
    def rank(self):
        return (-self.score, self.residual, self.assignments)


def _best_fragments(
    fragments: Iterable[Fragment],
    limit: Optional[int] = None,
) -> list[Fragment]:
    """Deduplicate equivalent graph states and retain their strongest route."""
    best: dict[tuple[Assignment, ...], Fragment] = {}
    for fragment in fragments:
        prior = best.get(fragment.assignments)
        if prior is None or fragment.rank < prior.rank:
            best[fragment.assignments] = fragment
    ranked = sorted(best.values(), key=lambda fragment: fragment.rank)
    return ranked if limit is None else ranked[:limit]


def _canonical_assignments(
    assignments: Iterable[Assignment],
) -> Optional[tuple[Assignment, ...]]:
    by_variable: dict[str, Assignment] = {}
    by_column: dict[int, str] = {}
    for assignment in assignments:
        prior = by_variable.get(assignment.variable)
        if prior is not None and prior != assignment:
            return None
        prior_variable = by_column.get(assignment.column)
        if (
            prior_variable is not None
            and prior_variable != assignment.variable
        ):
            return None
        by_variable[assignment.variable] = assignment
        by_column[assignment.column] = assignment.variable
    return tuple(
        sorted(
            by_variable.values(),
            key=lambda assignment: VAR_INDEX[assignment.variable],
        )
    )


def _batch_matches(
    ctx: RunContext,
    predictions: np.ndarray,
    predicted_tolerance: np.ndarray,
    available: np.ndarray,
    *,
    kind: str,
    magnitude: bool = False,
    excluded: Optional[np.ndarray] = None,
) -> list[StructuralMatch]:
    if kind == "pct":
        valid = (
            ctx.table.percent_ratio_valid
            | ctx.table.percent_whole_valid
        )
        available = available[valid[available]]
        if available.size == 0:
            return [
                StructuralMatch(-1, 1.0, 10**9, 10**9, np.inf)
                for _ in predictions
            ]
    scores = ctx.score_arrays(
        predictions,
        predicted_tolerance,
        kind,
        available,
        ctx.table.sample_index,
        magnitude=magnitude,
    )
    if excluded is not None:
        positions = {
            int(column): index
            for index, column in enumerate(available)
        }
        for row, column in enumerate(excluded):
            position = positions.get(int(column))
            if position is not None:
                scores.strict_bad[row, position] = 10**9
                scores.loose_bad[row, position] = 10**9
                scores.residual[row, position] = np.inf
    order = np.lexsort(
        (
            np.broadcast_to(available, scores.residual.shape),
            scores.residual,
            scores.loose_bad,
            scores.strict_bad,
        ),
        axis=1,
    )
    best = order[:, 0]
    return [
        StructuralMatch(
            column=int(available[column_index]),
            scale=float(scores.scale[row, column_index]),
            strict_bad=int(scores.strict_bad[row, column_index]),
            loose_bad=int(scores.loose_bad[row, column_index]),
            residual=float(scores.residual[row, column_index]),
        )
        for row, column_index in enumerate(best)
    ]


def _accepted(
    match: StructuralMatch,
    rows: int,
    cfg: Config,
) -> bool:
    return match.loose_bad <= _allowed_bad(rows, cfg)


def _unique_output_matches(
    matches: dict[str, StructuralMatch],
    rows: int,
    cfg: Config,
) -> dict[str, StructuralMatch]:
    """Keep only qualifying one-to-one output/column claims."""
    selected = {}
    used_columns = set()
    for variable, match in sorted(
        matches.items(),
        key=lambda item: item[1].rank,
    ):
        if not _accepted(match, rows, cfg):
            continue
        if match.column in used_columns:
            continue
        selected[variable] = match
        used_columns.add(match.column)
    return selected


def _unassigned_columns(
    table: PreparedTable,
    fragment: Fragment,
) -> np.ndarray:
    used = np.fromiter(
        (assignment.column for assignment in fragment.assignments),
        dtype=int,
    )
    return table.representatives[
        ~np.isin(table.representatives, used)
    ]


def _extend_fragment(
    base: Fragment,
    anchor: str,
    anchor_columns: np.ndarray,
    matches_by_variable: dict[str, list[StructuralMatch]],
    identifying: frozenset[str],
    weights: dict[str, int],
    source: str,
    rows: int,
    cfg: Config,
    limit: int,
    bonus: Optional[Callable[[dict[str, StructuralMatch]], int]] = None,
) -> list[Fragment]:
    """Orient a batched anchor search and retain its best graph fragments."""
    ranked = []
    for index, column in enumerate(anchor_columns):
        selected = _unique_output_matches(
            {
                variable: matches[index]
                for variable, matches in matches_by_variable.items()
            },
            rows,
            cfg,
        )
        if not identifying.intersection(selected):
            continue
        assignments = [
            *base.assignments,
            Assignment(anchor, int(column)),
            *(
                Assignment(variable, match.column, match.scale)
                for variable, match in selected.items()
            ),
        ]
        canonical = _canonical_assignments(assignments)
        if canonical is None:
            continue
        ranked.append(
            Fragment(
                assignments=canonical,
                score=(
                    base.score
                    + 5
                    + sum(
                        weights[variable]
                        + int(match.strict_bad == 0)
                        for variable, match in selected.items()
                    )
                    + (bonus(selected) if bonus else 0)
                ),
                residual=base.residual + sum(
                    match.residual for match in selected.values()
                ),
                sources=base.sources | frozenset({source}),
            )
        )
    return sorted(ranked, key=lambda fragment: fragment.rank)[:limit]


def _primary_estimate_hubs(ctx: RunContext) -> list[int]:
    table = ctx.table
    positive = _robust_prior(
        table.positive[:, table.sample_index],
        ctx.cfg,
    )
    return sorted(
        (
            int(column)
            for column in table.representatives
            if positive[column]
        ),
        key=lambda column: (-table.median_abs[column], column),
    )[: ctx.cfg.motif_hubs]


def _estimate_fragments(
    ctx: RunContext,
    hub_candidates: Optional[Iterable[int]] = None,
) -> list[Fragment]:
    table = ctx.table
    cfg = ctx.cfg
    reps = table.representatives
    sample = table.sample_index
    raw = table.raw[:, sample]
    positive = _robust_prior(
        table.positive[:, sample],
        cfg,
    )
    hub_candidates = (
        _primary_estimate_hubs(ctx)
        if hub_candidates is None
        else hub_candidates
    )
    hubs = [
        int(column)
        for column in hub_candidates
        if positive[int(column)]
    ]
    allowed = _allowed_bad(sample.size, cfg)
    candidates = []
    if not hubs or reps.size < 3:
        return []

    # Portfolio stability orients additive triangles.  Calculate every
    # hub/column ratio profile together instead of invoking percentile
    # machinery once per candidate orientation.
    hub_position = {column: index for index, column in enumerate(hubs)}
    rep_position = {
        int(column): index
        for index, column in enumerate(reps)
    }
    with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
        ratio_profiles = (
            raw[reps][None, :, :]
            / raw[np.asarray(hubs, dtype=int)][:, None, :]
        )
    ratio_profiles = np.where(
        np.isfinite(ratio_profiles),
        ratio_profiles,
        np.inf,
    )
    ordered_ratios = np.sort(ratio_profiles, axis=2)
    profile_rows = ordered_ratios.shape[2]
    middle = profile_rows // 2
    if profile_rows % 2:
        ratio_median = ordered_ratios[:, :, middle]
    else:
        ratio_median = (
            ordered_ratios[:, :, middle - 1]
            + ordered_ratios[:, :, middle]
        ) / 2.0
    lower = int(round(0.25 * (profile_rows - 1)))
    upper = int(round(0.75 * (profile_rows - 1)))
    ratio_spread = (
        ordered_ratios[:, :, upper]
        - ordered_ratios[:, :, lower]
    )

    # Score every requested hub against the same pair-sum matrix.  Blocks
    # bound the temporary hubs × pairs × rows cube while retaining the
    # simultaneous kernel that makes wide fallback practical.
    left_index, right_index = np.triu_indices(reps.size, 1)
    left = reps[left_index]
    right = reps[right_index]
    pair_sums = raw[left] + raw[right]
    floats_per_hub = max(1, pair_sums.size)
    block_size = max(1, min(len(hubs), 4_000_000 // floats_per_hub))
    scored_pairs: list[tuple[int, int, int, float]] = []
    for start in range(0, len(hubs), block_size):
        block = np.asarray(hubs[start : start + block_size], dtype=int)
        target = raw[block]
        residual = np.abs(
            pair_sums[None, :, :] - target[:, None, :]
        )
        strict = (
            3 * cfg.money_obs_tol
            + cfg.cert_slack
            + cfg.cert_money_rel * np.abs(target)
        )
        loose = strict + np.maximum(
            cfg.ident_abs,
            cfg.ident_rel * np.abs(target),
        )
        strict_bad = np.sum(
            residual > strict[:, None, :],
            axis=2,
        )
        loose_bad = np.sum(
            residual > loose[:, None, :],
            axis=2,
        )
        residual_score = np.sum(
            np.minimum(residual, loose[:, None, :]),
            axis=2,
        )
        eligible = (
            (left[None, :] != block[:, None])
            & (right[None, :] != block[:, None])
            & (loose_bad <= allowed)
        )
        for local, v_column in enumerate(block):
            qualifying = np.flatnonzero(eligible[local])
            if qualifying.size == 0:
                continue
            qualifying = qualifying[
                np.lexsort(
                    (
                        left[qualifying],
                        right[qualifying],
                        residual_score[local, qualifying],
                        loose_bad[local, qualifying],
                        strict_bad[local, qualifying],
                    )
                )
            ][: cfg.estimate_fragments * 2]
            scored_pairs.extend(
                (
                    int(v_column),
                    int(index),
                    int(strict_bad[local, index]),
                    float(residual_score[local, index]),
                )
                for index in qualifying
            )

    for v_column, index, pair_strict_bad, pair_residual in scored_pairs:
            a_column = int(left[index])
            b_column = int(right[index])
            orientations = []
            for c_column, g_column in (
                (a_column, b_column),
                (b_column, a_column),
            ):
                hub_index = hub_position[v_column]
                column_index = rep_position[c_column]
                median = float(ratio_median[hub_index, column_index])
                spread = float(ratio_spread[hub_index, column_index])
                if not np.isfinite(median) or not np.isfinite(spread):
                    continue
                if (
                    cfg.cost_ratio_band[0]
                    <= median
                    <= cfg.cost_ratio_band[1]
                    and spread <= cfg.estimate_iqr_max
                ):
                    orientations.append((c_column, g_column, spread))
            if not orientations:
                # A nested progress complement (C=D+Q) is algebraically
                # identical to V=C+G.  It must not masquerade as the estimate
                # triangle merely because D/C happens to match printed P.
                # Estimate orientation requires a portfolio-stable component
                # even when its median falls just outside the normal band.
                broad = []
                for c_column, g_column in (
                    (a_column, b_column),
                    (b_column, a_column),
                ):
                    hub_index = hub_position[v_column]
                    column_index = rep_position[c_column]
                    median = float(
                        ratio_median[hub_index, column_index]
                    )
                    spread = float(
                        ratio_spread[hub_index, column_index]
                    )
                    if (
                        not np.isfinite(median)
                        or not np.isfinite(spread)
                    ):
                        continue
                    if (
                        0.35 <= median <= 1.25
                        and spread <= cfg.estimate_iqr_max
                    ):
                        broad.append(
                            (spread, -median, c_column, g_column)
                        )
                if broad:
                    spread, _, c_column, g_column = min(broad)
                    orientations.append(
                        (c_column, g_column, spread)
                    )
            if not orientations:
                continue

            for c_column, g_column, estimate_spread in orientations:
                base = [
                    Assignment("V", v_column),
                    Assignment("C", c_column),
                    Assignment("G", g_column),
                ]
                used = {v_column, c_column, g_column}
                available = reps[
                    ~np.isin(reps, np.fromiter(used, dtype=int))
                ]
                with np.errstate(divide="ignore", invalid="ignore"):
                    margin = (
                        table.raw[g_column, sample]
                        / table.raw[v_column, sample]
                    )
                margin_tol = _tol_div(
                    (
                        table.raw[g_column, sample],
                        table.raw[v_column, sample],
                    ),
                    (
                        np.full(sample.size, cfg.money_obs_tol),
                        np.full(sample.size, cfg.money_obs_tol),
                    ),
                )[None, :]
                if available.size:
                    margin_match = _batch_matches(
                        ctx,
                        margin[None, :],
                        margin_tol,
                        available,
                        kind="pct",
                    )[0]
                    if _accepted(margin_match, sample.size, cfg):
                        base.append(
                            Assignment(
                                "M",
                                margin_match.column,
                                margin_match.scale,
                            )
                        )
                canonical = _canonical_assignments(base)
                if canonical is None:
                    continue
                fragment = Fragment(
                    assignments=canonical,
                    # A true estimate ratio is unusually stable across jobs;
                    # progress/backlog complements can be exact algebraically
                    # but vary with job completion.  This bonus allows one
                    # corrupted estimate cell to retain the correct semantic
                    # orientation over an exact downstream triangle.
                    score=(
                        (
                            8
                            if pair_strict_bad == 0
                            else 6
                        )
                        + (2 if len(base) == 4 else 0)
                        + (
                            4
                            if estimate_spread <= 0.05
                            else 2
                            if estimate_spread <= 0.10
                            else 0
                        )
                    ),
                    # Exact additive triangles are common elsewhere in the
                    # graph.  Portfolio stability is the cheap orientation
                    # signal that puts the true estimate triangle first.
                    residual=float(
                        pair_residual
                        + estimate_spread * sample.size
                    ),
                    sources=frozenset({"estimate_complement"}),
                )
                candidates.append(fragment)

    return _best_fragments(candidates, cfg.estimate_fragments)


def _additive_hub_fallback(ctx: RunContext) -> list[int]:
    """Return every plausible additive hub, cheaply ordered on three rows.

    This is the progressive wide-search frontier.  Degree affects evaluation
    order only; it never excludes a lower-degree but semantically coherent
    hub such as Contract Value in a table full of larger decoys.
    """
    table = ctx.table
    reps = table.representatives
    if reps.size < 3:
        return []
    sample = table.sample_index
    diagnostic = sample[
        np.unique(
            np.linspace(0, sample.size - 1, min(3, sample.size)).astype(int)
        )
    ]
    left_index, right_index = np.triu_indices(reps.size, 1)
    left = reps[left_index]
    right = reps[right_index]
    pair_sums = (
        table.raw[left][:, diagnostic]
        + table.raw[right][:, diagnostic]
    )
    degrees = []
    for target in reps:
        target_values = table.raw[target, diagnostic]
        strict = (
            3 * ctx.cfg.money_obs_tol
            + ctx.cfg.cert_slack
            + np.maximum(
                ctx.cfg.ident_abs,
                ctx.cfg.ident_rel * np.abs(target_values),
            )
        )
        eligible = (left != target) & (right != target)
        matches = np.all(
            np.abs(pair_sums - target_values[None, :])
            <= strict[None, :],
            axis=1,
        )
        degree = int(np.sum(eligible & matches))
        if degree:
            degrees.append(
                (degree, float(table.median_abs[target]), int(target))
            )
    degrees.sort(key=lambda item: (-item[0], -item[1], item[2]))
    return [target for _, _, target in degrees]


def _progress_fragments(
    ctx: RunContext,
    estimates: list[Fragment],
) -> list[Fragment]:
    table = ctx.table
    cfg = ctx.cfg
    sample = table.sample_index
    rows = sample.size
    results = []

    for estimate in estimates:
        mapping = estimate.mapping
        v_column = mapping["V"].column
        c_column = mapping["C"].column
        available = _unassigned_columns(table, estimate)
        candidates = available
        if candidates.size == 0:
            continue
        V = table.raw[v_column, sample]
        C = table.raw[c_column, sample]
        D_all = table.raw[candidates][:, sample]
        with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
            progress = D_all / C[None, :]
        plausible = _robust_prior(
            (progress >= -0.02)
            & (progress <= cfg.d_over_c_slack),
            cfg,
        )
        live = (
            np.median(np.abs(progress), axis=1)
            >= cfg.anchor_live_med
        )
        positive = _robust_prior(
            table.positive[candidates][:, sample],
            cfg,
        )
        candidates = candidates[plausible & live & positive]
        progress = progress[plausible & live & positive]
        if candidates.size == 0:
            continue
        D = table.raw[candidates][:, sample]
        money_tol = np.full_like(D, cfg.money_obs_tol)
        V_batch = np.broadcast_to(V, D.shape)
        C_batch = np.broadcast_to(C, D.shape)
        base_tol = np.full_like(D, cfg.money_obs_tol)
        with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
            Q = C[None, :] - D
            E = V[None, :] * D / C[None, :]
            H = E - D
            P = D / C[None, :]
        Q_tol = base_tol + money_tol
        E_tol = _tol_mul_div(
            (V_batch, D, C_batch),
            (base_tol, money_tol, base_tol),
        )
        H_tol = E_tol + money_tol
        P_tol = _tol_div((D, C_batch), (money_tol, base_tol))
        matches_by_variable = {
            "Q": _batch_matches(
                ctx, Q, Q_tol, available,
                kind="money", excluded=candidates,
            ),
            "E": _batch_matches(
                ctx, E, E_tol, available,
                kind="money", excluded=candidates,
            ),
            "H": _batch_matches(
                ctx, H, H_tol, available,
                kind="money", excluded=candidates,
            ),
            "P": _batch_matches(
                ctx, P, P_tol, available,
                kind="pct", excluded=candidates,
            ),
        }
        # Q alone is only an additive complement and cannot orient D.
        results.extend(
            _extend_fragment(
                estimate,
                "D",
                candidates,
                matches_by_variable,
                frozenset({"E", "P", "H"}),
                {"E": 6, "P": 5, "H": 3, "Q": 2},
                "progress_projection",
                rows,
                cfg,
                cfg.progress_per_estimate,
            )
        )

    return _best_fragments(results)


def _billing_fragments(
    ctx: RunContext,
    progress_fragments: list[Fragment],
) -> list[Fragment]:
    table = ctx.table
    cfg = ctx.cfg
    sample = table.sample_index
    rows = sample.size
    results = []

    for progress_fragment in progress_fragments:
        mapping = progress_fragment.mapping
        v_column = mapping["V"].column
        c_column = mapping["C"].column
        d_column = mapping["D"].column
        available = _unassigned_columns(table, progress_fragment)
        candidates = available
        if candidates.size == 0:
            continue
        V = table.raw[v_column, sample]
        C = table.raw[c_column, sample]
        D = table.raw[d_column, sample]
        B_all = table.raw[candidates][:, sample]
        with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
            E = V * D / C
            billed = B_all / V[None, :]
        plausible = _robust_prior(
            (billed >= -0.05)
            & (billed <= cfg.b_over_v_slack),
            cfg,
        )
        live = (
            np.median(np.abs(billed), axis=1)
            >= cfg.anchor_live_med
        )
        candidates = candidates[plausible & live]
        if candidates.size == 0:
            continue
        B = table.raw[candidates][:, sample]
        V_batch = np.broadcast_to(V, B.shape)
        E_batch = np.broadcast_to(E, B.shape)
        base_tol = np.full_like(B, cfg.money_obs_tol)
        E_tol_1d = _tol_mul_div(
            (V, D, C),
            (
                np.full(rows, cfg.money_obs_tol),
                np.full(rows, cfg.money_obs_tol),
                np.full(rows, cfg.money_obs_tol),
            ),
        )
        E_tol = np.broadcast_to(E_tol_1d, B.shape)
        with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
            N = E_batch - B
            U = np.maximum(N, 0.0)
            O = np.maximum(-N, 0.0)
            RB = V[None, :] - B
            PB = B / V[None, :]
        net_tol = E_tol + base_tol
        rb_tol = base_tol + base_tol
        pb_tol = _tol_div((B, V_batch), (base_tol, base_tol))
        matches_by_variable = {
            "N": _batch_matches(
                ctx, N, net_tol, available,
                kind="money", excluded=candidates,
            ),
            "U": _batch_matches(
                ctx,
                U,
                net_tol,
                available,
                kind="money",
                magnitude=True,
                excluded=candidates,
            ),
            "O": _batch_matches(
                ctx,
                O,
                net_tol,
                available,
                kind="money",
                magnitude=True,
                excluded=candidates,
            ),
            "RB": _batch_matches(
                ctx, RB, rb_tol, available,
                kind="money", excluded=candidates,
            ),
            "PB": _batch_matches(
                ctx, PB, pb_tol, available,
                kind="pct", excluded=candidates,
            ),
        }
        results.extend(
            _extend_fragment(
                progress_fragment,
                "B",
                candidates,
                matches_by_variable,
                frozenset({"N", "U", "O", "PB"}),
                {"N": 5, "U": 4, "O": 4, "PB": 5, "RB": 2},
                "billing_bridge",
                rows,
                cfg,
                cfg.billing_per_progress,
                lambda selected: (
                    4
                    if (
                        "U" in selected
                        and "O" in selected
                        and table.active_overlap[
                            selected["U"].column,
                            selected["O"].column,
                        ] == 0
                    )
                    else 0
                ),
            )
        )

    return _best_fragments(results, cfg.assembled_states)


def _discover_states(
    ctx: RunContext,
    *,
    broaden: bool = False,
    exhaustive: bool = False,
) -> tuple[list[Fragment], dict]:
    if exhaustive and ctx.cfg.ident_frac > ctx.cfg.shadow_audit_frac:
        # Recovery proposals are deliberately downstream of both ordinary
        # motif frontiers.  They can nominate a mapping when several bad rows
        # corrupt an anchor, but never bypass strict final certification.
        # The final recovery frontier uses every identification row so a
        # small motif sample cannot concentrate several unrelated errors and
        # reject a relationship that meets the document-wide recovery bar.
        recovery_table = replace(
            ctx.table,
            sample_index=_readonly(
                np.arange(ctx.table.raw.shape[1], dtype=int)
            ),
        )
        ctx = RunContext(
            recovery_table,
            replace(
                ctx.cfg,
                ident_frac=ctx.cfg.shadow_audit_frac,
            ),
        )
    use_additive_hubs = (
        broaden
        or ctx.table.representatives.size
        > max(20, ctx.cfg.motif_hubs * 4)
    )
    primary_hubs = _primary_estimate_hubs(ctx)
    fallback_hubs = (
        _additive_hub_fallback(ctx)
        if use_additive_hubs
        else []
    )
    hubs = tuple(dict.fromkeys((*primary_hubs, *fallback_hubs)))
    estimates = _estimate_fragments(ctx, hubs)
    fallback_used = bool(fallback_hubs)
    estimate_proposals = len(estimates)
    estimate_frontier = estimates
    if estimates and not exhaustive:
        strongest = estimates[0].score
        estimate_frontier = [
            fragment
            for fragment in estimates
            if fragment.score == strongest
        ]

    # Every context in the current confidence frontier enters the same next
    # stage.  No heuristic runs as a procedural early return; weaker bands are
    # deferred wholesale to progressive widening if the strong band cannot
    # certify.  Canonical keys collapse routes to the same semantic graph.
    progress_proposals = _progress_fragments(ctx, estimate_frontier)
    progress = progress_proposals
    if progress and not exhaustive:
        strongest = progress[0].score
        progress = [
            fragment
            for fragment in progress
            if fragment.score == strongest
        ]
    assembled = _billing_fragments(ctx, progress)
    return assembled, {
        "estimate_fragments": estimate_proposals,
        "estimate_frontier": len(estimate_frontier),
        "progress_fragments": len(progress_proposals),
        "progress_frontier": len(progress),
        "canonical_assembled_states": len(assembled),
        "additive_hub_fallback": fallback_used,
    }


# ---------------------------------------------------------------------------
# Dependency-frontier physical-first graph closure
# ---------------------------------------------------------------------------


@dataclass
class ClosedGraph:
    fragment: Fragment
    known: dict[str, NumericValue]
    column_to_var: dict[int, str]
    conflicts: list[str]
    certification_derivations: frozenset[str] = frozenset()
    checkable: frozenset[str] = frozenset()
    coverage: dict[str, int] = field(default_factory=dict)
    minimum_seeds: int = 0
    redundancy: int = 0

    @property
    def mapping_key(self):
        return tuple(
            sorted(
                (
                    column,
                    variable,
                    self.known[variable].scale,
                )
                for column, variable in self.column_to_var.items()
            )
        )


@dataclass(frozen=True)
class PhysicalClaim:
    column: int
    scale: float
    derivation: Derivation
    strict_bad: int
    loose_bad: int
    residual: float

    @property
    def rank(self):
        return (
            self.strict_bad,
            self.loose_bad,
            self.residual,
            self.column,
            self.derivation.id,
        )


def _physical_claims(
    ctx: RunContext,
    pending: dict[str, dict[int, tuple[Derivation, NumericValue]]],
    unavailable: set[int],
    *,
    exact: bool,
) -> list[PhysicalClaim]:
    table = ctx.table
    available_mask = np.zeros(table.raw.shape[0], dtype=bool)
    available_mask[table.representatives] = True
    if unavailable:
        available_mask[np.fromiter(unavailable, dtype=int)] = False
    available = np.flatnonzero(available_mask)
    if available.size == 0:
        return []
    allowed = 0 if exact else _allowed_bad(table.raw.shape[1], ctx.cfg)
    ready = []
    for out, predictions in pending.items():
        for derivation, value in predictions.values():
            if derivation.kind == "money":
                informative = np.sum(
                    np.abs(value.values)
                    > value.tolerance + ctx.cfg.cert_slack
                )
                if informative < ctx.cfg.min_informative_rows:
                    continue
            ready.append((out, derivation, value))
    scores_by_prediction = ctx.score_many(
        (value, derivation)
        for _, derivation, value in ready
    )
    claims = []
    for (out, derivation, value), scores in zip(
        ready,
        scores_by_prediction,
    ):
        bad = scores.strict_bad if exact else scores.loose_bad
        qualified = available[bad[available] <= allowed]
        if qualified.size == 0:
            continue
        order = np.lexsort(
            (
                qualified,
                scores.residual[qualified],
                scores.loose_bad[qualified],
                scores.strict_bad[qualified],
            )
        )
        column = int(qualified[order[0]])
        claims.append(
            PhysicalClaim(
                column=column,
                scale=float(scores.scale[column]),
                derivation=derivation,
                strict_bad=int(scores.strict_bad[column]),
                loose_bad=int(scores.loose_bad[column]),
                residual=float(scores.residual[column]),
            )
        )
    return claims


def _select_claims(claims: list[PhysicalClaim]) -> list[PhysicalClaim]:
    if not claims:
        return []
    best_by_out: dict[str, PhysicalClaim] = {}
    best_by_column: dict[int, PhysicalClaim] = {}
    for claim in claims:
        prior = best_by_out.get(claim.derivation.out)
        if prior is None or claim.rank < prior.rank:
            best_by_out[claim.derivation.out] = claim
        prior = best_by_column.get(claim.column)
        if prior is None or claim.rank < prior.rank:
            best_by_column[claim.column] = claim
    mutual = [
        claim
        for claim in best_by_out.values()
        if best_by_column[claim.column] is claim
    ]
    if mutual:
        return sorted(mutual, key=lambda claim: claim.rank)
    # An exact globally-best claim is still deterministic and safe.  Robust
    # callers receive no claim here and will materialize the virtual frontier.
    return []


def _close_graph(
    ctx: RunContext,
    fragment: Fragment,
) -> ClosedGraph:
    known: dict[str, NumericValue] = {}
    column_to_var: dict[int, str] = {}
    for assignment in fragment.assignments:
        known[assignment.variable] = ctx.observed(
            assignment.variable,
            assignment.column,
            scale=assignment.scale,
        )
        column_to_var[assignment.column] = assignment.variable

    # A dense structural motif can already explain every representative
    # physical column.  Virtual closure cannot discover another physical
    # assignment in that state, and finalist analysis will independently
    # verify the active identities.  Avoid deriving the virtual graph twice
    # on the overwhelmingly common 10–20 column path.
    if len(column_to_var) == int(ctx.table.representatives.size):
        return ClosedGraph(
            fragment=fragment,
            known=known,
            column_to_var=column_to_var,
            conflicts=[],
        )

    queue = deque(known)
    evaluated: set[tuple] = set()
    pending: dict[
        str,
        dict[int, tuple[Derivation, NumericValue]],
    ] = {}
    conflicts: list[str] = []
    while True:
        # Only derivations touched by newly grounded variables are inspected.
        while queue:
            variable = queue.popleft()
            for derivation in WAITING_ON[variable]:
                if not all(name in known for name in derivation.inputs):
                    continue
                signature = (
                    derivation.id,
                    tuple(known[name].id for name in derivation.inputs),
                )
                if signature in evaluated:
                    continue
                evaluated.add(signature)
                value = ctx.derive(derivation, known)
                if value is None:
                    continue
                observed = known.get(derivation.out)
                if observed is not None:
                    if not _values_agree(
                        value,
                        observed,
                        ctx.cfg,
                        robust=True,
                    ):
                        conflicts.append(derivation.identity_id)
                    continue
                pending.setdefault(derivation.out, {})[
                    value.id
                ] = (derivation, value)

        if not pending:
            break

        unavailable = set(column_to_var)
        claims = _select_claims(
            _physical_claims(
                ctx,
                pending,
                unavailable,
                exact=True,
            )
        )
        if not claims:
            robust_claims = _physical_claims(
                ctx,
                pending,
                unavailable,
                exact=False,
            )
            mutual = _select_claims(robust_claims)
            # Robust assignments require an unambiguous mutual best.
            claims = mutual

        if claims:
            for claim in claims:
                if (
                    claim.derivation.out in known
                    or claim.column in column_to_var
                ):
                    continue
                observed = ctx.observed(
                    claim.derivation.out,
                    claim.column,
                    scale=claim.scale,
                )
                known[claim.derivation.out] = observed
                column_to_var[claim.column] = claim.derivation.out
                pending.pop(claim.derivation.out, None)
                queue.append(claim.derivation.out)
            continue

        # Physical fixpoint.  Materialize every numerically agreeing output
        # in the current virtual frontier at once.
        virtuals = []
        for out, predictions in sorted(
            pending.items(),
            key=lambda item: VAR_INDEX[item[0]],
        ):
            alternatives = list(predictions.values())
            derivation, value = min(
                alternatives,
                key=lambda item: (
                    item[1].support.bit_count(),
                    item[0].id,
                ),
            )
            if all(
                _values_agree(
                    value,
                    other,
                    ctx.cfg,
                    robust=True,
                )
                for _, other in alternatives
            ):
                virtuals.append((out, value))
            else:
                conflicts.append(f"conflicting derivations for {out}")
        if not virtuals:
            break
        for out, value in virtuals:
            if out in known:
                continue
            known[out] = value
            pending.pop(out, None)
            queue.append(out)

    return ClosedGraph(
        fragment=fragment,
        known=known,
        column_to_var=column_to_var,
        conflicts=conflicts,
    )


def _close_unique_states(
    ctx: RunContext,
    fragments: list[Fragment],
) -> list[ClosedGraph]:
    """Progressively close fragments and deduplicate immediately."""
    unique: dict[tuple, ClosedGraph] = {}
    first_score = fragments[0].score if fragments else 0
    for index, fragment in enumerate(fragments):
        if (
            index >= ctx.cfg.initial_closures
            and unique
            and fragment.score < first_score
        ):
            break
        graph = _close_graph(ctx, fragment)
        prior = unique.get(graph.mapping_key)
        if prior is None or (
            graph.fragment.score,
            -len(graph.conflicts),
        ) > (
            prior.fragment.score,
            -len(prior.conflicts),
        ):
            unique[graph.mapping_key] = graph
    return sorted(
        unique.values(),
        key=lambda graph: (
            -len(graph.column_to_var),
            len(graph.conflicts),
            -graph.fragment.score,
            graph.fragment.residual,
            graph.mapping_key,
        ),
    )


# ---------------------------------------------------------------------------
# Finalist-only numeric evidence and exact graph redundancy
# ---------------------------------------------------------------------------


@lru_cache(maxsize=512)
def _minimum_seed_count(
    physical_variables: tuple[str, ...],
    active_derivations: tuple[Derivation, ...],
) -> int:
    target = reduce(
        or_,
        (VAR_BIT[variable] for variable in physical_variables),
        0,
    )
    transitions = tuple(
        (
            reduce(
                or_,
                (VAR_BIT[variable] for variable in derivation.inputs),
                0,
            ),
            VAR_BIT[derivation.out],
        )
        for derivation in active_derivations
    )
    def closure(seed: int) -> int:
        known = seed
        changed = True
        while changed:
            changed = False
            for required, output in transitions:
                if known & required == required and not known & output:
                    known |= output
                    changed = True
        return known

    for size in range(len(physical_variables) + 1):
        for selected in combinations(physical_variables, size):
            seed = reduce(
                or_,
                (VAR_BIT[variable] for variable in selected),
                0,
            )
            if closure(seed) & target == target:
                return size
    return len(physical_variables)


def _identity_checks(
    identity: Identity,
    ready: list,
    output: Callable[[object], str],
) -> list:
    """Select one proof per identity, or each required clipped output."""
    if not ready:
        return []
    if not identity.verification_outputs:
        return ready[:1]
    first_by_output = {}
    for item in ready:
        first_by_output.setdefault(output(item), item)
    return [
        first_by_output[out]
        for out in identity.verification_outputs
        if out in first_by_output
    ]


def _analyse_finalist(
    ctx: RunContext,
    graph: ClosedGraph,
    *,
    recovery: bool = False,
) -> ClosedGraph:
    # Dense motifs intentionally skip virtual closure during candidate
    # search.  Complete only the bounded finalist set so public virtuals,
    # evidence, certification, and diagnosis all see the full semantic
    # graph without taxing every discarded state.
    def merge_numeric(out, alternatives):
        merged = _merge_numeric_alternatives(ctx, alternatives)
        if merged is None:
            graph.conflicts.append(
                f"conflicting finalist derivations for {out}"
            )
        return merged

    graph.known, _ = _propagate_virtuals(
        dict(graph.known),
        frozenset(DERIVATION_BY_ID),
        ctx.derive,
        lambda value: value.id,
        merge_numeric,
    )

    physical = {
        variable: value
        for variable, value in graph.known.items()
        if value.column is not None
    }
    all_columns = reduce(
        or_,
        (1 << int(value.column) for value in physical.values()),
        0,
    )
    physical_variables = frozenset(physical)

    # Evidence must pass numerically.  Certification constraints are broader:
    # once a semantic mapping is selected, every predefined identity touching
    # a printed variable must be checked even when several rows violate it.
    # Otherwise repeated errors can make their own identity disappear before
    # strict certification sees them.
    certification_derivations = frozenset(
        derivation.id
        for identity in IDENTITIES
        if physical_variables.intersection(identity.variables)
        for derivation in identity.derivations
        if (
            derivation.out in graph.known
            and all(
                variable in graph.known
                for variable in derivation.inputs
            )
        )
    )

    # Verify each identity once, not every algebraic rearrangement.  Once the
    # identity holds, its ready derivations are computational directions over
    # the same fact; the subsequent independence closure is Boolean
    # provenance only.  Clipped U/O presentation has two forward claims and
    # therefore explicitly verifies both outputs.
    active = []
    evidence_cfg = (
        replace(
            ctx.cfg,
            ident_frac=ctx.cfg.shadow_audit_frac,
        )
        if recovery
        and ctx.cfg.ident_frac > ctx.cfg.shadow_audit_frac
        else ctx.cfg
    )
    for identity in IDENTITIES:
        if not identity.evidence:
            continue
        ready = [
            derivation
            for derivation in identity.derivations
            if (
                derivation.out in graph.known
                and all(
                    variable in graph.known
                    for variable in derivation.inputs
                )
            )
        ]
        if not ready:
            continue
        checks = _identity_checks(
            identity,
            ready,
            lambda derivation: derivation.out,
        )
        if not checks:
            continue
        verified = True
        for derivation in checks:
            predicted = ctx.derive(derivation, graph.known)
            if (
                predicted is None
                or not _values_agree(
                    predicted,
                    graph.known[derivation.out],
                    evidence_cfg,
                    robust=True,
                )
            ):
                verified = False
                break
        if verified:
            active.extend(ready)

    independent = {
        variable: (
            all_columns & ~(1 << int(value.column))
            if value.column is not None
            else 0
        )
        for variable, value in graph.known.items()
    }
    changed = True
    while changed:
        changed = False
        for derivation in active:
            mask = all_columns
            for variable in derivation.inputs:
                mask &= independent.get(variable, 0)
            updated = independent.get(derivation.out, 0) | mask
            if updated != independent.get(derivation.out, 0):
                independent[derivation.out] = updated
                changed = True

    checkable = frozenset(
        variable
        for variable, value in physical.items()
        if independent.get(variable, 0)
        & (1 << int(value.column))
    )
    coverage = {
        region: len(checkable & variables)
        for region, variables in REGIONS.items()
    }
    physical_variables = tuple(
        sorted(physical, key=VAR_INDEX.__getitem__)
    )
    minimum_seeds = _minimum_seed_count(
        physical_variables,
        tuple(active),
    )
    graph.certification_derivations = certification_derivations
    graph.checkable = checkable
    graph.coverage = coverage
    graph.minimum_seeds = minimum_seeds
    graph.redundancy = max(0, len(physical) - minimum_seeds)
    return graph


def _coverage_ok(graph: ClosedGraph, cfg: Config) -> bool:
    return all(
        graph.coverage.get(region, 0) >= cfg.min_region_checkable
        for region in REGIONS
    )


def _analyse_finalists(
    ctx: RunContext,
    closed: list[ClosedGraph],
    *,
    recovery: bool = False,
) -> list[ClosedGraph]:
    analysed = [
        _analyse_finalist(ctx, graph, recovery=recovery)
        for graph in closed[: ctx.cfg.finalist_limit]
        if all(variable in graph.known for variable in CORE_VARS)
    ]
    return sorted(
        analysed,
        key=lambda graph: (
            not _coverage_ok(graph, ctx.cfg),
            -graph.redundancy,
            -len(graph.column_to_var),
            len(graph.conflicts),
            -graph.fragment.score,
            graph.fragment.residual,
            graph.mapping_key,
        ),
    )


# ---------------------------------------------------------------------------
# Strict certification and minimal-suspect graph diagnosis
# ---------------------------------------------------------------------------


def _full_physical_values(
    ctx: RunContext,
    graph: ClosedGraph,
) -> dict[str, NumericValue]:
    if ctx.table.row_index.size == ctx.table.full.shape[0]:
        return {
            variable: value
            for variable, value in graph.known.items()
            if value.column is not None
        }
    return {
        variable: ctx.observed(
            variable,
            int(value.column),
            scale=value.scale,
            full=True,
        )
        for variable, value in graph.known.items()
        if value.column is not None
    }


def _propagate_virtuals(
    known: dict,
    active_derivations: frozenset[str],
    derive: Callable[[Derivation, dict], Optional[object]],
    value_key: Callable[[object], object],
    merge: Callable[
        [str, list[tuple[Derivation, object]]],
        Optional[tuple[object, list[str]]],
    ],
) -> tuple[dict, dict[str, list[str]]]:
    """Dependency-driven closure shared by certification and repair."""
    queue = deque(known)
    evaluated = set()
    pending: dict[str, dict[object, tuple[Derivation, object]]] = {}
    proofs: dict[str, list[str]] = {}

    while True:
        while queue:
            variable = queue.popleft()
            for derivation in WAITING_ON[variable]:
                if derivation.id not in active_derivations:
                    continue
                if derivation.out in known:
                    continue
                if not all(name in known for name in derivation.inputs):
                    continue
                signature = (
                    derivation.id,
                    tuple(
                        value_key(known[name])
                        for name in derivation.inputs
                    ),
                )
                if signature in evaluated:
                    continue
                evaluated.add(signature)
                value = derive(derivation, known)
                if value is None:
                    continue
                pending.setdefault(derivation.out, {})[
                    value_key(value)
                ] = (derivation, value)
        if not pending:
            break
        made = False
        for out, alternatives_by_key in list(pending.items()):
            merged = merge(
                out,
                list(alternatives_by_key.values()),
            )
            if merged is None:
                continue
            value, basis = merged
            known[out] = value
            proofs[out] = basis
            queue.append(out)
            pending.pop(out, None)
            made = True
        if not made:
            break
    return known, proofs


def _merge_numeric_alternatives(
    ctx: RunContext,
    alternatives: list[tuple[Derivation, NumericValue]],
) -> Optional[tuple[NumericValue, list[str]]]:
    derivation, value = min(
        alternatives,
        key=lambda item: (
            item[1].support.bit_count(),
            item[0].id,
        ),
    )
    if not all(
        _values_agree(
            value,
            other,
            ctx.cfg,
            robust=True,
        )
        for _, other in alternatives
    ):
        return None
    return value, sorted(
        candidate.id
        for candidate, _ in alternatives
    )


def _certify(
    ctx: RunContext,
    graph: ClosedGraph,
    labels: list[str],
) -> tuple[
    list[Witness],
    list[RowFailure],
    list[dict],
    dict[str, NumericValue],
]:
    cfg = ctx.cfg
    physical = _full_physical_values(ctx, graph)
    witnesses = []
    failures = []
    incomplete = []

    def merge_numeric(out, alternatives):
        del out
        return _merge_numeric_alternatives(ctx, alternatives)

    if ctx.table.row_index.size == ctx.table.full.shape[0]:
        full_known = graph.known
    else:
        full_known, _ = _propagate_virtuals(
            dict(physical),
            graph.certification_derivations,
            ctx.derive,
            lambda value: value.id,
            merge_numeric,
        )

    checks = []
    for identity in IDENTITIES:
        ready = []
        for derivation in identity.derivations:
            if (
                derivation.id not in graph.certification_derivations
                or derivation.out not in physical
                or not all(
                    variable in full_known
                    for variable in derivation.inputs
                )
            ):
                continue
            output_column = int(physical[derivation.out].column)
            input_support = reduce(
                or_,
                (
                    full_known[variable].support
                    for variable in derivation.inputs
                ),
                0,
            )
            if input_support & (1 << output_column):
                continue
            expected = ctx.derive(derivation, full_known)
            if (
                expected is not None
            ):
                ready.append((derivation, expected))
        ready.sort(
            key=lambda item: (
                item[1].support.bit_count(),
                item[0].id,
            )
        )
        checks.extend(
            (identity, item)
            for item in _identity_checks(
                identity,
                ready,
                lambda candidate: candidate[0].out,
            )
        )

    for identity, (derivation, expected) in checks:
        variable = derivation.out
        observed = physical[variable]
        valid = (
            np.isfinite(observed.values)
            & np.isfinite(expected.values)
        )
        if variable in PCT_VARS:
            tolerance = (
                observed.tolerance
                + expected.tolerance
                + 1e-9
            )
        else:
            tolerance = (
                observed.tolerance
                + expected.tolerance
                + cfg.cert_slack
                + cfg.cert_money_rel * np.abs(expected.values)
        )
        residual = np.abs(observed.values - expected.values)
        relation = identity.statement
        witnesses.append(
            Witness(
                relation=relation,
                business_form=(
                    f"{VAR_NAMES[variable]} reconstructed independently"
                ),
                column=observed.column,
                n_rows=int(valid.sum()),
                n_informative=int(
                    np.sum(
                        valid
                        & (
                            np.abs(expected.values)
                            > tolerance + 1e-9
                        )
                    )
                ),
                max_abs_residual=float(
                    residual[valid].max(initial=0.0)
                ),
                weight=1.0,
                family=identity.id,
            )
        )
        for row in np.flatnonzero(valid & (residual > tolerance)):
            failures.append(
                RowFailure(
                    relation=relation,
                    business_form=(
                        f"{VAR_NAMES[variable]} reconstructed independently"
                    ),
                    variable=variable,
                    column=observed.column,
                    row_index=int(row),
                    row_label=labels[int(row)],
                    observed=float(observed.values[row]),
                    expected=float(expected.values[row]),
                    difference=float(
                        observed.values[row] - expected.values[row]
                    ),
                    tolerance=float(tolerance[row]),
                )
            )
        missing = np.flatnonzero(~valid)
        if missing.size:
            incomplete.append(
                {
                    "variable": variable,
                    "column": observed.column,
                    "rows": [int(row) for row in missing],
                }
            )
    return witnesses, failures, incomplete, physical


def _audit_shadowed_virtuals(
    ctx: RunContext,
    graph: ClosedGraph,
    labels: list[str],
) -> tuple[list[RowFailure], dict[int, str]]:
    """Recover omitted printed money columns after winner selection only.

    A majority-fitting column may certify a virtual value, but it cannot
    contribute evidence, alter the selected graph, or reroute an anchor.
    """
    cfg = ctx.cfg
    table = ctx.table
    assigned = set(graph.column_to_var)
    unassigned = [
        column
        for column in range(table.raw.shape[0])
        if column not in assigned
    ]
    if not unassigned:
        return [], {}

    failures = []
    recovered = {}
    for column in unassigned:
        observed = table.raw[column]
        candidates = []
        for variable, value in graph.known.items():
            if value.column is not None or variable in PCT_VARS:
                continue
            strict = (
                value.tolerance
                + cfg.money_obs_tol
                + cfg.cert_slack
                + cfg.cert_money_rel * np.abs(value.values)
            )
            compared = (
                np.abs(observed)
                if variable in MAGNITUDE_PRESENTATION_VARS
                else observed
            )
            informative = (
                (np.abs(value.values) > strict + 1e-9)
                | (np.abs(compared) > strict + 1e-9)
            )
            n_informative = int(informative.sum())
            if n_informative < cfg.min_informative_rows:
                continue
            residual = np.abs(compared - value.values)
            fit = int(((residual <= strict) & informative).sum())
            required = max(
                cfg.min_informative_rows,
                int(np.ceil(cfg.shadow_audit_frac * n_informative)),
            )
            if required <= fit < n_informative:
                candidates.append(
                    (
                        fit / n_informative,
                        fit,
                        n_informative,
                        variable,
                        value,
                        strict,
                        residual,
                        compared,
                        informative,
                    )
                )
        if not candidates:
            continue
        candidates.sort(
            key=lambda item: (item[0], item[1]),
            reverse=True,
        )
        best = candidates[0]
        if (
            len(candidates) > 1
            and candidates[1][0:2] == best[0:2]
            and candidates[1][3] != best[3]
        ):
            continue
        (
            _,
            fit,
            n_informative,
            variable,
            value,
            strict,
            residual,
            compared,
            informative,
        ) = best
        recovered[column] = variable
        bad = np.flatnonzero((residual > strict) & informative)
        for row in bad:
            original_row = int(table.row_index[row])
            failures.append(
                RowFailure(
                    relation=(
                        f"column {column} realizes {variable} "
                        f"({value.derivation}) but disagrees"
                    ),
                    business_form=(
                        f"unmapped column {column} matches "
                        f"{VAR_NAMES[variable]} on "
                        f"{fit}/{n_informative} informative rows"
                    ),
                    variable=variable,
                    column=column,
                    row_index=original_row,
                    row_label=labels[original_row],
                    observed=float(observed[row]),
                    expected=float(value.values[row]),
                    difference=float(
                        compared[row] - value.values[row]
                    ),
                    tolerance=float(strict[row]),
                )
            )
    return failures, recovered


def _scalar_prediction(
    derivation: Derivation,
    values: dict[str, float],
    tolerances: dict[str, float],
) -> Optional[tuple[float, float]]:
    input_values = tuple(
        np.asarray([values[variable]], dtype=float)
        for variable in derivation.inputs
    )
    input_tolerances = tuple(
        np.asarray([tolerances[variable]], dtype=float)
        for variable in derivation.inputs
    )
    with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
        output = float(derivation.fn(*input_values)[0])
        tolerance = float(
            derivation.tol_fn(
                input_values,
                input_tolerances,
            )[0]
        )
    if not np.isfinite(output) or not np.isfinite(tolerance):
        return None
    return output, max(tolerance, 1e-12)


def _scalar_agreement(
    candidates: list[tuple[Derivation, float, float]],
    variable: str,
    cfg: Config,
) -> Optional[tuple[float, float, list[str]]]:
    if not candidates:
        return None
    values = [candidate[1] for candidate in candidates]
    center = float(np.median(values))
    tolerance = max(candidate[2] for candidate in candidates)
    allowance = (
        cfg.pct_ident_slack
        if variable in PCT_VARS
        else max(cfg.ident_abs, cfg.ident_rel * abs(center))
    )
    if any(
        abs(value - center) > tolerance + allowance
        for value in values
    ):
        return None
    return (
        center,
        tolerance,
        sorted({candidate[0].id for candidate in candidates}),
    )


@dataclass(frozen=True)
class _ScalarValue:
    value: float
    tolerance: float
    key: tuple


def _row_repair(
    graph: ClosedGraph,
    physical: dict[str, NumericValue],
    row: int,
    suspects: tuple[str, ...],
    cfg: Config,
) -> Optional[list[dict]]:
    suspect_set = frozenset(suspects)
    known = {
        variable: _ScalarValue(
            value=float(value.values[row]),
            tolerance=float(value.tolerance[row]),
            key=("physical", variable, row),
        )
        for variable, value in physical.items()
        if variable not in suspect_set
        and np.isfinite(value.values[row])
    }

    def derive_scalar(derivation, current):
        predicted = _scalar_prediction(
            derivation,
            {
                variable: value.value
                for variable, value in current.items()
            },
            {
                variable: value.tolerance
                for variable, value in current.items()
            },
        )
        if predicted is None:
            return None
        value, tolerance = predicted
        if derivation.out in PCT_VARS:
            observed = physical.get(derivation.out)
            if observed is not None and observed.grid is not None:
                value = round(value / observed.grid) * observed.grid
        if derivation.out in suspect_set and derivation.out in physical:
            tolerance = float(
                physical[derivation.out].tolerance[row]
            )
        return _ScalarValue(
            value=value,
            tolerance=tolerance,
            key=(
                derivation.id,
                tuple(
                    current[variable].key
                    for variable in derivation.inputs
                ),
            ),
        )

    def merge_scalars(variable, alternatives):
        agreed = _scalar_agreement(
            [
                (
                    derivation,
                    value.value,
                    value.tolerance,
                )
                for derivation, value in alternatives
            ],
            variable,
            cfg,
        )
        if agreed is None:
            return None
        value, tolerance, basis = agreed
        return (
            _ScalarValue(
                value=value,
                tolerance=tolerance,
                key=(
                    "merged",
                    variable,
                    tuple(
                        value.key
                        for _, value in alternatives
                    ),
                ),
            ),
            basis,
        )

    known, proofs = _propagate_virtuals(
        known,
        graph.certification_derivations,
        derive_scalar,
        lambda value: value.key,
        merge_scalars,
    )
    values = {
        variable: value.value
        for variable, value in known.items()
    }
    tolerances = {
        variable: value.tolerance
        for variable, value in known.items()
    }

    if not suspect_set <= values.keys():
        return None
    for variable in suspects:
        observed = float(physical[variable].values[row])
        material = (
            max(
                1e-9,
                float(physical[variable].tolerance[row]),
            )
            if variable in PCT_VARS
            else max(
                1.0,
                float(physical[variable].tolerance[row])
                + cfg.cert_slack,
            )
        )
        if abs(values[variable] - observed) <= material:
            return None

    # A repair must make the entire printed row coherent, not just the
    # identity that proposed it.
    for derivation_id in graph.certification_derivations:
        derivation = DERIVATION_BY_ID[derivation_id]
        if (
            derivation.out not in physical
            or derivation.out not in values
            or not all(
                variable in values
                for variable in derivation.inputs
            )
        ):
            continue
        predicted = _scalar_prediction(
            derivation,
            values,
            tolerances,
        )
        if predicted is None:
            return None
        expected, propagated = predicted
        observed = values[derivation.out]
        if derivation.out in PCT_VARS:
            strict = (
                propagated
                + tolerances[derivation.out]
                + 1e-9
            )
        else:
            strict = (
                propagated
                + tolerances[derivation.out]
                + cfg.cert_slack
                + cfg.cert_money_rel * abs(expected)
            )
        if abs(observed - expected) > strict:
            return None

    return [
        {
            "variable": variable,
            "column": physical[variable].column,
            "observed": float(physical[variable].values[row]),
            "proposed": float(values[variable]),
            "basis": proofs[variable],
        }
        for variable in suspects
    ]


def _minimal_row_repair(
    graph: ClosedGraph,
    physical: dict[str, NumericValue],
    row: int,
    cfg: Config,
):
    variables = tuple(
        sorted(physical, key=VAR_INDEX.__getitem__)
    )
    for size in (1, 2):
        solutions = []
        for suspects in combinations(variables, size):
            solution = _row_repair(
                graph,
                physical,
                row,
                suspects,
                cfg,
            )
            if solution is not None:
                solutions.append(solution)
        unique = {}
        for solution in solutions:
            key = tuple(
                (
                    item["variable"],
                    round(item["proposed"], 8),
                )
                for item in solution
            )
            unique[key] = solution
        solutions = list(unique.values())
        if len(solutions) == 1:
            return "resolved", solutions[0]
        if len(solutions) > 1:
            choices = sorted(
                {
                    item["variable"]
                    for solution in solutions
                    for item in solution
                },
                key=VAR_INDEX.__getitem__,
            )
            return "ambiguous", choices
    return "none", []


def _money_display_grid(values: np.ndarray) -> float:
    """Infer decimal display precision; fall back to ordinary cents."""
    finite = np.asarray(values, dtype=float)
    finite = finite[np.isfinite(finite)]
    for grid in (1.0, 0.1, 0.01, 0.001, 0.0001):
        rounded = np.round(finite / grid) * grid
        if np.all(
            np.abs(finite - rounded)
            <= 1e-9 + 1e-9 * np.abs(finite)
        ):
            return grid
    return 0.01


def _quantize_correction(proposed: float, grid: Optional[float]) -> float:
    if grid is None or grid <= 0:
        return proposed
    return float(round(proposed / grid) * grid)


def _unresolved_finding(
    row: int,
    label: str,
    candidates: list[str],
    failures: list[RowFailure],
    *,
    ambiguous: bool,
) -> Finding:
    return Finding(
        row_index=row,
        row_label=label,
        culprit_column=None,
        culprit_variable=None,
        candidate_variables=candidates,
        exonerated_variables=[],
        observed=None,
        proposed_correction=None,
        correction_basis=[],
        confidence="low",
        classification=(
            "ambiguous_multi_cell"
            if ambiguous
            else "unresolved_constraint_conflict"
        ),
        classification_detail=(
            "Several minimal observation removals can make this row "
            "internally coherent."
            if ambiguous
            else "No unique one- or two-observation repair makes the "
            "complete row coherent."
        ),
        transplant_sources=[],
        failing_relations=sorted(
            {failure.relation for failure in failures}
        ),
        proof_kind="joint",
    )


def _diagnose(
    graph: ClosedGraph,
    physical: dict[str, NumericValue],
    failures: list[RowFailure],
    labels: list[str],
    full_matrix: np.ndarray,
    cfg: Config,
) -> list[Finding]:
    by_row: dict[int, list[RowFailure]] = {}
    for failure in failures:
        by_row.setdefault(failure.row_index, []).append(failure)
    findings = []
    full_columns = [
        full_matrix[:, column]
        for column in range(full_matrix.shape[1])
    ]
    money_grids: dict[int, float] = {}
    for row, row_failures in sorted(by_row.items()):
        status, detail = _minimal_row_repair(
            graph,
            physical,
            row,
            cfg,
        )
        if status == "resolved":
            joint = len(detail) > 1
            suspect_variables = {
                correction["variable"]
                for correction in detail
            }
            for correction in detail:
                variable = correction["variable"]
                column = int(correction["column"])
                scale = physical[variable].scale
                observed = correction["observed"] * scale
                grid = (
                    physical[variable].grid
                    if variable in PCT_VARS
                    else money_grids.setdefault(
                        column,
                        _money_display_grid(full_columns[column]),
                    )
                )
                proposed = (
                    _quantize_correction(correction["proposed"], grid)
                    * scale
                )
                classification, classification_detail = _classify_error(
                    observed,
                    proposed,
                )
                basis_families = {
                    DERIVATION_BY_ID[derivation_id].identity_id
                    for derivation_id in correction["basis"]
                    if derivation_id in DERIVATION_BY_ID
                }
                proof_kind = (
                    "joint"
                    if joint
                    else (
                        "direct"
                        if len(basis_families)
                        >= cfg.correction_min_families
                        else "inherited"
                    )
                )
                if proof_kind == "inherited":
                    classification_detail += (
                        "; replacement is uniquely determined after every "
                        "alternative one-cell repair is rejected by the "
                        "row's other validated identities"
                    )
                elif proof_kind == "joint":
                    classification_detail += (
                        "; replacement is part of the unique smallest "
                        f"{len(detail)}-cell graph repair"
                    )
                transplant_sources = _transplant_sources(
                    full_columns,
                    row,
                    column,
                    observed,
                )
                if (
                    transplant_sources
                    and classification
                    in ("unexplained_substitution", "digit_transposition")
                ):
                    classification = "neighbor_transplant"
                    classification_detail = (
                        "observed value equals a neighboring cell; the "
                        "unique minimal repair implies the replacement"
                    )
                findings.append(
                    Finding(
                        row_index=row,
                        row_label=labels[row],
                        culprit_column=correction["column"],
                        culprit_variable=variable,
                        candidate_variables=[variable],
                        exonerated_variables=sorted(
                            set(physical) - suspect_variables
                        ),
                        observed=observed,
                        proposed_correction=proposed,
                        correction_basis=[
                            IDENTITY_BY_ID[identity_id].statement
                            for identity_id in sorted(basis_families)
                        ],
                        confidence="high",
                        classification=classification,
                        classification_detail=classification_detail,
                        transplant_sources=transplant_sources,
                        failing_relations=sorted(
                            {
                                failure.relation
                                for failure in row_failures
                            }
                        ),
                        proof_kind=proof_kind,
                    )
                )
        else:
            findings.append(
                _unresolved_finding(
                    row,
                    labels[row],
                    (
                        detail
                        if status == "ambiguous"
                        else sorted(
                            {
                                failure.variable
                                for failure in row_failures
                            },
                            key=VAR_INDEX.__getitem__,
                        )
                    ),
                    row_failures,
                    ambiguous=status == "ambiguous",
                )
            )
    return findings


# ---------------------------------------------------------------------------
# Public orchestration
# ---------------------------------------------------------------------------


def _config(config) -> Config:
    if config is None:
        return Config()
    if isinstance(config, Config):
        return config
    result = Config()
    result.__dict__.update(getattr(config, "__dict__", {}))
    return result


def _solve_frontier(
    ctx: RunContext,
    **discovery_options,
) -> tuple[list[Fragment], dict, list[ClosedGraph], list[ClosedGraph]]:
    fragments, discovery = _discover_states(ctx, **discovery_options)
    closed = _close_unique_states(ctx, fragments) if fragments else []
    finalists = (
        _analyse_finalists(
            ctx,
            closed,
            recovery=bool(discovery_options.get("exhaustive")),
        )
        if closed
        else []
    )
    return fragments, discovery, closed, finalists


def _insufficient(reason: str, diagnostics: dict) -> ValidationResult:
    return ValidationResult(
        status=INSUFFICIENT,
        reason=reason,
        diagnostics=diagnostics,
    )


def validate_wip(
    columns,
    job_labels=None,
    config=None,
) -> ValidationResult:
    cfg = _config(config)
    physical_columns, labels = _ingest(columns, job_labels)
    matrix = (
        np.column_stack(physical_columns)
        if physical_columns
        else np.empty((0, 0), dtype=float)
    )
    diagnostics = {
        "engine": "wip2_search_compressed_constraint_graph",
        "notes": [],
        "prepared_once": True,
    }
    if matrix.shape[1] == 0:
        return _insufficient(
            "empty input: no columns provided",
            diagnostics,
        )
    if matrix.shape[1] < 4:
        return _insufficient(
            (
                f"only {matrix.shape[1]} column(s); too few physical "
                "observations to ground estimate, progress, and billing"
            ),
            diagnostics,
        )
    complete = np.all(np.isfinite(matrix), axis=1)
    if int(complete.sum()) < cfg.min_rows:
        return _insufficient(
            (
                f"only {int(complete.sum())} usable row(s) of "
                f"{matrix.shape[0]}; need at least {cfg.min_rows}"
            ),
            diagnostics,
        )
    if np.any(~complete):
        diagnostics["notes"].append(
            f"{int((~complete).sum())} row(s) excluded from identification; "
            "retained for strict full-document certification"
        )

    table = PreparedTable.build(matrix, cfg)
    ctx = RunContext(table, cfg)
    frontiers = (
        {},
        {"broaden": True},
        {"broaden": True, "exhaustive": True},
    )
    for frontier_index, options in enumerate(frontiers):
        fragments, discovery, closed, finalists = _solve_frontier(
            ctx,
            **options,
        )
        if frontier_index == 0:
            initial_discovery = discovery
        if finalists and _coverage_ok(finalists[0], cfg):
            break

    # Magnitude orders the first compressed frontier but never gates
    # correctness.  Later frontiers progressively admit every plausible
    # additive hub, then tolerate repeated corruptions during proposal only.
    widened = frontier_index >= 1
    exhaustive_widening = frontier_index >= 2
    if widened:
        discovery = {
            **discovery,
            "initial_estimate_fragments": initial_discovery[
                "estimate_fragments"
            ],
            "initial_assembled_states": initial_discovery[
                "canonical_assembled_states"
            ],
        }

    diagnostics["discovery"] = {
        **discovery,
        "progressive_widening": widened,
        "exhaustive_widening": exhaustive_widening,
        "representative_columns": int(table.representatives.size),
        "duplicate_columns": int(
            matrix.shape[1] - table.representatives.size
        ),
    }
    diagnostics["closure"] = {
        "fragments_considered": len(fragments),
        "unique_graph_states": len(closed),
    }
    diagnostics["finalists_analysed"] = len(finalists)
    if not fragments:
        return _insufficient(
            (
                "could not assemble a coherent estimate/progress/billing "
                "constraint graph from the observed columns"
            ),
            diagnostics,
        )
    if not closed:
        return _insufficient(
            "structural fragments did not close to a semantic graph",
            diagnostics,
        )
    if not finalists:
        return _insufficient(
            (
                "candidate graphs did not close to the required semantic core"
            ),
            diagnostics,
        )
    best = finalists[0]
    if not _coverage_ok(best, cfg):
        diagnostics["best_coverage"] = best.coverage
        diagnostics["uncertified_best_mapping"] = {
            column: variable
            for column, variable in sorted(
                best.column_to_var.items()
            )
        }
        missing = [
            region
            for region in REGIONS
            if best.coverage.get(region, 0)
            < cfg.min_region_checkable
        ]
        return _insufficient(
            (
                "identifiable but not independently validatable across "
                f"the required business regions: {', '.join(missing)}"
            ),
            diagnostics,
        )

    witnesses, failures, incomplete, physical = _certify(
        ctx,
        best,
        labels,
    )
    shadow_failures, shadow_mapping = _audit_shadowed_virtuals(
        ctx,
        best,
        labels,
    )
    failures.extend(shadow_failures)
    full_values = bool(physical) and next(iter(physical.values())).full
    for column, variable in shadow_mapping.items():
        if variable not in physical:
            physical[variable] = ctx.observed(
                variable,
                column,
                full=full_values,
            )
    findings = (
        _diagnose(
            best,
            physical,
            failures,
            labels,
            table.full,
            cfg,
        )
        if failures
        else []
    )

    # Only equally redundant, equally complete finalists can remain
    # ambiguous, and certification is allowed to refute them.
    competing = None
    if not failures and not incomplete:
        best_rank = (
            best.redundancy,
            len(best.column_to_var),
        )
        for rival in finalists[1:]:
            if (
                rival.redundancy,
                len(rival.column_to_var),
            ) != best_rank:
                break
            if rival.mapping_key == best.mapping_key:
                continue
            _, rival_failures, rival_incomplete, _ = _certify(
                ctx,
                rival,
                labels,
            )
            if not rival_failures and not rival_incomplete:
                competing = {
                    column: variable
                    for column, variable in sorted(
                        rival.column_to_var.items()
                    )
                }
                break

    mapping = {
        column: variable
        for column, variable in sorted(best.column_to_var.items())
    }
    occupied_variables = set(mapping.values())
    for column, variable in sorted(shadow_mapping.items()):
        if column not in mapping and variable not in occupied_variables:
            mapping[column] = variable
            occupied_variables.add(variable)
    if shadow_mapping:
        diagnostics["shadow_columns_promoted"] = {
            column: variable
            for column, variable in sorted(shadow_mapping.items())
            if mapping.get(column) == variable
        }
    diagnostics["evidence"] = {
        "physical_observations": len(mapping),
        "minimum_generating_seeds": best.minimum_seeds,
        "grounded_graph_redundancy": best.redundancy,
        "checkable_columns": sorted(
            best.checkable,
            key=VAR_INDEX.__getitem__,
        ),
        "business_region_coverage": best.coverage,
    }
    diagnostics["cache"] = {
        "derived_hits": ctx.derived_hits,
        "derived_misses": ctx.derived_misses,
        "score_hits": ctx.score_hits,
        "score_misses": ctx.score_misses,
        "derived_entries": len(ctx.derived_cache),
        "score_entries": len(ctx.score_cache),
    }
    diagnostics["matcher"] = {
        "batched_calls": ctx.score_batches,
        "predictions_scored": ctx.score_misses,
    }
    diagnostics["winning_sources"] = sorted(best.fragment.sources)
    diagnostics["winning_structural_score"] = best.fragment.score
    if incomplete:
        diagnostics["incomplete_full_document_checks"] = incomplete

    if competing is not None:
        return ValidationResult(
            status=INSUFFICIENT,
            reason=(
                "irreducibly ambiguous: two distinct physical-to-semantic "
                "graphs have equal grounded redundancy and both certify"
            ),
            mapping=mapping,
            mapping_named={
                column: VAR_NAMES[variable]
                for column, variable in mapping.items()
            },
            competing_mapping=competing,
            diagnostics=diagnostics,
        )

    failed = bool(failures or incomplete)
    reason = ""
    if failed:
        parts = []
        if failures:
            parts.append(
                f"{len(failures)} row-level identity violation(s)"
            )
        if incomplete:
            parts.append(
                f"{len(incomplete)} independently checkable relation(s) "
                "could not be evaluated on every row"
            )
        reason = (
            "; ".join(parts)
            + "; the schedule is not fully internally certified"
        )

    virtuals = {
        variable: value.derivation or "constraint-graph closure"
        for variable, value in best.known.items()
        if value.column is None and variable not in occupied_variables
    }
    estimate_orientation = ""
    if "C" in best.known and best.known["C"].column is not None:
        estimate_orientation = (
            f"estimate column (col {best.known['C'].column}) read as "
            f"{VAR_NAMES['C']} (C)"
        )

    return ValidationResult(
        status=FAILED if failed else SUCCESS,
        reason=reason,
        mapping=mapping,
        mapping_named={
            column: VAR_NAMES[variable]
            for column, variable in mapping.items()
        },
        estimate_orientation=estimate_orientation,
        virtuals=virtuals,
        core={
            variable: best.known[variable].values.copy()
            for variable in CORE_VARS
            if variable in best.known
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
