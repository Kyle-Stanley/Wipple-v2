from pathlib import Path

path = Path("wipple/accounting/wip.py")
text = path.read_text()


def replace_once(old: str, new: str) -> None:
    global text
    assert text.count(old) == 1, (old[:80], text.count(old))
    text = text.replace(old, new)


replace_once(
    "    anchor_rank_global_keep: int = 8\n",
    """    anchor_rank_global_keep: int = 8
    # Common-case billing-position fast path. Split under/overbillings have
    # mutually exclusive nonzero support (with any number of shared-zero,
    # exactly-billed rows). This is a ranking prior only: every proposed anchor
    # placement still goes through the ordinary peel and strict certification,
    # and the existing global search remains the fallback.
    billing_fast_path: bool = True
    billing_motif_keep: int = 8
    billing_fast_path_pairs: int = 4
""",
)

replace_once(
    """def _v_candidates(cols, finite, cfg, shortlist):
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


""",
    """def _v_candidates(cols, finite, cfg, shortlist):
    \"\"\"Rank the portfolio-scale normalization axis before broader search.

    Contract value is normally the dominant positive money vector across an
    entire WIP. Rowwise dominance is more robust than a raw maximum or total:
    one malformed giant cell cannot outrank a column that is largest on nearly
    every job. This remains a shortlist prior and expands on failure.
    \"\"\"
    matrix = np.vstack([c[finite] for c in cols])
    if matrix.shape[1] == 0:
        return []
    positive = np.maximum(matrix, 0.0)
    row_max = positive.max(axis=0)
    row_tol = cfg.money_obs_tol + cfg.cert_money_rel * np.abs(row_max)
    scored = []
    for j, x in enumerate(matrix):
        pos = x[x > 0]
        if pos.size < max(cfg.min_rows, int(np.ceil(0.5 * max(1, x.size)))):
            continue
        dominance = float((x >= row_max - row_tol).mean())
        score = (dominance, float(np.median(pos)), float(pos.sum()))
        scored.append((j, score))
    scored.sort(key=lambda t: t[1], reverse=True)
    ranked = [j for j, _ in scored]
    return ranked[:cfg.v_shortlist] if shortlist else ranked


def _billing_motif_workspace(cols, finite, cfg):
    \"\"\"Find cheap split-U/O motifs from mutually exclusive nonzero support.

    Shared-zero rows are valid exactly-billed jobs and carry N=0. A few rows
    with both sides nonzero are tolerated under the same robust allowance used
    by identification. Both U/O orientations are retained because presentation
    signs and physical order are not semantic evidence.
    \"\"\"
    if not cfg.billing_fast_path or len(cols) < 2:
        return []
    row_index = np.nonzero(finite)[0]
    if row_index.size < cfg.min_rows:
        return []
    matrix = np.vstack([np.asarray(c, dtype=float)[row_index] for c in cols])
    active = np.abs(matrix) > cfg.money_obs_tol
    ab = _allowed_bad(matrix.shape[1], cfg)
    motifs = []
    for a in range(matrix.shape[0]):
        a_info = int(active[a].sum())
        if a_info < cfg.min_informative_rows:
            continue
        for b in range(a + 1, matrix.shape[0]):
            b_info = int(active[b].sum())
            if b_info < cfg.min_informative_rows:
                continue
            overlap = int((active[a] & active[b]).sum())
            if overlap > ab:
                continue
            active_rows = int((active[a] | active[b]).sum())
            quality = (-overlap, active_rows, min(a_info, b_info),
                       -abs(a_info - b_info))
            av = np.abs(np.asarray(cols[a], dtype=float))
            bv = np.abs(np.asarray(cols[b], dtype=float))
            motifs.append({
                \"columns\": (a, b),
                \"nets\": (av - bv, bv - av),
                \"overlap\": overlap,
                \"active_rows\": active_rows,
                \"quality\": quality,
            })
    motifs.sort(key=lambda item: item[\"quality\"], reverse=True)
    return motifs[:cfg.billing_motif_keep]


def _slice_billing_motifs(motifs, row_index):
    return [
        {
            \"columns\": motif[\"columns\"],
            \"nets\": tuple(net[row_index] for net in motif[\"nets\"]),
            \"overlap\": motif[\"overlap\"],
            \"active_rows\": motif[\"active_rows\"],
            \"quality\": motif[\"quality\"],
        }
        for motif in motifs
    ]


""",
)

