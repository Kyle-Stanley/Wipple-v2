"""
Block-misalignment detection: the transplant detector promoted from cells to
blocks, living OUTSIDE the validator so the engine stays provenance-blind.

Failure mode: an extractor drops or merges a column on ONE chunk; every cell
in that chunk displaces sideways and the validator dutifully reports sixty
transcription errors. The tell is distributional -- failures land in one
provenance band while every other band is clean -- and the cure is a
deterministic hypothesis sweep, not a model call:

  for each (shift s, start column k): move the band's cells right by s from
  column k on and re-check every closed money identity on the band. A unique
  (s, k) under which the band's identities pass at dollar precision is a
  certified diagnosis; the repair is applied, the finding cites the chunk
  and pages, and the column whose values were physically lost (shifted off
  the edge) stays NaN with a re-extract suggestion.

Ambiguity or no winner -> no repair, band escalates to re-extraction.
"""

from __future__ import annotations

import numpy as np

from .schemas import CC_LATTICE
from .wip_validator import RULES

_SHIFTS = (1, 2, -1, -2)


def _checks_for(mapping: dict, schema: str):
    """Closed identities as (name, out, ins, fn) over mapped vars only."""
    have = set(mapping.values())
    if schema == "cc":
        out = []
        for (a, b, s) in CC_LATTICE + [("BC", "RR", "RT")]:
            if {a, b, s} <= have:
                out.append((f"{s} = {a} + {b}", s, (a, b),
                            lambda A, B: A + B, "money"))
        return out
    return [(r.name, r.out, r.ins, r.fn, r.kind) for r in RULES
            if not r.clipped and r.out in have and set(r.ins) <= have]


def _band_passes(cols, band, mapping, checks, tol=2.0):
    inv = {v: k for k, v in mapping.items()}
    n_checked = n_pass = 0
    for (_, out, ins, fn, kind) in checks:
        with np.errstate(divide="ignore", invalid="ignore"):
            pred = fn(*[cols[np.ix_(band, [inv[v]])].ravel() for v in ins])
        obs = cols[np.ix_(band, [inv[out]])].ravel()
        fin = np.isfinite(pred) & np.isfinite(obs)
        if not fin.any():
            continue
        if kind == "pct":
            # ratio identities discriminate shifts that money checks can't
            # reach once the lost column NaNs them out. Display-percent
            # columns (parse leaves them raw when the band polluted the
            # median) are detected per-band and matched in display units.
            if np.nanmedian(np.abs(obs[fin])) > 1.5:
                pred = pred * 100.0
                rel_tol = np.full(int(fin.sum()), 0.6)
            else:
                rel_tol = np.full(int(fin.sum()), 0.006)
        else:
            rel_tol = np.maximum(tol, 1e-6 * np.abs(pred[fin]))
        n_checked += int(fin.sum())
        n_pass += int((np.abs(obs[fin] - pred[fin]) <= rel_tol).sum())
    return n_checked, n_pass


def _shift_band(cols, band, s, start, scaled):
    """Hypothesis: the band's cells from column `start` on were displaced
    LEFT by s (s>0) or RIGHT by |s| (s<0) during extraction. Repair moves
    them back; the |s| columns whose data left the frame become NaN.
    Crossing a percent-scaled column crosses a units boundary (display
    percents vs fractions), so moved values are rescaled accordingly."""
    cand = cols.copy()
    m = cols.shape[1]

    def factor(dst, src):
        if dst in scaled and src not in scaled:
            return 0.01
        if src in scaled and dst not in scaled:
            return 100.0
        return 1.0

    if s > 0:
        for j in range(m - 1, start + s - 1, -1):
            src = j - s
            cand[band, j] = cols[band, src] * factor(j, src) \
                if src >= start else np.nan
        edge = list(range(start, start + s))
    else:
        k = -s
        for j in range(start, m - k):
            src = j + k
            cand[band, j] = cols[band, src] * factor(j, src)
        edge = list(range(m - k, m))
    for j in edge:
        if 0 <= j < m:
            cand[band, j] = np.nan
    return cand, edge


