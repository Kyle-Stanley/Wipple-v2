from __future__ import annotations

import numpy as np
import pytest

from tests.synth import rows_numeric
from tests.test_billing_motif_fast_path import make_wip
from tests.test_pipeline import shadow_audit_regression_table
from tests.test_wip_motif_planner import make_rich_wip
from wipple.accounting import wip, wip2
from wipple.accounting.parsing import parse_table


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
    ctx = wip2.RunContext(table, wip2.Config())
    known = {
        "V": ctx.observed("V", 0),
        "D": ctx.observed("D", 4),
        "C": ctx.observed("C", 1),
    }
    derivation = wip2.DERIVATION_BY_ID["E_from_V_D_C"]
    predicted = ctx.derive(derivation, known)
    assert predicted is ctx.derive(derivation, known)
    assert ctx.score(predicted, derivation) is ctx.score(predicted, derivation)
    assert ctx.derived_hits == 1
    assert ctx.score_hits == 1
    q_derivation = wip2.DERIVATION_BY_ID["Q_from_C_D"]
    q_value = ctx.derive(q_derivation, known)
    first = ctx.score_many(
        ((predicted, derivation), (q_value, q_derivation))
    )
    second = ctx.score_many(
        ((predicted, derivation), (q_value, q_derivation))
    )
    assert first[0] is second[0]
    assert first[1] is second[1]
    assert ctx.score_hits >= 4


def test_rich_graph_matches_current_public_result_and_records_redundancy():
    matrix = make_wip(n=24)
    current = wip.validate_wip(matrix, labels(matrix))
    rebuilt = wip2.validate_wip(matrix, labels(matrix))

    assert rebuilt.status == current.status == wip.SUCCESS
    assert rebuilt.mapping == current.mapping
    evidence = rebuilt.diagnostics["evidence"]
    assert evidence["grounded_graph_redundancy"] > 0
    assert evidence["minimum_generating_seeds"] < evidence["physical_observations"]
    assert evidence["minimum_generating_seeds"] == 4
    assert evidence["grounded_graph_redundancy"] == (
        evidence["physical_observations"] - 4
    )
    assert all(
        evidence["business_region_coverage"][region] > 0
        for region in ("estimate", "progress", "billing")
    )
    cache = rebuilt.diagnostics["cache"]
    assert cache["derived_hits"] > 0


def test_complete_common_motif_skips_redundant_virtual_closure():
    matrix = make_rich_wip(n=48, decoys=0)
    cfg = wip2.Config()
    table = wip2.PreparedTable.build(matrix, cfg)
    ctx = wip2.RunContext(table, cfg)
    fragments, _ = wip2._discover_states(ctx)

    closed = wip2._close_unique_states(ctx, fragments)

    assert len(closed) == 1
    assert len(closed[0].column_to_var) == len(table.representatives)
    assert closed[0].derived_values == 0


def test_public_result_completes_virtual_frontier_only_for_finalist():
    rows = []
    job_labels = []
    for name, V, C, G, D, Q, P, E, _B, _U, _O in rows_numeric():
        B = E + 10_000
        job_labels.append(name)
        rows.append([V, C, G, D, Q, P, E, B, B - E])

    result = wip2.validate_wip(
        np.asarray(rows, dtype=float),
        job_labels,
    )

    assert result.status == wip.SUCCESS
    assert result.mapping[5] == "P"
    assert result.mapping[8] == "O"
    assert "U" in result.virtuals
    assert "N" in result.virtuals


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


def test_wide_decoys_use_batched_additive_hub_frontier():
    matrix = make_rich_wip(n=48, decoys=70)

    result = wip2.validate_wip(matrix, labels(matrix))

    assert result.status == wip.SUCCESS
    assert result.mapping == {
        0: "V",
        1: "C",
        2: "G",
        3: "E",
        4: "D",
        5: "H",
        6: "B",
        7: "Q",
        8: "U",
        9: "O",
    }
    discovery = result.diagnostics["discovery"]
    assert discovery["progressive_widening"] is False
    assert discovery["additive_hub_fallback"] is True