marker = """def _rank_anchor_context(cols_m, Vm, Cm, vcol, xcol, orient,
                         d_candidates, b_candidates, pairs, cfg):
"""
helper = """def _rank_billing_motif_pairs(cols_m, d_cache, pairs, motifs,
                                vcol, xcol, cfg):
    \"\"\"Rank D/B placements by a precomputed split billing-position motif.

    This does not create evidence. It only asks whether the E implied by a D
    candidate and a physical B candidate reproduce N = |U| - |O| on all but
    the robustly allowed rows. The selected placement is still rebuilt by the
    ordinary peeler and certified from its independent accounting cycles.
    \"\"\"
    if not motifs or not pairs:
        return []
    m = cols_m[0].size
    ab = _allowed_bad(m, cfg)
    ranked = []
    for dcol, bcol in pairs:
        known, _ = d_cache[dcol]
        earned, earned_tol = known[\"E\"]
        predicted_net = earned - cols_m[bcol]
        strict = (earned_tol + 3.0 * cfg.money_obs_tol + cfg.cert_slack
                  + cfg.cert_money_rel * np.abs(predicted_net))
        loose = strict + np.maximum(
            cfg.ident_abs, cfg.ident_rel * np.abs(predicted_net))
        best = None
        used = {vcol, xcol, dcol, bcol}
        for motif in motifs:
            if used & set(motif[\"columns\"]):
                continue
            for orientation, net in enumerate(motif[\"nets\"]):
                resid = np.abs(predicted_net - net)
                bad = int((resid > loose).sum())
                if bad > ab:
                    continue
                strict_bad = int((resid > strict).sum())
                clipped = float(np.minimum(resid, loose).sum())
                denom = max(1.0, float(np.abs(predicted_net).sum()))
                norm = clipped / denom
                rank = (m - bad, m - strict_bad,
                        int(motif[\"active_rows\"]),
                        -int(motif[\"overlap\"]), -norm)
                if best is None or rank > best[0]:
                    best = (rank, motif, orientation, bad, strict_bad, norm)
        if best is None:
            continue
        rank, motif, orientation, bad, strict_bad, norm = best
        ranked.append((rank, dcol, bcol, {
            \"stage\": \"billing_motif_fast_path\",
            \"d\": int(dcol), \"b\": int(bcol),
            \"u_o_columns\": [int(x) for x in motif[\"columns\"]],
            \"orientation\": int(orientation),
            \"bad_rows\": int(bad),
            \"strict_bad_rows\": int(strict_bad),
            \"active_rows\": int(motif[\"active_rows\"]),
            \"overlap_rows\": int(motif[\"overlap\"]),
            \"normalized_residual\": float(norm),
            \"rank\": [float(x) for x in rank],
        }))
    ranked.sort(key=lambda item: item[0], reverse=True)
    return ranked


"""
assert text.count(marker) == 1
text = text.replace(marker, helper + marker)

replace_once(
    marker,
    """def _rank_anchor_context(cols_m, Vm, Cm, vcol, xcol, orient,
                         d_candidates, b_candidates, pairs, cfg,
                         billing_motifs=None):
""",
)

