from __future__ import annotations

import numpy as np

from tests.synth import rows_numeric
from tests.test_billing_motif_fast_path import make_wip
from tests.test_wip_motif_planner import make_rich_wip
from wipple.accounting import wip, wip2


def labels(matrix):
    return [f"J-{i}" for i in range(matrix.shape[0])]


def test_preparation_is_single_pass_immutable_and_collapses_duplicates():
    matrix = make_rich_wip(n=12, decoys=0, percent_mode="whole")
    matrix = np.column_stack([matrix, matrix[:, 0]])
    table = wip2.PreparedTable.build(matrix, wip2.Config())

    assert table.raw.flags.writeable is False
    assert table.magnitude.flags.writeable is False
    assert table.whole_percent.flags.writeable is False
    assert table.duplicate_representative[-1] == 0
    assert len(table.representatives) == matrix.shape[1] - 1
    assert np.allclose(table.whole_percent[10], matrix[:, 10] / 100.0)


def test_rich_graph_matches_current_public_result_and_records_redundancy():
    matrix = make_wip(n=24)
    current = wip.validate_wip(matrix, labels(matrix))
    rebuilt = wip2.validate_wip(matrix, labels(matrix))

    assert rebuilt.status == current.status == wip.SUCCESS
    assert rebuilt.mapping == current.mapping
    evidence = rebuilt.diagnostics["evidence"]
    assert evidence["grounded_graph_redundancy"] > 0
    assert evidence["minimum_generating_seeds"] < evidence["physical_observations"]
    assert all(
        evidence["business_region_coverage"][region] > 0
        for region in ("estimate", "progress", "billing")
    )
    matcher = rebuilt.diagnostics["matcher"]
    assert matcher["predictions_scored"] > matcher["batched_calls"]


def test_header_blind_mapping_survives_arbitrary_column_order():
    matrix = make_wip(n=24)
    order = np.asarray([9, 4, 12, 2, 7, 14, 0, 11, 5, 1, 13, 8, 6, 10, 3])
    shuffled = matrix[:, order]
    expected = {
        new_col: variable
        for new_col, variable in enumerate(
            ["O", "Q", "RB", "G", "B", "M", "V", "R",
             "P", "C", "PB", "U", "E", "H", "D"]
        )
    }

    result = wip2.validate_wip(shuffled, labels(shuffled))

    assert result.status == wip.SUCCESS
    assert result.mapping == expected


def test_quantized_percent_is_numeric_evidence_not_bit_identity():
    for mode in ("whole", "tenth"):
        matrix = make_rich_wip(n=36, decoys=8, percent_mode=mode)
        result = wip2.validate_wip(matrix, labels(matrix))

        assert result.status == wip.SUCCESS
        assert result.mapping[10] == "P"
        assert "P" in result.diagnostics["evidence"]["checkable_columns"]


def test_one_corrupted_cell_keeps_mapping_and_fails_strict_certification():
    matrix = make_rich_wip(n=36, decoys=8)
    matrix[7, 3] += 37_000.0
    result = wip2.validate_wip(matrix, labels(matrix))

    assert result.status == wip.FAILED
    assert result.mapping[3] == "E"
    assert any(
        failure.row_index == 7
        and failure.variable == "E"
        and failure.difference == 37_000.0
        for failure in result.failures
    )
    assert any(finding.row_index == 7 for finding in result.findings)


def test_sparse_tree_is_not_mistaken_for_independent_validation():
    matrix = np.asarray(
        [[row[1], row[2], row[4], row[8]] for row in rows_numeric()],
        dtype=float,
    )
    current = wip.validate_wip(matrix, labels(matrix))
    rebuilt = wip2.validate_wip(matrix, labels(matrix))

    assert current.status == rebuilt.status == wip.INSUFFICIENT
    assert rebuilt.mapping == {}


def test_constraint_registry_separates_identity_from_derivation():
    estimate = next(
        identity
        for identity in wip2.IDENTITIES
        if identity.id == "estimate_complement"
    )

    assert estimate.variables == ("V", "C", "G")
    assert {derivation.out for derivation in estimate.derivations} == {
        "V",
        "C",
        "G",
    }
    assert {
        derivation.identity_id for derivation in estimate.derivations
    } == {"estimate_complement"}
