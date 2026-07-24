"""Cheap accounting evidence for choosing among plausible table layouts.

Reconstruction has already used grid shape to eliminate impossible joins before
this module runs.  These functions do not emit user findings, apply corrections,
run analysis, or permanently classify a table.  They ask the existing WIP and
CC validators how coherently each candidate grid behaves.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .cc_validator import validate_cc
from .parsing import parse_table
from .wip_validator import ValidationResult, validate_wip


@dataclass(frozen=True)
class SchemaEvidence:
    schema: str
    rank: int
    numeric_columns: int
    explained_columns: int
    witness_families: int
    witnessed_row_weight: float
    failures: int
    findings: int
    status: str

    @property
    def coverage(self) -> float:
        return self.explained_columns / max(self.numeric_columns, 1)

    @property
    def key(self) -> tuple:
        """Lexicographic evidence only; no subjective continuation features."""
        return (
            self.rank,
            round(self.coverage, 9),
            self.witness_families,
            round(self.witnessed_row_weight, 6),
            -self.failures,
            -self.findings,
        )


@dataclass(frozen=True)
class TableEvidence:
    shape: tuple[int, int]
    parseable: bool
    best: SchemaEvidence | None
    wip: SchemaEvidence | None
    cc: SchemaEvidence | None


@dataclass(frozen=True)
class LayoutEvidence:
    tables: tuple[TableEvidence, ...]

    @property
    def key(self) -> tuple:
        """Whole-layout accounting coherence.

        Scores are aggregated over all selected logical tables.  Table count is
        deliberately absent: math-equivalent "one long table" versus "two
        separate tables" must remain ambiguous instead of being resolved by an
        arbitrary preference for more or fewer tables.
        """
        fits = [table.best for table in self.tables if table.best is not None]
        numeric = sum(fit.numeric_columns for fit in fits)
        explained = sum(fit.explained_columns for fit in fits)
        coverage = explained / max(numeric, 1)
        return (
            sum(fit.rank for fit in fits),
            round(coverage, 9),
            sum(fit.witness_families for fit in fits),
            round(sum(fit.witnessed_row_weight for fit in fits), 6),
            -sum(fit.failures for fit in fits),
            -sum(fit.findings for fit in fits),
            -sum(1 for table in self.tables if not table.parseable),
        )


def _rank(result: ValidationResult) -> int:
    # Mirrors the existing schema race: witnessed mapping > mapping > nothing.
    if result.mapping and result.witnesses:
        return 2
    if result.mapping:
        return 1
    return 0


def _evidence(schema: str, result: ValidationResult,
              numeric_columns: int) -> SchemaEvidence:
    families = {getattr(witness, "family", None) or witness.relation
                for witness in result.witnesses}
    row_weight = sum(float(witness.weight) * int(witness.n_rows)
                     for witness in result.witnesses)
    return SchemaEvidence(
        schema=schema,
        rank=_rank(result),
        numeric_columns=numeric_columns,
        explained_columns=len(result.mapping),
        witness_families=len(families),
        witnessed_row_weight=row_weight,
        failures=len(result.failures),
        findings=len(result.findings),
        status=result.status,
    )


def evaluate_table(table: dict) -> TableEvidence:
    rows = table.get("rows") or []
    headers = table.get("headers") or []
    shape = (len(rows), max([len(headers), *(len(row) for row in rows)],
                            default=0))
    parsed = parse_table(rows, headers=headers)
    matrix = parsed.matrix
    if matrix is None or getattr(matrix, "size", 0) == 0:
        return TableEvidence(shape=shape, parseable=False,
                             best=None, wip=None, cc=None)

    labels = parsed.job_labels
    wip_result = validate_wip(matrix, job_labels=labels)
    cc_result = validate_cc(matrix, job_labels=labels)
    numeric_columns = int(matrix.shape[1])
    wip = _evidence("wip", wip_result, numeric_columns)
    cc = _evidence("cc", cc_result, numeric_columns)
    best = wip if wip.key >= cc.key else cc
    return TableEvidence(shape=shape, parseable=True,
                         best=best, wip=wip, cc=cc)


def evaluate_layout(layout: Iterable[dict]) -> LayoutEvidence:
    return LayoutEvidence(tuple(evaluate_table(table) for table in layout))


def rank_layouts(layouts: list[list[dict]]) -> list[tuple[list[dict], LayoutEvidence]]:
    """Return strongest-first candidates without forcing an ambiguous winner."""
    ranked = [(layout, evaluate_layout(layout)) for layout in layouts]
    ranked.sort(key=lambda item: item[1].key, reverse=True)
    return ranked


def select_layout(layouts: list[list[dict]]) -> dict:
    """Select only when validator evidence has one unique best layout.

    Exact accounting ties remain explicit.  A later policy may use additional
    deterministic document evidence, but this layer will not invent a geometric
    or semantic tiebreaker.
    """
    ranked = rank_layouts(layouts)
    if not ranked:
        return {"status": "no_tables", "layout": None, "candidates": []}

    best_key = ranked[0][1].key
    tied = [item for item in ranked if item[1].key == best_key]
    summary = [
        {
            "key": list(evidence.key),
            "shapes": [list(table.shape) for table in evidence.tables],
            "schemas": [table.best.schema if table.best else None
                        for table in evidence.tables],
        }
        for _, evidence in ranked
    ]
    if len(tied) != 1:
        return {"status": "ambiguous", "layout": None,
                "candidates": summary}
    return {"status": "selected", "layout": ranked[0][0],
            "evidence": ranked[0][1], "candidates": summary}