replace_once(
    """    initial = []
    if d_strong and b_strong:
        initial = sorted(
            filtered,
            key=lambda p: (d_score_map[p[0]], b_score_map[p[1]]),
            reverse=True)[:cfg.max_anchor_pairs]

    return {
""",
    """    initial = []
    if d_strong and b_strong:
        initial = sorted(
            filtered,
            key=lambda p: (d_score_map[p[0]], b_score_map[p[1]]),
            reverse=True)[:cfg.max_anchor_pairs]

    motif_pairs = filtered if filtered else pairs
    billing_ranked = _rank_billing_motif_pairs(
        cols_m, d_cache, motif_pairs, billing_motifs or [],
        vcol, xcol, cfg)

    return {
""",
)

replace_once(
    """        \"filtered\": filtered, \"all_pairs\": pairs,
        \"initial\": initial,
    }
""",
    """        \"filtered\": filtered, \"all_pairs\": pairs,
        \"initial\": initial, \"billing_ranked\": billing_ranked,
    }
""",
)

replace_once(
    """def _enumerate_hypotheses(cols, finite, cfg, diag, shortlist):
    by_key = {}
    wide_contexts = []
""",
    """def _enumerate_hypotheses(cols, finite, cfg, diag, shortlist):
    by_key = {}
    wide_contexts = []
    billing_motifs = _billing_motif_workspace(cols, finite, cfg)
    diag[\"billing_motif_candidates\"] = len(billing_motifs)
""",
)

replace_once(
    """            context = {
                \"cols_m\": cols_m, \"row_index\": row_index,
                \"vcol\": vcol, \"xcol\": xcol, \"orient\": orient,
                \"Vm\": Vm, \"Cm\": Cm,
            }
""",
    """            context = {
                \"cols_m\": cols_m, \"row_index\": row_index,
                \"vcol\": vcol, \"xcol\": xcol, \"orient\": orient,
                \"Vm\": Vm, \"Cm\": Cm,
                \"billing_motifs\": _slice_billing_motifs(
                    billing_motifs, row_index),
            }
""",
)

replace_once(
    """            ranked_ctx = _rank_anchor_context(
                cols_m, Vm, Cm, vcol, xcol, orient,
                d_c, b_c, pairs, cfg)
""",
    """            ranked_ctx = _rank_anchor_context(
                cols_m, Vm, Cm, vcol, xcol, orient,
                d_c, b_c, pairs, cfg, context[\"billing_motifs\"])
""",
)

replace_once(
    """    if not wide_contexts:
        return by_key

    # Pair-dependent N/U/O scoring is inexpensive compared with a full peel.
""",
    """    if not wide_contexts:
        return by_key

    # Common split-U/O fast pass. The motif only ranks candidates; each chosen
    # placement is still fully peeled. If none validates, the existing global
    # pair ranking and progressive widening run unchanged.
    billing_global = []
    for context in wide_contexts:
        for rank, dcol, bcol, detail in context[\"ranked\"][\"billing_ranked\"]:
            billing_global.append((rank, context, dcol, bcol, detail))
    billing_global.sort(key=lambda item: item[0], reverse=True)
    if billing_global:
        top_rank = billing_global[0][0]
        selected = [item for item in billing_global if item[0] == top_rank]
        selected = selected[:cfg.billing_fast_path_pairs]
        grouped = {}
        for _, context, dcol, bcol, _ in selected:
            grouped.setdefault(id(context), (context, []))[1].append(
                (dcol, bcol))
        for context, pairs in grouped.values():
            evaluate(context, pairs)
        diag[\"billing_fast_path\"] = {
            \"ranked_pairs\": int(len(billing_global)),
            \"peeled_pairs\": int(len(selected)),
            \"top\": [
                dict(item[4], v_col=int(item[1][\"vcol\"]),
                     x_col=int(item[1][\"xcol\"]),
                     orientation=item[1][\"orient\"])
                for item in selected[:5]
            ],
        }
        if has_validatable():
            return by_key

    # Pair-dependent N/U/O scoring is inexpensive compared with a full peel.
""",
)

path.write_text(text)

Path("tests/test_billing_motif_fast_path.py").write_text(r'''from __future__ import annotations

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
    assert dataclasses.asdict(fast) == dataclasses.asdict(slow)
''')
