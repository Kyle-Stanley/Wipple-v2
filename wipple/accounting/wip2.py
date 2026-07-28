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
from dataclasses import dataclass, field
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
    detect_grid,
    render_report,
)

PCT_VARS = frozenset({"M", "P", "PB"})
VAR_ORDER = tuple(VAR_NAMES)
VAR_INDEX = {var: i for i, var in enumerate(VAR_ORDER)}
VAR_BIT = {var: 1 << i for i, var in enumerate(VAR_ORDER)}
ESTIMATE_REGION = frozenset({"V", "C", "G", "M"})
PROGRESS_REGION = frozenset({"D", "Q", "P", "E", "H", "R"})
BILLING_REGION = frozenset({"B", "N", "U", "O", "RB", "PB"})
REGIONS = {
    "estimate": ESTIMATE_REGION,
    "progress": PROGRESS_REGION,
    "billing": BILLING_REGION,
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
    clipped: bool = False


@dataclass(frozen=True)
class Identity:
    id: str
    variables: tuple[str, ...]
    region: str
    derivations: tuple[Derivation, ...]
    verification_outputs: tuple[str, ...] = ()


def _derivation(
    identity: str,
    out: str,
    inputs: tuple[str, ...],
    fn: ArrayFn,
    tol_fn: TolFn,
    kind: str = "money",
    *,
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
        clipped=clipped,
    )


def _registry() -> tuple[Identity, ...]:
    D = _derivation
    return (
        Identity(
            "estimate_complement",
            ("V", "C", "G"),
            "estimate",
            (
                D("estimate_complement", "V", ("C", "G"), lambda C, G: C + G, _tol_sum),
                D("estimate_complement", "C", ("V", "G"), lambda V, G: V - G, _tol_sum),
                D("estimate_complement", "G", ("V", "C"), lambda V, C: V - C, _tol_sum),
            ),
        ),
        Identity(
            "cost_completion",
            ("C", "D", "Q"),
            "progress",
            (
                D("cost_completion", "C", ("D", "Q"), lambda D, Q: D + Q, _tol_sum),
                D("cost_completion", "D", ("C", "Q"), lambda C, Q: C - Q, _tol_sum),
                D("cost_completion", "Q", ("C", "D"), lambda C, D: C - D, _tol_sum),
            ),
        ),
        Identity(
            "earned_revenue",
            ("E", "V", "D", "C"),
            "progress",
            (
                D("earned_revenue", "E", ("V", "D", "C"), lambda V, D, C: V * D / C, _tol_mul_div),
                D("earned_revenue", "D", ("E", "C", "V"), lambda E, C, V: E * C / V, _tol_mul_div),
                D("earned_revenue", "C", ("V", "D", "E"), lambda V, D, E: V * D / E, _tol_mul_div),
                D("earned_revenue", "V", ("E", "C", "D"), lambda E, C, D: E * C / D, _tol_mul_div),
            ),
        ),
        Identity(
            "earned_profit",
            ("H", "E", "D"),
            "progress",
            (
                D("earned_profit", "H", ("E", "D"), lambda E, D: E - D, _tol_sum),
                D("earned_profit", "E", ("H", "D"), lambda H, D: H + D, _tol_sum),
                D("earned_profit", "D", ("E", "H"), lambda E, H: E - H, _tol_sum),
            ),
        ),
        Identity(
            "net_billing",
            ("N", "E", "B"),
            "billing",
            (
                D("net_billing", "N", ("E", "B"), lambda E, B: E - B, _tol_sum),
                D("net_billing", "E", ("N", "B"), lambda N, B: N + B, _tol_sum),
                D("net_billing", "B", ("E", "N"), lambda E, N: E - N, _tol_sum),
            ),
        ),
        Identity(
            "billing_split",
            ("E", "B", "U", "O"),
            "billing",
            (
                D("billing_split", "U", ("E", "B"), lambda E, B: np.maximum(E - B, 0.0), _tol_sum, clipped=True),
                D("billing_split", "O", ("E", "B"), lambda E, B: np.maximum(B - E, 0.0), _tol_sum, clipped=True),
                D("billing_split", "E", ("B", "U", "O"), lambda B, U, O: B + U - O, _tol_sum),
                D("billing_split", "B", ("E", "U", "O"), lambda E, U, O: E - U + O, _tol_sum),
            ),
            ("U", "O"),
        ),
        Identity(
            "backlog",
            ("R", "V", "E"),
            "progress",
            (
                D("backlog", "R", ("V", "E"), lambda V, E: V - E, _tol_sum),
                D("backlog", "V", ("R", "E"), lambda R, E: R + E, _tol_sum),
                D("backlog", "E", ("V", "R"), lambda V, R: V - R, _tol_sum),
            ),
        ),
        Identity(
            "remaining_billings",
            ("RB", "V", "B"),
            "billing",
            (
                D("remaining_billings", "RB", ("V", "B"), lambda V, B: V - B, _tol_sum),
                D("remaining_billings", "V", ("RB", "B"), lambda RB, B: RB + B, _tol_sum),
                D("remaining_billings", "B", ("V", "RB"), lambda V, RB: V - RB, _tol_sum),
            ),
        ),
        Identity(
            "margin",
            ("M", "G", "V"),
            "estimate",
            (
                D("margin", "M", ("G", "V"), lambda G, V: G / V, _tol_div, "pct"),
                D("margin", "G", ("V", "M"), lambda V, M: V * M, _tol_mul),
                D("margin", "V", ("G", "M"), lambda G, M: G / M, _tol_div),
            ),
        ),
        Identity(
            "percent_complete_cost",
            ("P", "D", "C"),
            "progress",
            (
                D("percent_complete_cost", "P", ("D", "C"), lambda D, C: D / C, _tol_div, "pct"),
                D("percent_complete_cost", "D", ("C", "P"), lambda C, P: C * P, _tol_mul),
                D("percent_complete_cost", "C", ("D", "P"), lambda D, P: D / P, _tol_div),
            ),
        ),
        Identity(
            "percent_complete_revenue",
            ("P", "E", "V"),
            "progress",
            (
                D("percent_complete_revenue", "P", ("E", "V"), lambda E, V: E / V, _tol_div, "pct"),
                D("percent_complete_revenue", "E", ("V", "P"), lambda V, P: V * P, _tol_mul),
                D("percent_complete_revenue", "V", ("E", "P"), lambda E, P: E / P, _tol_div),
            ),
        ),
        Identity(
            "percent_billed",
            ("PB", "B", "V"),
            "billing",
            (
                D("percent_billed", "PB", ("B", "V"), lambda B, V: B / V, _tol_div, "pct"),
                D("percent_billed", "B", ("V", "PB"), lambda V, PB: V * PB, _tol_mul),
                D("percent_billed", "V", ("B", "PB"), lambda B, PB: B / PB, _tol_div),
            ),
        ),
    )


IDENTITIES = _registry()
DERIVATIONS = tuple(
    derivation
    for identity in IDENTITIES
    for derivation in identity.derivations
)
DERIVATION_BY_ID = {derivation.id: derivation for derivation in DERIVATIONS}
WAITING_ON = {
    var: tuple(
        derivation
        for derivation in DERIVATIONS
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


def _ingest(columns, job_labels):
    if columns is None:
        raise InputShapeError("columns is None")
    if isinstance(columns, np.ndarray):
        if columns.ndim != 2:
            raise InputShapeError(
                "expected a 2-D array of shape (rows, cols); "
                f"got ndim={columns.ndim}"
            )
        matrix = np.asarray(columns, dtype=float)
    else:
        arrays = []
        for index, column in enumerate(columns):
            array = np.asarray(column, dtype=float)
            if array.ndim != 1:
                raise InputShapeError(
                    f"column {index} is not 1-D (ndim={array.ndim})"
                )
            arrays.append(array)
        if not arrays:
            matrix = np.empty((0, 0), dtype=float)
        else:
            rows = arrays[0].size
            if any(array.size != rows for array in arrays):
                raise InputShapeError("all columns must have the same row count")
            matrix = np.column_stack(arrays)
    if job_labels is None:
        labels = [f"Job {index + 1}" for index in range(matrix.shape[0])]
    else:
        labels = [str(label) for label in list(job_labels)]
        if len(labels) != matrix.shape[0]:
            raise InputShapeError(
                f"job_labels has {len(labels)} entries for "
                f"{matrix.shape[0]} rows"
            )
    return matrix, labels


@dataclass(frozen=True)
class PreparedTable:
    full: np.ndarray
    row_index: np.ndarray
    raw: np.ndarray
    magnitude: np.ndarray
    whole_percent: np.ndarray
    finite: np.ndarray
    active: np.ndarray
    positive: np.ndarray
    negative: np.ndarray
    percent_ratio_valid: np.ndarray
    percent_whole_valid: np.ndarray
    percent_ratio_tol: np.ndarray
    percent_whole_tol: np.ndarray
    percent_ratio_grid: tuple[Optional[float], ...]
    percent_whole_grid: tuple[Optional[float], ...]
    median_abs: np.ndarray
    duplicate_representative: np.ndarray
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
        finite = _readonly(np.isfinite(raw))
        active = _readonly(
            magnitude > cfg.money_obs_tol + cfg.cert_slack
        )
        positive = _readonly(raw > cfg.money_obs_tol)
        negative = _readonly(raw < -cfg.money_obs_tol)
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
            finite=finite,
            active=active,
            positive=positive,
            negative=negative,
            percent_ratio_valid=ratio_valid,
            percent_whole_valid=whole_valid,
            percent_ratio_tol=ratio_tol,
            percent_whole_tol=whole_tol,
            percent_ratio_grid=ratio_grids,
            percent_whole_grid=whole_grids,
            median_abs=median_abs,
            duplicate_representative=_readonly(duplicate_representative),
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
    input_ids: tuple[int, ...] = ()
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
        self.score_predictions = 0

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
            input_ids=tuple(value.id for value in inputs),
            full=full,
        )
        self.derived_cache[key] = result
        return result

    def score(
        self,
        value: NumericValue,
        derivation: Derivation,
    ) -> ScoreVectors:
        return self.score_many(((value, derivation),))[0]

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
            self.score_predictions += len(group)
            predictions = np.stack(
                [value.values for _, value in group]
            )
            predicted_tolerances = np.stack(
                [value.tolerance for _, value in group]
            )

            if kind == "money":
                observed = (
                    self.table.magnitude
                    if magnitude
                    else self.table.raw
                )
                residual = np.abs(
                    predictions[:, None, :]
                    - observed[None, :, :]
                )
                strict = (
                    predicted_tolerances[:, None, :]
                    + self.cfg.money_obs_tol
                    + self.cfg.cert_slack
                    + self.cfg.cert_money_rel
                    * np.abs(predictions[:, None, :])
                )
                loose = strict + np.maximum(
                    self.cfg.ident_abs,
                    self.cfg.ident_rel
                    * np.abs(predictions[:, None, :]),
                )
                strict_bad = np.sum(residual > strict, axis=2)
                loose_bad = np.sum(residual > loose, axis=2)
                clipped = np.sum(
                    np.minimum(residual, loose),
                    axis=2,
                )
                scales = np.ones_like(clipped)
            else:
                alternatives = []
                for (
                    scale_value,
                    observed,
                    valid,
                    observed_tolerance,
                ) in (
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
                    residual_i = np.abs(
                        predictions[:, None, :]
                        - observed[None, :, :]
                    )
                    strict_i = (
                        predicted_tolerances[:, None, :]
                        + observed_tolerance[None, :, None]
                        + 1e-9
                    )
                    loose_i = strict_i + self.cfg.pct_ident_slack
                    strict_bad_i = np.sum(
                        residual_i > strict_i,
                        axis=2,
                    )
                    loose_bad_i = np.sum(
                        residual_i > loose_i,
                        axis=2,
                    )
                    clipped_i = np.sum(
                        np.minimum(residual_i, loose_i),
                        axis=2,
                    )
                    strict_bad_i[:, ~valid] = 10**9
                    loose_bad_i[:, ~valid] = 10**9
                    clipped_i[:, ~valid] = np.inf
                    alternatives.append(
                        (
                            scale_value,
                            strict_bad_i,
                            loose_bad_i,
                            clipped_i,
                        )
                    )
                _, strict_bad, loose_bad, clipped = alternatives[0]
                _, sb2, lb2, clipped2 = alternatives[1]
                better = (
                    (sb2 < strict_bad)
                    | ((sb2 == strict_bad) & (lb2 < loose_bad))
                    | (
                        (sb2 == strict_bad)
                        & (lb2 == loose_bad)
                        & (clipped2 < clipped)
                    )
                )
                strict_bad = np.where(better, sb2, strict_bad)
                loose_bad = np.where(better, lb2, loose_bad)
                clipped = np.where(better, clipped2, clipped)
                scales = np.where(better, 100.0, 1.0)

            for index, (key, _) in enumerate(group):
                self.score_cache[key] = ScoreVectors(
                    strict_bad=_readonly(
                        np.asarray(strict_bad[index], dtype=np.int32)
                    ),
                    loose_bad=_readonly(
                        np.asarray(loose_bad[index], dtype=np.int32)
                    ),
                    residual=_readonly(
                        np.asarray(clipped[index], dtype=float)
                    ),
                    scale=_readonly(
                        np.asarray(scales[index], dtype=float)
                    ),
                )

        return [self.score_cache[key] for key in keys]


def _allowed_bad(rows: int, cfg: Config) -> int:
    return max(1, int(np.floor((1.0 - cfg.ident_frac) * rows)))


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


def _sampled(
    values: np.ndarray,
    table: PreparedTable,
) -> np.ndarray:
    return values[..., table.sample_index]


def _batch_money_matches(
    ctx: RunContext,
    predictions: np.ndarray,
    predicted_tolerance: np.ndarray,
    available: np.ndarray,
    *,
    magnitude: bool = False,
    excluded: Optional[np.ndarray] = None,
) -> list[StructuralMatch]:
    """Best all-column match for every prediction in one tensor kernel."""
    table = ctx.table
    sample = table.sample_index
    observed_source = table.magnitude if magnitude else table.raw
    observed = observed_source[available][:, sample]
    prediction = predictions[:, None, :]
    tolerance = predicted_tolerance[:, None, :]
    residual = np.abs(prediction - observed[None, :, :])
    strict = (
        tolerance
        + ctx.cfg.money_obs_tol
        + ctx.cfg.cert_slack
        + ctx.cfg.cert_money_rel * np.abs(prediction)
    )
    loose = strict + np.maximum(
        ctx.cfg.ident_abs,
        ctx.cfg.ident_rel * np.abs(prediction),
    )
    strict_bad = np.sum(residual > strict, axis=2).astype(np.int32)
    loose_bad = np.sum(residual > loose, axis=2).astype(np.int32)
    clipped = np.sum(np.minimum(residual, loose), axis=2)
    if excluded is not None:
        positions = {
            int(column): index
            for index, column in enumerate(available)
        }
        for row, column in enumerate(excluded):
            position = positions.get(int(column))
            if position is not None:
                strict_bad[row, position] = 10**8
                loose_bad[row, position] = 10**8
                clipped[row, position] = np.inf
    order = np.lexsort(
        (
            np.broadcast_to(available, clipped.shape),
            clipped,
            loose_bad,
            strict_bad,
        ),
        axis=1,
    )
    best = order[:, 0]
    return [
        StructuralMatch(
            column=int(available[column_index]),
            scale=1.0,
            strict_bad=int(strict_bad[row, column_index]),
            loose_bad=int(loose_bad[row, column_index]),
            residual=float(clipped[row, column_index]),
        )
        for row, column_index in enumerate(best)
    ]


def _batch_percent_matches(
    ctx: RunContext,
    predictions: np.ndarray,
    predicted_tolerance: np.ndarray,
    available: np.ndarray,
    *,
    excluded: Optional[np.ndarray] = None,
) -> list[StructuralMatch]:
    table = ctx.table
    sample = table.sample_index
    # Prepared typing removes obviously non-percent columns before the
    # predictions x columns x rows workspace exists.
    possible = (
        table.percent_ratio_valid[available]
        | table.percent_whole_valid[available]
    )
    available = available[possible]
    if available.size == 0:
        return [
            StructuralMatch(
                column=-1,
                scale=1.0,
                strict_bad=10**8,
                loose_bad=10**8,
                residual=np.inf,
            )
            for _ in range(predictions.shape[0])
        ]
    shapes = (predictions.shape[0], available.size)
    best_strict = np.full(shapes, 10**8, dtype=np.int32)
    best_loose = np.full(shapes, 10**8, dtype=np.int32)
    best_residual = np.full(shapes, np.inf, dtype=float)
    best_scale = np.ones(shapes, dtype=float)

    for scale, source, valid, column_tolerance in (
        (
            1.0,
            table.raw,
            table.percent_ratio_valid,
            table.percent_ratio_tol,
        ),
        (
            100.0,
            table.whole_percent,
            table.percent_whole_valid,
            table.percent_whole_tol,
        ),
    ):
        observed = source[available][:, sample]
        residual = np.abs(
            predictions[:, None, :]
            - observed[None, :, :]
        )
        strict = (
            predicted_tolerance[:, None, :]
            + column_tolerance[available][None, :, None]
            + 1e-9
        )
        loose = strict + ctx.cfg.pct_ident_slack
        strict_bad = np.sum(residual > strict, axis=2).astype(np.int32)
        loose_bad = np.sum(residual > loose, axis=2).astype(np.int32)
        clipped = np.sum(np.minimum(residual, loose), axis=2)
        valid_columns = valid[available][None, :]
        strict_bad = np.where(valid_columns, strict_bad, 10**8)
        loose_bad = np.where(valid_columns, loose_bad, 10**8)
        clipped = np.where(valid_columns, clipped, np.inf)
        better = (
            (strict_bad < best_strict)
            | (
                (strict_bad == best_strict)
                & (loose_bad < best_loose)
            )
            | (
                (strict_bad == best_strict)
                & (loose_bad == best_loose)
                & (clipped < best_residual)
            )
        )
        best_strict = np.where(better, strict_bad, best_strict)
        best_loose = np.where(better, loose_bad, best_loose)
        best_residual = np.where(better, clipped, best_residual)
        best_scale = np.where(better, scale, best_scale)

    if excluded is not None:
        positions = {
            int(column): index
            for index, column in enumerate(available)
        }
        for row, column in enumerate(excluded):
            position = positions.get(int(column))
            if position is not None:
                best_strict[row, position] = 10**8
                best_loose[row, position] = 10**8
                best_residual[row, position] = np.inf

    order = np.lexsort(
        (
            np.broadcast_to(available, best_residual.shape),
            best_residual,
            best_loose,
            best_strict,
        ),
        axis=1,
    )
    best = order[:, 0]
    return [
        StructuralMatch(
            column=int(available[column_index]),
            scale=float(best_scale[row, column_index]),
            strict_bad=int(best_strict[row, column_index]),
            loose_bad=int(best_loose[row, column_index]),
            residual=float(best_residual[row, column_index]),
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


def _estimate_fragments(
    ctx: RunContext,
    hub_candidates: Optional[Iterable[int]] = None,
) -> list[Fragment]:
    table = ctx.table
    cfg = ctx.cfg
    reps = table.representatives
    sample = table.sample_index
    raw = table.raw[:, sample]
    positive_rate = np.mean(table.positive[:, sample], axis=1)
    if hub_candidates is None:
        hubs = sorted(
            (
                int(column)
                for column in reps
                if positive_rate[column] >= cfg.prior_robust_frac
            ),
            key=lambda column: (-table.median_abs[column], column),
        )[: cfg.motif_hubs]
    else:
        hubs = [
            int(column)
            for column in hub_candidates
            if positive_rate[int(column)] >= cfg.prior_robust_frac
        ]
    allowed = _allowed_bad(sample.size, cfg)
    candidates: dict[tuple[Assignment, ...], Fragment] = {}
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
                    margin_match = _batch_percent_matches(
                        ctx,
                        margin[None, :],
                        margin_tol,
                        available,
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
                prior = candidates.get(canonical)
                if prior is None or (
                    fragment.score,
                    -fragment.residual,
                ) > (prior.score, -prior.residual):
                    candidates[canonical] = fragment

    return sorted(
        candidates.values(),
        key=lambda fragment: (
            -fragment.score,
            fragment.residual,
            fragment.assignments,
        ),
    )[: cfg.estimate_fragments]


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
    results: dict[tuple[Assignment, ...], Fragment] = {}

    for estimate in estimates:
        mapping = estimate.mapping
        v_column = mapping["V"].column
        c_column = mapping["C"].column
        used = np.asarray(
            [assignment.column for assignment in estimate.assignments],
            dtype=int,
        )
        candidates = table.representatives[
            ~np.isin(table.representatives, used)
        ]
        if candidates.size == 0:
            continue
        V = table.raw[v_column, sample]
        C = table.raw[c_column, sample]
        D_all = table.raw[candidates][:, sample]
        with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
            progress = D_all / C[None, :]
        plausible = (
            np.mean(
                (progress >= -0.02)
                & (progress <= cfg.d_over_c_slack),
                axis=1,
            )
            >= cfg.prior_robust_frac
        )
        live = (
            np.median(np.abs(progress), axis=1)
            >= cfg.anchor_live_med
        )
        positive = (
            np.mean(table.positive[candidates][:, sample], axis=1)
            >= cfg.prior_robust_frac
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
        available = table.representatives[
            ~np.isin(table.representatives, used)
        ]
        matches_by_variable = {
            "Q": _batch_money_matches(
                ctx, Q, Q_tol, available, excluded=candidates
            ),
            "E": _batch_money_matches(
                ctx, E, E_tol, available, excluded=candidates
            ),
            "H": _batch_money_matches(
                ctx, H, H_tol, available, excluded=candidates
            ),
            "P": _batch_percent_matches(
                ctx, P, P_tol, available, excluded=candidates
            ),
        }
        ranked = []
        for index, d_column in enumerate(candidates):
            raw_matches = {
                variable: matches[index]
                for variable, matches in matches_by_variable.items()
            }
            selected = _unique_output_matches(raw_matches, rows, cfg)
            # D must be oriented by earned/progress structure.  Q alone is
            # only an additive complement and cannot name the axis.
            identifying = {
                variable
                for variable in selected
                if variable in {"E", "P", "H"}
            }
            if not identifying:
                continue
            assignments = list(estimate.assignments)
            assignments.append(Assignment("D", int(d_column)))
            score = estimate.score + 5
            residual = estimate.residual
            weights = {"E": 6, "P": 5, "H": 3, "Q": 2}
            for variable, match in selected.items():
                assignments.append(
                    Assignment(variable, match.column, match.scale)
                )
                score += weights[variable]
                if match.strict_bad == 0:
                    score += 1
                residual += match.residual
            canonical = _canonical_assignments(assignments)
            if canonical is None:
                continue
            ranked.append(
                Fragment(
                    assignments=canonical,
                    score=score,
                    residual=residual,
                    sources=estimate.sources
                    | frozenset({"progress_projection"}),
                )
            )
        ranked.sort(
            key=lambda fragment: (
                -fragment.score,
                fragment.residual,
                fragment.assignments,
            )
        )
        for fragment in ranked[: cfg.progress_per_estimate]:
            prior = results.get(fragment.assignments)
            if prior is None or (
                fragment.score,
                -fragment.residual,
            ) > (prior.score, -prior.residual):
                results[fragment.assignments] = fragment

    return sorted(
        results.values(),
        key=lambda fragment: (
            -fragment.score,
            fragment.residual,
            fragment.assignments,
        ),
    )


def _billing_fragments(
    ctx: RunContext,
    progress_fragments: list[Fragment],
) -> list[Fragment]:
    table = ctx.table
    cfg = ctx.cfg
    sample = table.sample_index
    rows = sample.size
    results: dict[tuple[Assignment, ...], Fragment] = {}

    for progress_fragment in progress_fragments:
        mapping = progress_fragment.mapping
        v_column = mapping["V"].column
        c_column = mapping["C"].column
        d_column = mapping["D"].column
        used = np.asarray(
            [
                assignment.column
                for assignment in progress_fragment.assignments
            ],
            dtype=int,
        )
        candidates = table.representatives[
            ~np.isin(table.representatives, used)
        ]
        if candidates.size == 0:
            continue
        V = table.raw[v_column, sample]
        C = table.raw[c_column, sample]
        D = table.raw[d_column, sample]
        B_all = table.raw[candidates][:, sample]
        with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
            E = V * D / C
            billed = B_all / V[None, :]
        plausible = (
            np.mean(
                (billed >= -0.05)
                & (billed <= cfg.b_over_v_slack),
                axis=1,
            )
            >= cfg.prior_robust_frac
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
        available = table.representatives[
            ~np.isin(table.representatives, used)
        ]
        matches_by_variable = {
            "N": _batch_money_matches(
                ctx, N, net_tol, available, excluded=candidates
            ),
            "U": _batch_money_matches(
                ctx,
                U,
                net_tol,
                available,
                magnitude=True,
                excluded=candidates,
            ),
            "O": _batch_money_matches(
                ctx,
                O,
                net_tol,
                available,
                magnitude=True,
                excluded=candidates,
            ),
            "RB": _batch_money_matches(
                ctx, RB, rb_tol, available, excluded=candidates
            ),
            "PB": _batch_percent_matches(
                ctx, PB, pb_tol, available, excluded=candidates
            ),
        }
        ranked = []
        for index, b_column in enumerate(candidates):
            raw_matches = {
                variable: matches[index]
                for variable, matches in matches_by_variable.items()
            }
            selected = _unique_output_matches(raw_matches, rows, cfg)
            identifying = {
                variable
                for variable in selected
                if variable in {"N", "U", "O", "PB"}
            }
            if not identifying:
                continue
            assignments = list(progress_fragment.assignments)
            assignments.append(Assignment("B", int(b_column)))
            score = progress_fragment.score + 5
            residual = progress_fragment.residual
            weights = {"N": 5, "U": 4, "O": 4, "PB": 5, "RB": 2}
            for variable, match in selected.items():
                assignments.append(
                    Assignment(variable, match.column, match.scale)
                )
                score += weights[variable]
                if match.strict_bad == 0:
                    score += 1
                residual += match.residual
            if "U" in selected and "O" in selected:
                u_column = selected["U"].column
                o_column = selected["O"].column
                if table.active_overlap[u_column, o_column] == 0:
                    score += 4
            canonical = _canonical_assignments(assignments)
            if canonical is None:
                continue
            ranked.append(
                Fragment(
                    assignments=canonical,
                    score=score,
                    residual=residual,
                    sources=progress_fragment.sources
                    | frozenset({"billing_bridge"}),
                )
            )
        ranked.sort(
            key=lambda fragment: (
                -fragment.score,
                fragment.residual,
                fragment.assignments,
            )
        )
        for fragment in ranked[: cfg.billing_per_progress]:
            prior = results.get(fragment.assignments)
            if prior is None or (
                fragment.score,
                -fragment.residual,
            ) > (prior.score, -prior.residual):
                results[fragment.assignments] = fragment

    return sorted(
        results.values(),
        key=lambda fragment: (
            -fragment.score,
            fragment.residual,
            fragment.assignments,
        ),
    )[: cfg.assembled_states]


def _discover_states(
    ctx: RunContext,
    *,
    broaden: bool = False,
    exhaustive: bool = False,
) -> tuple[list[Fragment], dict]:
    estimates = _estimate_fragments(ctx)
    fallback_used = False
    if broaden:
        fallback_estimates = _estimate_fragments(
            ctx,
            _additive_hub_fallback(ctx),
        )
        estimates_by_key = {
            fragment.assignments: fragment
            for fragment in (*estimates, *fallback_estimates)
        }
        estimates = sorted(
            estimates_by_key.values(),
            key=lambda fragment: (
                -fragment.score,
                fragment.residual,
                fragment.assignments,
            ),
        )[: ctx.cfg.estimate_fragments]
        fallback_used = bool(fallback_estimates)
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
    derived_values: int
    physical_matches: int
    active_derivations: frozenset[str] = frozenset()
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
    out: str
    column: int
    scale: float
    derivation: Derivation
    value: NumericValue
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
                out=out,
                column=column,
                scale=float(scores.scale[column]),
                derivation=derivation,
                value=value,
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
        prior = best_by_out.get(claim.out)
        if prior is None or claim.rank < prior.rank:
            best_by_out[claim.out] = claim
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
            derived_values=0,
            physical_matches=0,
        )

    queue = deque(known)
    evaluated: set[tuple] = set()
    pending: dict[
        str,
        dict[int, tuple[Derivation, NumericValue]],
    ] = {}
    conflicts: list[str] = []
    derived_start = ctx.derived_misses
    physical_matches = 0

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
                    claim.out in known
                    or claim.column in column_to_var
                ):
                    continue
                observed = ctx.observed(
                    claim.out,
                    claim.column,
                    scale=claim.scale,
                )
                known[claim.out] = observed
                column_to_var[claim.column] = claim.out
                pending.pop(claim.out, None)
                queue.append(claim.out)
                physical_matches += 1
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
        derived_values=ctx.derived_misses - derived_start,
        physical_matches=physical_matches,
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
    closure_cache: dict[int, int] = {}

    def closure(seed: int) -> int:
        cached = closure_cache.get(seed)
        if cached is not None:
            return cached
        known = seed
        changed = True
        while changed:
            changed = False
            for required, output in transitions:
                if known & required == required and not known & output:
                    known |= output
                    changed = True
        closure_cache[seed] = known
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


def _analyse_finalist(
    ctx: RunContext,
    graph: ClosedGraph,
) -> ClosedGraph:
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

    # Verify each identity once, not every algebraic rearrangement.  Once the
    # identity holds, its ready derivations are computational directions over
    # the same fact; the subsequent independence closure is Boolean
    # provenance only.  Clipped U/O presentation has two forward claims and
    # therefore explicitly verifies both outputs.
    active = []
    for identity in IDENTITIES:
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
        if identity.verification_outputs:
            checks = [
                next(
                    (
                        derivation
                        for derivation in ready
                        if derivation.out == output
                    ),
                    None,
                )
                for output in identity.verification_outputs
                if output in graph.known
            ]
            checks = [
                derivation
                for derivation in checks
                if derivation is not None
            ]
        else:
            checks = ready[:1]
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
                    ctx.cfg,
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
    graph.active_derivations = frozenset(
        derivation.id for derivation in active
    )
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
) -> list[ClosedGraph]:
    analysed = [
        _analyse_finalist(ctx, graph)
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


def _reconstruct_target(
    ctx: RunContext,
    graph: ClosedGraph,
    physical: dict[str, NumericValue],
    target: str,
) -> tuple[Optional[NumericValue], Optional[str]]:
    known = {
        variable: value
        for variable, value in physical.items()
        if variable != target
    }

    def merge_numeric(out, alternatives):
        del out
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
            {candidate.id for candidate, _ in alternatives}
        )

    known, proofs = _propagate_virtuals(
        known,
        graph.active_derivations,
        ctx.derive,
        lambda value: value.id,
        merge_numeric,
    )
    value = known.get(target)
    if value is None:
        return None, None
    basis = proofs.get(target, [])
    return value, (
        basis[0]
        if len(basis) == 1
        else value.derivation
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
    direct: dict[str, list[tuple[Derivation, NumericValue]]] = {}
    for derivation_id in graph.active_derivations:
        derivation = DERIVATION_BY_ID[derivation_id]
        if (
            derivation.out not in physical
            or not all(
                variable in physical
                for variable in derivation.inputs
            )
        ):
            continue
        predicted = ctx.derive(derivation, physical)
        if predicted is not None:
            direct.setdefault(derivation.out, []).append(
                (derivation, predicted)
            )

    for variable in sorted(graph.checkable, key=VAR_INDEX.__getitem__):
        observed = physical[variable]
        direct_options = direct.get(variable, [])
        if direct_options:
            derivation, expected = min(
                direct_options,
                key=lambda item: (
                    item[1].support.bit_count(),
                    item[0].id,
                ),
            )
            proof = derivation.id
        else:
            expected, proof = _reconstruct_target(
                ctx,
                graph,
                physical,
                variable,
            )
        if expected is None:
            continue
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
        relation = proof or f"{variable}_from_grounded_graph"
        identity_id = (
            DERIVATION_BY_ID[proof].identity_id
            if proof in DERIVATION_BY_ID
            else "grounded_graph"
        )
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
                family=identity_id,
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
        graph.active_derivations,
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
    for derivation_id in graph.active_derivations:
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


def _diagnose(
    graph: ClosedGraph,
    physical: dict[str, NumericValue],
    failures: list[RowFailure],
    labels: list[str],
    cfg: Config,
) -> list[Finding]:
    by_row: dict[int, list[RowFailure]] = {}
    for failure in failures:
        by_row.setdefault(failure.row_index, []).append(failure)
    findings = []
    for row, row_failures in sorted(by_row.items()):
        status, detail = _minimal_row_repair(
            graph,
            physical,
            row,
            cfg,
        )
        if status == "resolved":
            joint = len(detail) > 1
            for correction in detail:
                variable = correction["variable"]
                findings.append(
                    Finding(
                        row_index=row,
                        row_label=labels[row],
                        culprit_column=correction["column"],
                        culprit_variable=variable,
                        candidate_variables=[variable],
                        exonerated_variables=[],
                        observed=correction["observed"],
                        proposed_correction=correction["proposed"],
                        correction_basis=correction["basis"],
                        confidence="high",
                        classification="internally inconsistent value",
                        classification_detail=(
                            "Removing this observation lets the remaining "
                            "constraint graph reconstruct a unique coherent "
                            "replacement."
                        ),
                        transplant_sources=[],
                        failing_relations=sorted(
                            {
                                failure.relation
                                for failure in row_failures
                            }
                        ),
                        proof_kind="joint" if joint else "inherited",
                    )
                )
        elif status == "ambiguous":
            findings.append(
                Finding(
                    row_index=row,
                    row_label=labels[row],
                    culprit_column=None,
                    culprit_variable=None,
                    candidate_variables=detail,
                    exonerated_variables=[],
                    observed=None,
                    proposed_correction=None,
                    correction_basis=[],
                    confidence="low",
                    classification="ambiguous_multi_cell",
                    classification_detail=(
                        "Several minimal observation removals can make this "
                        "row internally coherent."
                    ),
                    transplant_sources=[],
                    failing_relations=sorted(
                        {
                            failure.relation
                            for failure in row_failures
                        }
                    ),
                    proof_kind="joint",
                )
            )
        else:
            candidates = sorted(
                {failure.variable for failure in row_failures},
                key=VAR_INDEX.__getitem__,
            )
            findings.append(
                Finding(
                    row_index=row,
                    row_label=labels[row],
                    culprit_column=None,
                    culprit_variable=None,
                    candidate_variables=candidates,
                    exonerated_variables=[],
                    observed=None,
                    proposed_correction=None,
                    correction_basis=[],
                    confidence="low",
                    classification="unresolved_constraint_conflict",
                    classification_detail=(
                        "No unique one- or two-observation repair makes the "
                        "complete row coherent."
                    ),
                    transplant_sources=[],
                    failing_relations=sorted(
                        {
                            failure.relation
                            for failure in row_failures
                        }
                    ),
                    proof_kind="joint",
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


def validate_wip(
    columns,
    job_labels=None,
    config=None,
) -> ValidationResult:
    cfg = _config(config)
    matrix, labels = _ingest(columns, job_labels)
    diagnostics = {
        "engine": "wip2_search_compressed_constraint_graph",
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
                f"only {int(complete.sum())} usable row(s) of "
                f"{matrix.shape[0]}; need at least {cfg.min_rows}"
            ),
            diagnostics=diagnostics,
        )
    if np.any(~complete):
        diagnostics["notes"].append(
            f"{int((~complete).sum())} row(s) excluded from identification; "
            "retained for strict full-document certification"
        )

    table = PreparedTable.build(matrix, cfg)
    ctx = RunContext(table, cfg)
    fragments, discovery = _discover_states(ctx)
    initial_discovery = discovery
    closed = _close_unique_states(ctx, fragments) if fragments else []
    finalists = _analyse_finalists(ctx, closed) if closed else []

    # Magnitude is an excellent ordering prior, never a correctness gate.
    # Widen only after the compressed frontier fails its independent-region
    # test, then run every plausible additive hub through the same batched
    # motif kernel and deduplicate before closing any state.
    widened = not finalists or not _coverage_ok(finalists[0], cfg)
    if widened:
        broad_fragments, broad_discovery = _discover_states(
            ctx,
            broaden=True,
        )
        broad_closed = (
            _close_unique_states(ctx, broad_fragments)
            if broad_fragments
            else []
        )
        broad_finalists = (
            _analyse_finalists(ctx, broad_closed)
            if broad_closed
            else []
        )
        discovery = {
            **broad_discovery,
            "initial_estimate_fragments": initial_discovery[
                "estimate_fragments"
            ],
            "initial_assembled_states": initial_discovery[
                "canonical_assembled_states"
            ],
        }
        fragments = broad_fragments
        closed = broad_closed
        finalists = broad_finalists

    exhaustive_widening = (
        widened
        and (
            not finalists
            or not _coverage_ok(finalists[0], cfg)
        )
    )
    if exhaustive_widening:
        exhaustive_fragments, exhaustive_discovery = _discover_states(
            ctx,
            broaden=True,
            exhaustive=True,
        )
        exhaustive_closed = (
            _close_unique_states(ctx, exhaustive_fragments)
            if exhaustive_fragments
            else []
        )
        exhaustive_finalists = (
            _analyse_finalists(ctx, exhaustive_closed)
            if exhaustive_closed
            else []
        )
        discovery = {
            **exhaustive_discovery,
            "initial_estimate_fragments": initial_discovery[
                "estimate_fragments"
            ],
            "initial_assembled_states": initial_discovery[
                "canonical_assembled_states"
            ],
        }
        fragments = exhaustive_fragments
        closed = exhaustive_closed
        finalists = exhaustive_finalists

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
        return ValidationResult(
            status=INSUFFICIENT,
            reason=(
                "could not assemble a coherent estimate/progress/billing "
                "constraint graph from the observed columns"
            ),
            diagnostics=diagnostics,
        )
    if not closed:
        return ValidationResult(
            status=INSUFFICIENT,
            reason="structural fragments did not close to a semantic graph",
            diagnostics=diagnostics,
        )
    if not finalists:
        return ValidationResult(
            status=INSUFFICIENT,
            reason=(
                "candidate graphs did not close to the required semantic core"
            ),
            diagnostics=diagnostics,
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
        return ValidationResult(
            status=INSUFFICIENT,
            reason=(
                "identifiable but not independently validatable across "
                f"the required business regions: {', '.join(missing)}"
            ),
            diagnostics=diagnostics,
        )

    witnesses, failures, incomplete, physical = _certify(
        ctx,
        best,
        labels,
    )
    findings = (
        _diagnose(
            best,
            physical,
            failures,
            labels,
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
        "predictions_scored": ctx.score_predictions,
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
        if value.column is None
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
