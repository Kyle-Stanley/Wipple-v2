from __future__ import annotations

import math

import numpy as np
import pytest

from wipple.accounting.wip import Config, validate_wip


def make_rich_wip(n=72, decoys=24, percent_mode=None):
    rows = []
    for i in range(n):
        V = 1_000_000.0 + i * 27_000.0
        C = V * (0.76 + 0.01 * (i % 4))
        G = V - C
        true_p = 0.16 + 0.72 * ((i % 31) / 30.0)
        D = C * true_p
        Q = C - D
        E = V * true_p
        H = E - D
        if i % 9 == 0:
            B = E
        elif i % 2:
            B = E + 17_000.0
        else:
            B = E - 13_000.0
        U = max(E - B, 0.0)
        O = -max(B - E, 0.0)
        values = [V, C, G, E, D, H, B, Q, U, O]
        if percent_mode == "whole":
            values.append(math.floor(true_p * 100.0))
        elif percent_mode == "tenth":
            values.append(math.floor(true_p * 1000.0) / 10.0)
        rows.append(values)
    matrix = np.asarray(rows, dtype=float)
    extras = []
    r = np.arange(n, dtype=float)
    for k in range(decoys):
        frac = 0.035 + 0.019 * k
        extras.append(matrix[:, 0] * frac * (0.82 + 0.025 * ((r + k) % 7)))
    if extras:
        matrix = np.column_stack([matrix, *extras])
    return matrix


def semantic(result):
    fields = (
        "status", "reason", "mapping", "mapping_named",
        "estimate_orientation", "virtuals", "witnesses", "failures",
        "findings", "competing_mapping", "suggested_disambiguator",
    )
    return {name: getattr(result, name) for name in fields}


def test_nested_complement_planner_collapses_wide_search():
    matrix = make_rich_wip(n=96, decoys=36)
    labels = [f"J-{i}" for i in range(matrix.shape[0])]
    result = validate_wip(
        matrix, labels,
        Config(max_anchor_pairs=10, motif_rank_rows=60))

    assert result.mapping[0] == "V"
    assert result.mapping[1] == "C"
    assert result.mapping[2] == "G"
    assert result.mapping[3] == "E"
    assert result.mapping[4] == "D"
    assert result.mapping[6] == "B"
    assert result.mapping[7] == "Q"
    assert result.mapping[8] == "U"
    assert result.mapping[9] == "O"
    planner = result.diagnostics["motif_planner"]
    assert planner["used"] is True
    assert planner["oriented_hub_pairs"] >= 1
    assert planner["peeled_hypotheses"] <= 8
    assert result.diagnostics["hypotheses_examined"] <= 8


def test_planner_and_fallback_return_same_semantic_validation():
    matrix = make_rich_wip(n=36, decoys=12)
    labels = [f"J-{i}" for i in range(matrix.shape[0])]
    fast = validate_wip(
        matrix, labels,
        Config(max_anchor_pairs=10, motif_rank_rows=32))
    slow = validate_wip(
        matrix, labels,
        Config(max_anchor_pairs=10, motif_planner=False,
               billing_fast_path=False))

    assert semantic(fast) == semantic(slow)
    assert fast.diagnostics["hypotheses_examined"] \
        < slow.diagnostics["hypotheses_examined"]


@pytest.mark.parametrize("mode", ["whole", "tenth"])
def test_percent_complete_projection_handles_truncation(mode):
    matrix = make_rich_wip(n=48, decoys=14, percent_mode=mode)
    # Remove the direct D/Q complement and H so the planner has to use the
    # quantized Percent Complete projection to propose D and E.
    keep = [0, 1, 2, 3, 4, 6, 8, 9, 10] + list(range(11, matrix.shape[1]))
    matrix = matrix[:, keep]
    labels = [f"J-{i}" for i in range(matrix.shape[0])]
    result = validate_wip(
        matrix, labels,
        Config(max_anchor_pairs=8, motif_rank_rows=40))

    planner = result.diagnostics["motif_planner"]
    assert planner["used"] is True
    assert any(item["source"] == "percent_complete"
               for item in planner["top"])
    assert result.mapping[0] == "V"
    assert result.mapping[1] == "C"
    assert result.mapping[2] == "G"
    assert result.mapping[3] == "E"
    assert result.mapping[4] == "D"
    assert result.mapping[5] == "B"
    assert result.mapping[8] == "P"



def test_single_signed_net_position_drives_billing_bridge():
    base = make_rich_wip(n=52, decoys=16)
    net = base[:, 8] + base[:, 9]  # U plus signed O equals E - B
    matrix = np.column_stack([base[:, :8], net, base[:, 10:]])
    labels = [f"J-{i}" for i in range(matrix.shape[0])]
    result = validate_wip(
        matrix, labels,
        Config(max_anchor_pairs=8, motif_rank_rows=44))

    planner = result.diagnostics["motif_planner"]
    assert planner["used"] is True
    assert any(item["billing"] == "signed_net" for item in planner["top"])
    assert result.mapping[0] == "V"
    assert result.mapping[1] == "C"
    assert result.mapping[2] == "G"
    assert result.mapping[3] == "E"
    assert result.mapping[4] == "D"
    assert result.mapping[6] == "B"
    assert result.mapping[8] == "N"
