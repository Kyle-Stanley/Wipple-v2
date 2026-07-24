"""Page-level table fragments -> reconstructed logical tables.

The vision model supplies grids only. Array shape determines which adjacent-page
joins are mechanically possible. Existing WIP/CC validators compare the viable
layouts. No label-overlap, numeric-density, header-semantic, or pixel-geometry
heuristics decide continuation.
"""

from __future__ import annotations

from .layout_validation import select_layout
from .reconstruction import enumerate_layouts, normalize_fragment


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


def assemble(fragments: list[dict]) -> list[dict]:
    """Reconstruct logical tables from page-reader grids.

    Shape first eliminates impossible joins. Validator math chooses among the
    surviving page-order layouts. If accounting evidence ties exactly, the
    layout with fewer logical tables wins because each join was already proven
    mechanically viable. If ambiguity remains even after that narrow rule, the
    safe output preserves page fragments separately rather than inventing a
    continuation.
    """
    if not fragments:
        return []

    layouts = enumerate_layouts(fragments)
    decision = select_layout(layouts)
    if decision.get("status") == "selected" and decision.get("layout"):
        selected = decision["layout"]
    else:
        selected = [normalize_fragment(fragment, ordinal)
                    for ordinal, fragment in enumerate(fragments)]
        selected.sort(key=lambda table: (
            min(table.get("pages") or [1]),
            (table.get("source_fragments") or [(1, 0)])[0][1],
        ))
        for table in selected:
            table.setdefault("issues", []).append({
                "kind": "layout_ambiguous",
                "note": "multiple mechanically viable layouts remained tied; "
                        "page fragments were preserved separately",
            })

    return [_public_table(table) for table in selected]
