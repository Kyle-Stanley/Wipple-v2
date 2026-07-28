from __future__ import annotations

import dataclasses

import numpy as np

from wipple.accounting.wip import (
    Config,
    _anchor_candidates,
    _billing_motif_workspace,
    _rank_anchor_context,
    _slice_billing_motifs,
    _v_candidates,
    _x_candidates,
    validate_wip,
)


def make_wip(n=20, signed_over=True, shared_zero=True):
    rows = []
    for i in range(n):
        V = 1_000_000.0 + i * 31_000.0
        C = V * (0.76 + 0.01 * (i % 4))
        G = V - C
        D = C * (0.18 + 0.035 * (i % 15))
        Q = C - D
        P = D / C
        E = V * P
        if shared_zero and i % 7 == 0:
            B = E
        elif i % 2:
            B = E + 17_000.0
        else:
            B = E - 13_000.0
        U = max(E - B, 0.0)
        O = max(B - E, 0.0)
        if signed_over:
            O = -O
        H = E - D
        R = V - E
        RB = V - B
        PB = B / V
        M = G / V
        rows.append([V, C, G, D, Q, P, E, B, U, O, H, R, RB, PB, M])
    return np.asarray(rows, dtype=float)


def test_split_billing_motif_allows_shared_zero_rows_and_signed_overbillings():
    matrix = make_wip()
    cfg = Config()
    finite = np.all(np.isfinite(matrix), axis=1)
    motifs = _billing_motif_workspace(
        [matrix[:, j] for j in range(matrix.shape[1])], finite, cfg)
    pair = next(m for m in motifs if set(m["columns"]) == {8, 9})
    assert pair["overlap"] == 0
    assert pair["active_rows"] < matrix.shape[0]
    expected = matrix[:, 6] - matrix[:, 7]
    assert any(np.allclose(net, expected) for net in pair["nets"])


def test_portfolio_dominance_ranks_contract_value_first():
    matrix = make_wip()
    malformed = np.full(matrix.shape[0], 20_000.0)
    malformed[3] = 999_999_999.0
    matrix = np.column_stack([matrix, malformed])
    cfg = Config()
    finite = np.ones(matrix.shape[0], dtype=bool)
    ranked = _v_candidates(
        [matrix[:, j] for j in range(matrix.shape[1])],
        finite, cfg, shortlist=False)
    assert ranked[0] == 0


def test_billing_motif_ranks_true_d_b_pair_first():
    base = make_wip(n=24)
    decoys = []
    for k in range(18):
        frac = 0.05 + 0.035 * k
        decoys.append(base[:, 0] * frac * (0.85 + 0.02 * (np.arange(24) % 5)))
    matrix = np.column_stack([base, *decoys])
    cols = [matrix[:, j] for j in range(matrix.shape[1])]
    finite = np.ones(matrix.shape[0], dtype=bool)
    cfg = Config(max_anchor_pairs=10)
    motifs = _billing_motif_workspace(cols, finite, cfg)
    x = _x_candidates(cols, 0, finite, cfg, {"prior_rejections": []})
    assert (1, "C") in x
    Vm, Cm = cols[0], cols[1]
    d_c, b_c = _anchor_candidates(cols, Vm, Cm, {0, 1}, cfg)
    pairs = [(d, b) for d in d_c for b in b_c if d != b]
    ranked = _rank_anchor_context(
        cols, Vm, Cm, 0, 1, "C", d_c, b_c, pairs, cfg,
        _slice_billing_motifs(motifs, np.arange(matrix.shape[0])))
    assert ranked["billing_ranked"]
    _, dcol, bcol, detail = ranked["billing_ranked"][0]
    assert (dcol, bcol) == (3, 7)
    assert set(detail["u_o_columns"]) == {8, 9}


def test_fast_path_and_fallback_return_same_validation():
    matrix = make_wip(n=18)
    labels = [f"J-{i}" for i in range(matrix.shape[0])]
    fast = validate_wip(matrix, labels, Config(max_anchor_pairs=10))
    slow = validate_wip(
        matrix, labels,
        Config(max_anchor_pairs=10, billing_fast_path=False))
    fields = (
        "status", "reason", "mapping", "mapping_named",
        "estimate_orientation", "virtuals", "witnesses", "failures",
        "findings", "competing_mapping", "suggested_disambiguator",
    )
    assert {name: getattr(fast, name) for name in fields} == {
        name: getattr(slow, name) for name in fields
    }