def test_wide_additive_hubs_enter_the_first_simultaneous_frontier():
    matrix = make_rich_wip(n=160, decoys=88)

    result = wip2.validate_wip(matrix, labels(matrix))

    assert result.status == wip.SUCCESS
    assert result.mapping == {
        0: "V",
        1: "C",
        2: "G",
        3: "E",
        4: "D",
        5: "H",
        6: "B",
        7: "Q",
        8: "U",
        9: "O",
    }
    discovery = result.diagnostics["discovery"]
    assert discovery["additive_hub_fallback"] is True
    assert discovery["progressive_widening"] is False


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


def test_one_bad_row_in_five_cannot_eject_the_physical_column():
    matrix = make_rich_wip(n=5, decoys=0)
    matrix[2, 4] *= 0.1

    result = wip2.validate_wip(matrix, labels(matrix))

    assert result.status == wip.FAILED
    assert result.mapping[4] == "D"
    assert [
        (finding.row_index, finding.culprit_variable)
        for finding in result.findings
    ] == [(2, "D")]


def test_digit_shape_classification_survives_graph_repair():
    matrix = make_rich_wip(n=28, decoys=0)
    row = 5
    V = 1_350_000.0
    C = 800_000.0
    G = V - C
    D = 390_003.98
    Q = C - D
    E = V * D / C
    H = E - D
    B = E - 13_000.0
    matrix[row] = [V, C, G, E, D, H, B, Q, 13_000.0, 0.0]
    matrix[row, 4] = 39_003.98

    result = wip2.validate_wip(matrix, labels(matrix))

    assert result.status == wip.FAILED
    finding = next(
        finding
        for finding in result.findings
        if finding.row_index == row
    )
    assert finding.culprit_variable == "D"
    assert finding.proposed_correction == pytest.approx(390_003.98)
    assert finding.classification == "dropped_character"


def test_neighbor_transplant_classification_uses_original_table_context():
    matrix = make_rich_wip(n=28, decoys=0)
    row = 7
    matrix[row, 3] = matrix[row - 1, 3]

    result = wip2.validate_wip(matrix, labels(matrix))

    finding = next(
        finding
        for finding in result.findings
        if finding.row_index == row
    )
    assert finding.culprit_variable == "E"
    assert finding.classification == "neighbor_transplant"
    assert finding.transplant_sources == [(row - 1, 3)]


@pytest.mark.parametrize(
    ("column", "variable"),
    ((3, "E"), (4, "D"), (5, "H"), (7, "Q")),
)
def test_repeated_errors_do_not_disable_the_identity_being_certified(
    column,
    variable,
):
    matrix = make_rich_wip(n=28, decoys=0)
    bad_rows = (2, 7, 12)
    for row in bad_rows:
        matrix[row, column] *= 0.1

    result = wip2.validate_wip(matrix, labels(matrix))

    assert result.status == wip.FAILED
    assert result.mapping[column] == variable
    located = {
        finding.row_index
        for finding in result.findings
        if finding.culprit_variable == variable
    }
    assert located == set(bad_rows)


def test_repeated_estimate_errors_use_finalist_only_recovery():
    matrix = make_rich_wip(n=28, decoys=0)
    matrix = matrix[:, [0, 1, 2, 3, 4, 5, 6, 8, 9]]
    bad_rows = (18, 19, 25)
    for row in bad_rows:
        matrix[row, 1] *= 0.1

    result = wip2.validate_wip(matrix, labels(matrix))

    assert result.status == wip.FAILED
    assert result.mapping[1] == "C"
    assert result.diagnostics["discovery"]["exhaustive_widening"] is True
    assert {
        finding.row_index
        for finding in result.findings
        if finding.culprit_variable == "C"
    } == set(bad_rows)