def check_bands(matrix, mapping, schema, failures, band_of_row,
                scaled=(), min_band=3, frac=0.5):
    """failures: serialized RowFailure dicts (row_index = matrix row).
    band_of_row: matrix row -> chunk_id.
    Returns (repaired_matrix|None, findings, bad_chunks)."""
    mapping = {int(k): v for k, v in mapping.items()}
    scaled = set(scaled)
    if not failures:
        return None, [], []
    fail_rows = {}
    for f in failures:
        fail_rows.setdefault(f["row_index"], set()).add(f["relation"])
    bands = {}
    for r, cid in band_of_row.items():
        bands.setdefault(cid, []).append(r)

    findings, bad_chunks, repaired = [], [], None
    for cid, rows in bands.items():
        rows = sorted(rows)
        failing = [r for r in rows if r in fail_rows]
        others = [r for r in fail_rows if band_of_row.get(r) != cid]
        if len(rows) < min_band or len(failing) / len(rows) < frac:
            continue
        if len(others) > 0.2 * len(fail_rows):
            continue          # failures not band-shaped; not this pathology
        band = np.array(rows)

        # A corrupted band degrades IDENTIFICATION, not just certification:
        # the printed table may yield only a partial mapping. The clean
        # population defines the schema -- re-identify with the suspect band
        # held out, and sweep against that mapping. (The repair is still
        # only a hypothesis until the whole repaired table re-certifies.)
        sweep_map = mapping
        clean = np.array([r for r in band_of_row if r not in rows])
        if clean.size >= min_band:
            from .cc_validator import validate_cc
            from .wip_validator import validate_wip
            vfn = validate_cc if schema == "cc" else validate_wip
            vsub = vfn(matrix[clean])
            if vsub.mapping and len(vsub.mapping) > len(mapping):
                sweep_map = {int(k): v for k, v in vsub.mapping.items()}
        checks = _checks_for(sweep_map, schema)
        if not checks:
            bad_chunks.append(cid)
            continue

        bc, bp = _band_passes(matrix, band, sweep_map, checks)
        if bc and bp / bc >= 0.9:
            continue          # band passes under the full mapping; not shifted
        winners = []
        for s in _SHIFTS:
            for start in range(0, matrix.shape[1] - 1):
                cand, edge = _shift_band(matrix, band, s, start, scaled)
                nc, npass = _band_passes(cand, band, sweep_map, checks)
                if nc >= 3 and npass / nc >= 0.95:
                    winners.append((s, start, edge, npass / nc, nc, cand))
        if not winners:
            bad_chunks.append(cid)
            continue
        # A repair that VERIFIES more identities beats one that passes
        # vacuously by NaN-ing the informative columns away.
        best_nc = max(w[4] for w in winners)
        top = [w for w in winners if w[4] == best_nc]
        top.sort(key=lambda w: -w[3])
        top = [w for w in top if w[3] == top[0][3]]
        if len({w[0] for w in top}) > 1:
            bad_chunks.append(cid)
            findings.append({
                "kind": "block_misalignment_ambiguous", "chunk_id": cid,
                "note": "band-shaped failures with multiple shift readings; "
                        "re-extraction required"})
            continue
        s, start, edge, rate, nc, cand = top[0]
        repaired = cand if repaired is None else np.where(
            np.isfinite(cand), cand, repaired)
        bad_chunks.append(cid)     # unrecoverable column -> re-extract once
        findings.append({
            "kind": "block_misalignment", "chunk_id": cid,
            "rows": [int(r) for r in rows],
            "shift": int(s), "from_column": int(start),
            "unrecoverable_columns": [int(e) for e in edge],
            "pass_rate_after_repair": round(rate, 4),
            "note": f"every identity on this chunk fails as printed and "
                    f"passes with cells moved {'right' if s > 0 else 'left'} "
                    f"by {abs(s)} from column {start} on -- the chunk was "
                    "extracted with a column shift, not transcribed with "
                    "dozens of independent errors. Values shifted out of "
                    "frame are unrecoverable; re-extract this chunk to fill "
                    "them."})
    return repaired, findings, sorted(set(bad_chunks))
