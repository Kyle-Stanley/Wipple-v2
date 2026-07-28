from pathlib import Path


path = Path("wipple/accounting/wip.py")
text = path.read_text()

config_old = """    billing_fast_path: bool = True
    billing_motif_keep: int = 8
    billing_fast_path_pairs: int = 4
"""
config_new = """    billing_fast_path: bool = True
    billing_motif_keep: int = 8
    billing_fast_path_pairs: int = 4
    # Pre-enumeration structural planner. Rich WIPs normally expose a dominant
    # Contract Value hub, its C/G complement, and either a nested D/Q complement
    # or a physical Percent Complete projection. The planner uses those motifs
    # to propose a few complete anchor placements before the expensive generic
    # V/X enumeration. Every proposal still goes through the ordinary peeler
    # and strict certification; failure falls through to the existing search.
    motif_planner: bool = True
    motif_rank_rows: int = 64
    motif_v_keep: int = 3
    motif_sum_pairs_keep: int = 8
    motif_matches_keep: int = 3
    motif_hypotheses_keep: int = 8
"""
assert config_old in text
text = text.replace(config_old, config_new, 1)

old_guard = """    if not cfg.billing_fast_path or len(cols) < 2:
        return []
"""
new_guard = """    if not (cfg.billing_fast_path or cfg.motif_planner) or len(cols) < 2:
        return []
"""
assert old_guard in text
text = text.replace(old_guard, new_guard, 1)