def test_interacting_progress_errors_preserve_mapping_and_all_rows():
    matrix = make_rich_wip(n=28, decoys=0)
    edits = (
        (3, 4, 0.1, "D"),
        (9, 3, 0.1, "E"),
        (15, 7, 0.1, "Q"),
        (21, 4, 10.0, "D"),
    )
    for row, column, factor, _ in edits:
        matrix[row, column] *= factor

    result = wip2.validate_wip(matrix, labels(matrix))

    assert result.status == wip.FAILED
    assert result.mapping == {
        0: "V",
        1: "C",
        2: "G",
        3: "E",
        4: "D",
        5: "H",
        6: "B",
        7: "Q",
        8: "U",
        9: "O",
    }
    located = {
        (finding.row_index, finding.culprit_variable)
        for finding in result.findings
        if finding.culprit_variable is not None
    }
    assert located == {
        (row, variable)
        for row, _, _, variable in edits
    }


def test_exhaustive_recovery_uses_full_rows_not_an_unlucky_sample():
    matrix = make_rich_wip(n=28, decoys=0)
    matrix = matrix[:, [0, 1, 2, 3, 4, 6, 7, 8, 9]]
    edits = (
        (27, 3, -1.0, "E"),
        (10, 1, 0.1, "C"),
        (5, 4, 0.1, "D"),
        (15, 6, 10.0, "Q"),
        (16, 0, 10.0, "V"),
    )
    for row, column, factor, _ in edits:
        matrix[row, column] *= factor

    result = wip2.validate_wip(matrix, labels(matrix))

    assert result.status == wip.FAILED
    assert result.diagnostics["discovery"]["exhaustive_widening"] is True
    assert {
        (finding.row_index, finding.culprit_variable)
        for finding in result.findings
        if finding.culprit_variable is not None
    } == {
        (row, variable)
        for row, _, _, variable in edits
    }


def test_dense_interacting_error_fixture_matches_legacy_findings():
    raw = shadow_audit_regression_table()
    parsed = parse_table(raw["rows"], headers=raw["headers"])

    current = wip.validate_wip(parsed.matrix, parsed.job_labels)
    rebuilt = wip2.validate_wip(parsed.matrix, parsed.job_labels)

    assert rebuilt.status == current.status == wip.FAILED
    assert rebuilt.mapping == current.mapping
    assert [
        (
            finding.row_label,
            finding.culprit_variable,
            finding.proposed_correction,
            finding.classification,
        )
        for finding in rebuilt.findings
    ] == [
        (
            finding.row_label,
            finding.culprit_variable,
            finding.proposed_correction,
            finding.classification,
        )
        for finding in current.findings
    ]


def test_margin_route_repairs_progress_pair_when_q_is_not_printed():
    matrix = make_rich_wip(n=28, decoys=0)
    matrix = matrix[:, [0, 1, 2, 3, 4, 5, 6, 8, 9]]
    row = 7
    matrix[row, 3] *= 0.1
    matrix[row, 4] *= 10.0

    result = wip2.validate_wip(matrix, labels(matrix))

    assert result.status == wip.FAILED
    assert {
        finding.culprit_variable
        for finding in result.findings
        if finding.row_index == row
    } == {"D", "E"}


def test_all_zero_decoy_cannot_become_a_billing_position_column():
    rows = []
    job_labels = []
    for name, V, C, G, D, Q, P, E, B, _U, O in rows_numeric():
        job_labels.append(name)
        rows.append([V, C, G, D, Q, P, E, B, O, 0.0])

    result = wip2.validate_wip(
        np.asarray(rows, dtype=float),
        job_labels,
    )

    assert result.status == wip.SUCCESS
    assert result.mapping[8] == "O"
    assert 9 not in result.mapping
    assert "U" in result.virtuals


def test_incomplete_rows_are_retained_for_full_document_certification():
    matrix = make_wip(n=24)
    matrix[3, 2] = np.nan

    result = wip2.validate_wip(matrix, labels(matrix))

    assert result.status == wip.FAILED
    incomplete = result.diagnostics["incomplete_full_document_checks"]
    assert any(
        item["column"] == 2 and item["rows"] == [3]
        for item in incomplete
    )


def test_public_api_accepts_column_sequences_like_current_validator():
    matrix = make_wip(n=24)
    columns = [matrix[:, column] for column in range(matrix.shape[1])]

    current = wip.validate_wip(columns, labels(matrix))
    rebuilt = wip2.validate_wip(columns, labels(matrix))

    assert rebuilt.status == current.status
    assert rebuilt.mapping == current.mapping


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
