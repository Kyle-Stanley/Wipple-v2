"""Page-level table fragments -> reconstructed logical tables.

The vision model supplies grids only. Array shape determines which adjacent-page
joins are mechanically possible. For each neighboring pair, existing WIP/CC math
compares at most three layouts: keep separate, join vertically, join horizontally.
A successful join collapses on a stack and may then join the preceding block.

Image strips are the one explicit structural constraint: the chunker already knows
they are overlapping slices of one image, so a shape-compatible vertical join is
performed directly and duplicate physical rows are removed deterministically.

No document-wide layout search, label-overlap continuation thresholds, numeric-
density rules, header semantics, or pixel geometry decide PDF page assembly.
"""

from __future__ import annotations

from .layout_validation import select_layout
from .reconstruction import (
    can_join_horizontally,
    can_join_vertically,
    join_horizontal,
    join_vertical,
    normalize_fragment,
)


def _public_table(table: dict) -> dict:
    """Return the logical-table contract consumed by the document graph."""
    return {
        "headers": list(table.get("headers") or []),
        "rows": [list(row) for row in (table.get("rows") or [])],
        "row_prov": [list(item) for item in (table.get("row_prov") or [])],
        "issues": list(table.get("issues") or []),
        "chunks": list(table.get("chunks") or []),
        "pages": list(table.get("pages") or []),
        "joined_columns": bool(table.get("joined_columns", False)),
    }


def _pair_candidates(left: dict, right: dict) -> list[list[dict]]:
    """Return only mechanically viable interpretations of one boundary."""
    candidates = [[left, right]]
    if can_join_vertically(left, right):
        candidates.append([join_vertical(left, right)])
    if can_join_horizontally(left, right):
        candidates.append([join_horizontal(left, right)])
    return candidates


def _reduce_tail(stack: list[dict]) -> None:
    """Collapse the newest pair while structure/math supports a join."""
    while len(stack) >= 2:
        left, right = stack[-2], stack[-1]

        # Overlapping image strips are not an inferred page relationship. The
        # chunker explicitly created them from one image and marked the overlap.
        if right.get("overlaps_prev") and can_join_vertically(left, right):
            stack[-2:] = [join_vertical(left, right)]
            continue

        candidates = _pair_candidates(left, right)
        if len(candidates) == 1:
            return

        decision = select_layout(candidates)
        selected = decision.get("layout")
        if decision.get("status") == "selected" and selected:
            if len(selected) == 1:
                stack[-2:] = selected
                continue
            return

        # Two joins can occasionally remain mathematically indistinguishable.
        # Preserve the boundary rather than inventing a direction.
        right.setdefault("issues", []).append({
            "kind": "layout_ambiguous",
            "note": "vertical and horizontal page joins remained tied; "
                    "the page boundary was preserved",
        })
        return


def assemble(fragments: list[dict]) -> list[dict]:
    """Reconstruct logical tables with local shape + validator decisions."""
    normalized = [normalize_fragment(fragment, ordinal)
                  for ordinal, fragment in enumerate(fragments)]
    normalized.sort(key=lambda table: (
        min(table.get("pages") or [1]),
        (table.get("source_fragments") or [(1, 0)])[0][1],
    ))

    stack: list[dict] = []
    for fragment in normalized:
        stack.append(fragment)
        _reduce_tail(stack)

    return [_public_table(table) for table in stack]