marker = "\n\ndef _x_candidates(cols, vcol, finite, cfg, diag):\n"
assert marker in text
planner_code = r'''


def _motif_sample_indices(finite, cfg):
    """Deterministic ranking-only row sample; certification always uses all rows."""
    rows = np.nonzero(finite)[0]
    cap = max(cfg.min_rows, int(cfg.motif_rank_rows))
    if rows.size <= cap:
        return rows
    positions = np.rint(np.linspace(0, rows.size - 1, cap)).astype(int)
    return rows[np.unique(positions)]


def _motif_workspace(cols, finite, cfg):
    row_index = _motif_sample_indices(finite, cfg)
    cols_m = [np.asarray(c[row_index], dtype=float) for c in cols]
    matrix = np.vstack(cols_m)
    return {
        "row_index": row_index,
        "cols": cols_m,
        "matrix": matrix,
        "abs_matrix": np.abs(matrix),
    }


def _motif_pair_sums(target_col, ws, cfg, exclude=()):
    """Best physical column pairs whose rowwise sum reproduces target_col."""
    matrix = ws["matrix"]
    m = matrix.shape[1]
    if m == 0:
        return []
    target = matrix[target_col]
    blocked = set(exclude) | {target_col}
    candidates = [j for j in range(matrix.shape[0]) if j not in blocked]
    ab = _allowed_bad(m, cfg)
    strict = (3.0 * cfg.money_obs_tol + cfg.cert_slack
              + cfg.cert_money_rel * np.abs(target))
    loose = strict + np.maximum(cfg.ident_abs,
                                cfg.ident_rel * np.abs(target))
    denom = max(1.0, float(np.abs(target).sum()))
    records = []
    for pos, a in enumerate(candidates):
        b_idx = np.asarray(candidates[pos + 1:], dtype=int)
        if b_idx.size == 0:
            continue
        resid = np.abs(matrix[b_idx] + matrix[a] - target[None, :])
        bad = (resid > loose[None, :]).sum(axis=1)
        qualified = np.nonzero(bad <= ab)[0]
        for k in qualified:
            strict_bad = int((resid[k] > strict).sum())
            norm = float(np.minimum(resid[k], loose).sum()) / denom
            records.append({
                "columns": (int(a), int(b_idx[k])),
                "bad": int(bad[k]),
                "strict_bad": strict_bad,
                "norm_resid": norm,
                "rank": (m - int(bad[k]), m - strict_bad, -norm),
            })
    records.sort(key=lambda item: item["rank"], reverse=True)
    return records[:cfg.motif_sum_pairs_keep]


def _motif_match_money(pred, ptol, ws, cfg, exclude=(), keep=None):
    """Vectorized ranking-only match of one predicted money vector to columns."""
    matrix = ws["matrix"]
    candidates = np.asarray(
        [j for j in range(matrix.shape[0]) if j not in set(exclude)],
        dtype=int)
    if candidates.size == 0:
        return []
    m = pred.size
    ab = _allowed_bad(m, cfg)
    strict = (np.asarray(ptol, dtype=float) + cfg.money_obs_tol
              + cfg.cert_slack + cfg.cert_money_rel * np.abs(pred))
    loose = strict + np.maximum(cfg.ident_abs,
                                cfg.ident_rel * np.abs(pred))
    resid = np.abs(matrix[candidates] - pred[None, :])
    bad = (resid > loose[None, :]).sum(axis=1)
    strict_bad = (resid > strict[None, :]).sum(axis=1)
    denom = max(1.0, float(np.abs(pred).sum()))
    norm = np.minimum(resid, loose[None, :]).sum(axis=1) / denom
    qualified = np.nonzero(bad <= ab)[0]
    if qualified.size == 0:
        return []
    order = np.lexsort((norm[qualified], bad[qualified],
                        strict_bad[qualified]))
    limit = cfg.motif_matches_keep if keep is None else int(keep)
    out = []
    for k in qualified[order[:limit]]:
        out.append({
            "column": int(candidates[k]),
            "bad": int(bad[k]),
            "strict_bad": int(strict_bad[k]),
            "norm_resid": float(norm[k]),
            "rank": (m - int(bad[k]), m - int(strict_bad[k]),
                     -float(norm[k])),
        })
    return out


def _motif_percent_interpretations(ws, cfg, exclude=()):
    """Likely ratio/percent columns with explicit whole/tenth truncation grids."""
    matrix = ws["matrix"]
    m = matrix.shape[1]
    blocked = set(exclude)
    out = []
    for col, raw in enumerate(matrix):
        if col in blocked:
            continue
        for scale in (1.0, 100.0):
            values = raw / scale
            inside = (values >= -0.01) & (values <= 1.25)
            if int(inside.sum()) < int(np.ceil(0.9 * m)):
                continue
            if scale == 100.0 and np.allclose(raw, np.round(raw), atol=1e-8):
                tol = 0.01       # whole displayed percentage, possibly truncated
            elif (scale == 100.0
                  and np.allclose(raw * 10.0, np.round(raw * 10.0),
                                  atol=1e-8)):
                tol = 0.001      # displayed to one tenth of a percentage point
            else:
                grid = detect_grid(values)
                tol = (grid * cfg.pct_grid_mult if grid is not None
                       else cfg.pct_default_tol)
            out.append({
                "column": int(col), "scale": float(scale),
                "values": values, "tol": np.full(m, tol, dtype=float),
            })
    return out


def _motif_estimate_pairs(vcol, ws, cfg):
    """Confirm V as an additive hub and orient its physical C/G complement."""
    V = ws["matrix"][vcol]
    mask = V > cfg.money_obs_tol
    if int(mask.sum()) < cfg.min_rows:
        return []
    clo, chi = cfg.cost_ratio_band
    glo, ghi = cfg.margin_band
    out = []
    for pair in _motif_pair_sums(vcol, ws, cfg):
        a, b = pair["columns"]
        for ccol, gcol in ((a, b), (b, a)):
            cr = ws["matrix"][ccol][mask] / V[mask]
            gr = ws["matrix"][gcol][mask] / V[mask]
            cmed, gmed = float(np.median(cr)), float(np.median(gr))
            ciqr, giqr = _iqr(cr), _iqr(gr)
            if not (clo <= cmed <= chi and glo <= gmed <= ghi):
                continue
            if max(ciqr, giqr) > cfg.estimate_iqr_max:
                continue
            item = dict(pair)
            item.update({
                "v_col": int(vcol), "c_col": int(ccol),
                "g_col": int(gcol), "c_median": cmed,
                "g_median": gmed, "stability": max(ciqr, giqr),
                "rank": pair["rank"] + (-max(ciqr, giqr),),
            })
            out.append(item)
    out.sort(key=lambda item: item["rank"], reverse=True)
    return out


def _motif_valid_b_match(match, V, ws, cfg):
    x = ws["matrix"][match["column"]]
    m = x.size
    required = m - _allowed_bad(m, cfg)
    return (int((x >= -cfg.money_obs_tol).sum()) >= required
            and int((x <= V * cfg.b_over_v_slack + 1.0).sum()) >= required)


def _motif_billing_bridges(E, Etol, V, ws, billing_motifs, cfg,
                            exclude=()):
    """Propose physical B from split U/O, signed N, or Percent Billed."""
    m = E.size
    blocked = set(exclude)
    bridges = []

    # Strongest bridge: a split U/O pair has mutually exclusive nonzero support.
    for motif in billing_motifs:
        if blocked & set(motif["columns"]):
            continue
        for orientation, net in enumerate(motif["nets"]):
            Bpred = E - net
            Btol = Etol + 2.0 * cfg.money_obs_tol
            for match in _motif_match_money(
                    Bpred, Btol, ws, cfg,
                    exclude=blocked | set(motif["columns"])):
                if not _motif_valid_b_match(match, V, ws, cfg):
                    continue
                bridges.append({
                    "b_col": match["column"], "strength": 3,
                    "kind": "split_u_o", "match": match,
                    "source_columns": tuple(motif["columns"]),
                    "orientation": int(orientation),
                    "rank": (3,) + match["rank"] + motif["quality"],
                })

    # One signed net-position column. It has no standalone visual signature, but
    # E - N must still reproduce a separate physical B vector across the table.
    for ncol in range(ws["matrix"].shape[0]):
        if ncol in blocked:
            continue
        N = ws["matrix"][ncol]
        if int((np.abs(N) > cfg.money_obs_tol).sum()) < cfg.min_informative_rows:
            continue
        Bpred = E - N
        Btol = Etol + cfg.money_obs_tol
        for match in _motif_match_money(
                Bpred, Btol, ws, cfg, exclude=blocked | {ncol}, keep=1):
            if not _motif_valid_b_match(match, V, ws, cfg):
                continue
            bridges.append({
                "b_col": match["column"], "strength": 2,
                "kind": "signed_net", "match": match,
                "source_columns": (int(ncol),), "orientation": 0,
                "rank": (2,) + match["rank"],
            })

    # Optional percent-billed bridge. Quantization uncertainty is propagated
    # into the predicted money vector; full-row peel/certification remains final.
    obs_tol = np.full(m, cfg.money_obs_tol)
    for pct in _motif_percent_interpretations(ws, cfg, exclude=blocked):
        Bpred, Btol = _prop_tol(
            lambda v, p: v * p, [V, pct["values"]],
            [obs_tol, pct["tol"]])
        for match in _motif_match_money(
                Bpred, Btol, ws, cfg,
                exclude=blocked | {pct["column"]}, keep=1):
            if not _motif_valid_b_match(match, V, ws, cfg):
                continue
            bridges.append({
                "b_col": match["column"], "strength": 1,
                "kind": "percent_billed", "match": match,
                "source_columns": (pct["column"],),
                "orientation": int(pct["scale"]),
                "rank": (1,) + match["rank"],
            })

    best = {}
    for bridge in bridges:
        col = bridge["b_col"]
        if col not in best or bridge["rank"] > best[col]["rank"]:
            best[col] = bridge
    return sorted(best.values(), key=lambda item: item["rank"], reverse=True)


def _motif_nested_complement_proposals(estimate, ws, billing_motifs, cfg):
    """Use C=D+Q, then E=V*D/C and H=E-D, before proposing B."""
    matrix = ws["matrix"]
    m = matrix.shape[1]
    vcol, ccol, gcol = (estimate["v_col"], estimate["c_col"],
                         estimate["g_col"])
    V, C = matrix[vcol], matrix[ccol]
    obs_tol = np.full(m, cfg.money_obs_tol)
    proposals = []
    c_pairs = _motif_pair_sums(
        ccol, ws, cfg, exclude={vcol, gcol})
    for complement in c_pairs:
        a, b = complement["columns"]
        for dcol, qcol in ((a, b), (b, a)):
            D = matrix[dcol]
            required = m - _allowed_bad(m, cfg)
            if int((D >= -cfg.money_obs_tol).sum()) < required:
                continue
            if int((D <= C * cfg.d_over_c_slack + 1.0).sum()) < required:
                continue
            if float(np.median(D / np.maximum(C, 1e-9))) < cfg.anchor_live_med:
                continue
            E, Etol = _prop_tol(
                RULE_BY_NAME["E = V x D / C"].fn,
                [V, D, C], [obs_tol, obs_tol, obs_tol])
            used = {vcol, ccol, gcol, dcol, qcol}
            ematches = _motif_match_money(E, Etol, ws, cfg, exclude=used)
            for ematch in ematches:
                H, Htol = _prop_tol(
                    RULE_BY_NAME["H = E - D"].fn,
                    [E, D], [Etol, obs_tol])
                hmatches = _motif_match_money(
                    H, Htol, ws, cfg, exclude=used | {ematch["column"]},
                    keep=1)
                hmatch = hmatches[0] if hmatches else None
                bridges = _motif_billing_bridges(
                    E, Etol, V, ws, billing_motifs, cfg,
                    exclude=used | {ematch["column"]}
                    | ({hmatch["column"]} if hmatch else set()))
                for bridge in bridges[:cfg.motif_matches_keep]:
                    d_support = 2 if hmatch is not None else 1
                    strict_rows = (
                        estimate["rank"][1] + complement["rank"][1]
                        + ematch["rank"][1]
                        + (hmatch["rank"][1] if hmatch else 0)
                        + bridge["match"]["rank"][1])
                    robust_rows = (
                        estimate["rank"][0] + complement["rank"][0]
                        + ematch["rank"][0]
                        + (hmatch["rank"][0] if hmatch else 0)
                        + bridge["match"]["rank"][0])
                    norm = (estimate["norm_resid"]
                            + complement["norm_resid"]
                            + ematch["norm_resid"]
                            + (hmatch["norm_resid"] if hmatch else 0.0)
                            + bridge["match"]["norm_resid"])
                    proposals.append({
                        "vcol": vcol, "xcol": ccol, "orient": "C",
                        "dcol": dcol, "bcol": bridge["b_col"],
                        "source": "nested_complements",
                        "e_col": ematch["column"],
                        "h_col": hmatch["column"] if hmatch else None,
                        "q_col": qcol, "billing": bridge,
                        "rank": (d_support, bridge["strength"],
                                 strict_rows, robust_rows, -norm),
                    })
    return proposals


def _motif_percent_complete_proposals(estimate, ws, billing_motifs, cfg):
    """Optional P projection with whole/tenth percent truncation tolerance."""
    matrix = ws["matrix"]
    m = matrix.shape[1]
    vcol, ccol, gcol = (estimate["v_col"], estimate["c_col"],
                         estimate["g_col"])
    V, C = matrix[vcol], matrix[ccol]
    obs_tol = np.full(m, cfg.money_obs_tol)
    proposals = []
    used_base = {vcol, ccol, gcol}
    for pct in _motif_percent_interpretations(ws, cfg, exclude=used_base):
        P, Ptol = pct["values"], pct["tol"]
        D, Dtol = _prop_tol(
            lambda c, p: c * p, [C, P], [obs_tol, Ptol])
        E, Etol = _prop_tol(
            lambda v, p: v * p, [V, P], [obs_tol, Ptol])
        dmatches = _motif_match_money(
            D, Dtol, ws, cfg, exclude=used_base | {pct["column"]})
        ematches = _motif_match_money(
            E, Etol, ws, cfg, exclude=used_base | {pct["column"]})
        for dmatch in dmatches:
            for ematch in ematches:
                if dmatch["column"] == ematch["column"]:
                    continue
                used = (used_base | {pct["column"], dmatch["column"],
                                    ematch["column"]})
                bridges = _motif_billing_bridges(
                    E, Etol, V, ws, billing_motifs, cfg, exclude=used)
                for bridge in bridges[:cfg.motif_matches_keep]:
                    strict_rows = (estimate["rank"][1]
                                   + dmatch["rank"][1]
                                   + ematch["rank"][1]
                                   + bridge["match"]["rank"][1])
                    robust_rows = (estimate["rank"][0]
                                   + dmatch["rank"][0]
                                   + ematch["rank"][0]
                                   + bridge["match"]["rank"][0])
                    norm = (estimate["norm_resid"]
                            + dmatch["norm_resid"] + ematch["norm_resid"]
                            + bridge["match"]["norm_resid"])
                    proposals.append({
                        "vcol": vcol, "xcol": ccol, "orient": "C",
                        "dcol": dmatch["column"],
                        "bcol": bridge["b_col"],
                        "source": "percent_complete",
                        "p_col": pct["column"], "p_scale": pct["scale"],
                        "e_col": ematch["column"], "billing": bridge,
                        "rank": (2, bridge["strength"],
                                 strict_rows, robust_rows, -norm),
                    })
    return proposals


def _motif_plan_proposals(cols, finite, cfg, diag, billing_motifs):
    """Construct a few complete V/C/D/B placements before generic enumeration."""
    ws = _motif_workspace(cols, finite, cfg)
    sample_rows = ws["row_index"]
    sliced_billing = _slice_billing_motifs(billing_motifs, sample_rows)
    v_candidates = _v_candidates(cols, finite, cfg, shortlist=True)[
        :cfg.motif_v_keep]
    proposals = []
    hub_pairs = 0
    for vcol in v_candidates:
        estimates = _motif_estimate_pairs(vcol, ws, cfg)
        hub_pairs += len(estimates)
        for estimate in estimates:
            proposals += _motif_nested_complement_proposals(
                estimate, ws, sliced_billing, cfg)
            proposals += _motif_percent_complete_proposals(
                estimate, ws, sliced_billing, cfg)

    deduped = {}
    for proposal in proposals:
        key = (proposal["vcol"], proposal["xcol"], proposal["orient"],
               proposal["dcol"], proposal["bcol"])
        if key not in deduped or proposal["rank"] > deduped[key]["rank"]:
            deduped[key] = proposal
    ranked = sorted(deduped.values(), key=lambda item: item["rank"],
                    reverse=True)[:cfg.motif_hypotheses_keep]
    diag["motif_planner"] = {
        "sample_rows": int(sample_rows.size),
        "v_candidates": [int(x) for x in v_candidates],
        "oriented_hub_pairs": int(hub_pairs),
        "billing_motifs": int(len(sliced_billing)),
        "ranked_proposals": int(len(deduped)),
        "peeled_hypotheses": 0,
        "used": False,
        "top": [
            {
                "v_col": int(p["vcol"]), "c_col": int(p["xcol"]),
                "d_col": int(p["dcol"]), "b_col": int(p["bcol"]),
                "source": p["source"],
                "billing": p["billing"]["kind"],
                "rank": [float(x) for x in p["rank"]],
            }
            for p in ranked[:5]
        ],
    }
    return ranked
'''
text = text.replace(marker, planner_code + marker, 1)

