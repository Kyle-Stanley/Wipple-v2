from __future__ import annotations

import numpy as np

from tests.test_billing_motif_fast_path import make_wip
from tests.test_wip_motif_planner import make_rich_wip
from wipple.accounting import validation, wip, wip2
from wipple.accounting.wip_pipeline import validate_wip as pipeline_validate_wip
from wipple.pipeline import graph as pipeline_graph  # noqa: F401


def labels(matrix):
    return [f"J-{row}" for row in range(matrix.shape[0])]


def _shifted_mapping(mapping: dict[int, str], inserted_at: int) -> dict[int, str]:
    return {
        column + (column >= inserted_at): variable
        for column, variable in mapping.items()
    }


def test_production_graph_selects_wip2_adapter():
    assert validation.validate_wip is pipeline_validate_wip

    matrix = make_wip(n=24)
    result = validation.validate_wip(matrix, labels(matrix))

    assert result.status == wip.SUCCESS
    assert result.diagnostics["pipeline_validator"] == "wip2"
    assert "evidence" in result.diagnostics


def test_all_zero_column_is_ignored_and_original_indices_are_restored():
    matrix = make_wip(n=24)
    baseline = wip2.validate_wip(matrix, labels(matrix))
    inserted_at = 5
    expanded = np.insert(matrix, inserted_at, 0.0, axis=1)

    result = pipeline_validate_wip(expanded, labels(expanded))

    assert result.status == baseline.status == wip.SUCCESS
    assert result.mapping == _shifted_mapping(baseline.mapping, inserted_at)
    assert inserted_at not in result.mapping
    assert result.diagnostics["ignored_all_zero_columns"] == [inserted_at]
    assert result.diagnostics["pipeline_validator"] == "wip2"


def test_zero_column_guard_remaps_failure_and_finding_columns():
    matrix = make_rich_wip(n=28, decoys=0)
    matrix[7, 4] = matrix[7, 4] / 10.0
    baseline = wip2.validate_wip(matrix, labels(matrix))
    inserted_at = 2
    expanded = np.insert(matrix, inserted_at, -0.0, axis=1)

    result = pipeline_validate_wip(expanded, labels(expanded))

    assert result.status == baseline.status == wip.FAILED
    assert result.mapping == _shifted_mapping(baseline.mapping, inserted_at)
    assert [failure.column for failure in result.failures] == [
        None if failure.column is None else failure.column + (
            failure.column >= inserted_at
        )
        for failure in baseline.failures
    ]
    assert [finding.culprit_column for finding in result.findings] == [
        None if finding.culprit_column is None else finding.culprit_column + (
            finding.culprit_column >= inserted_at
        )
        for finding in baseline.findings
    ]
