"""
Splitter: the inverse operator to the stitcher, and the stitcher's safety
net. An over-merged table (completed contracts appended to the WIP in the
same columns -- the layout that caused real intermingling bugs) is detected
by MATH, not labels: a completed contract in WIP columns is exactly
degenerate (E=V, D=C, Q=0, P=1, U=O=0), and k exact identities agreeing at
dollar precision is a different beast from one fuzzy "percent looks high".

Rules:
  * a contiguous terminal block of >= 2 all-degenerate rows splits off as a
    CC section (CPAs print completed contracts after in-progress ones);
  * a LONE degenerate row stays in the WIP and emits a soft finding --
    "complete but unclosed" is an underwriting observation, not a section;
  * the split is provenance-preserving: each section keeps its slice of
    row_prov, so page citations survive the cut.

Segmentation runs BEFORE per-section validation is trusted, so the validator
never sees a mixed population -- deleting the bug category rather than
patching its instances.
"""

from __future__ import annotations

import numpy as np

from .analysis import reconstruct_core
from .schemas import degenerate_wip_rows


def find_cc_block(matrix, mapping: dict) -> dict:
    """Returns {'split_at': i or None, 'lone_rows': [i], 'hits': per-row}."""
    core, _ = reconstruct_core(matrix, {int(k): v for k, v in mapping.items()})
    if core is None:
        return {"split_at": None, "lone_rows": [], "hits": []}
    if "Q" not in core and "C" in core and "D" in core:
        core = {**core, "Q": core["C"] - core["D"]}
    hits = degenerate_wip_rows(core)
    n = len(hits)
    is_deg = [h >= 3 and h == a for (h, a) in hits]

    split_at = None
    i = n
    while i > 0 and is_deg[i - 1]:
        i -= 1
    if n - i >= 2:
        split_at = i
    lone = [r for r in range(n) if is_deg[r]
            and (split_at is None or r < split_at)]
    return {"split_at": split_at, "lone_rows": lone,
            "hits": [list(h) for h in hits]}


def split_sections(raw_rows, headers, row_prov, parse_row_index, seg) -> list:
    """Cut the LOGICAL table's raw string rows into sections using the
    matrix-row split point, mapped back through the parse row_index so
    stripped totals/fences land with the section they belong to."""
    if seg["split_at"] is None:
        return [{"type": "wip", "rows": raw_rows, "headers": headers,
                 "row_prov": row_prov}]
    cut_raw = parse_row_index[seg["split_at"]]   # first raw row of CC block
    return [
        {"type": "wip", "rows": raw_rows[:cut_raw], "headers": headers,
         "row_prov": row_prov[:cut_raw]},
        {"type": "cc", "rows": raw_rows[cut_raw:], "headers": headers,
         "row_prov": row_prov[cut_raw:],
         "note": "completed-contract block split from a consolidated "
                 "schedule by exact degeneracy (E=V, D=C, Q=0, P=1)"},
    ]