hook_old = """    def has_validatable():
        return any(h.corr_d >= 1 and h.corr_b >= 1
                   for h in by_key.values())

    for vcol in _v_candidates(cols, finite, cfg, shortlist):
"""
hook_new = """    def has_validatable():
        return any(h.corr_d >= 1 and h.corr_b >= 1
                   for h in by_key.values())

    # Structural common path: use table-level WIP motifs to propose complete
    # anchors before constructing every V/X context. A successful proposal is
    # still an ordinary peeled hypothesis; inability to validate simply falls
    # through to the unchanged comprehensive search below.
    if shortlist and cfg.motif_planner:
        proposals = _motif_plan_proposals(
            cols, finite, cfg, diag, billing_motifs)
        for proposal in proposals:
            Vfull = cols[proposal["vcol"]]
            Cfull = cols[proposal["xcol"]]
            mask = finite & (Vfull > 0) & (Cfull > 0)
            if int(mask.sum()) < cfg.min_rows:
                continue
            cols_m = [c[mask] for c in cols]
            row_index = np.nonzero(mask)[0]
            hyp = _build_hypothesis(
                cols_m, row_index, proposal["vcol"], proposal["xcol"],
                proposal["orient"], proposal["dcol"], proposal["bcol"], cfg)
            diag["hypotheses_examined"] = \
                diag.get("hypotheses_examined", 0) + 1
            diag["motif_planner"]["peeled_hypotheses"] += 1
            old = by_key.get(hyp.key)
            if old is None or hyp.score > old.score:
                by_key[hyp.key] = hyp
        if has_validatable():
            diag["motif_planner"]["used"] = True
            return by_key

    for vcol in _v_candidates(cols, finite, cfg, shortlist):
"""
assert hook_old in text
text = text.replace(hook_old, hook_new, 1)

path.write_text(text)


test_path = Path("tests/test_wip_motif_planner.py")
test_path.write_text(r'''from __future__ import annotations

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
''')
