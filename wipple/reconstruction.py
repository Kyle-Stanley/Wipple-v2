"""Reconstruct logical tables from page-level table fragments.

This module is intentionally ignorant of accounting semantics.  The vision
model returns grids; this layer derives their shape and enumerates only the
page-order assemblies that are mechanically possible:

* separate table
* vertical continuation (same columns, more rows)
* horizontal continuation (same rows, more columns)

It does not classify WIP/CC, read headers semantically, inspect pixel geometry,
or decide which plausible layout is correct.  A caller supplies the cheap
validator-backed scoring function after candidates have been generated.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Callable, Iterable


Table = dict
Layout = list[Table]


def _strings(values: Iterable) -> list[str]:
    return ["" if value is None else str(value) for value in values]


def _shape(headers: list, rows: list[list]) -> tuple[int, int]:
    """Return the deterministic grid shape; never ask the model to count."""
    n_rows = len(rows)
    n_cols = max([len(headers), *(len(row) for row in rows)], default=0)
    return n_rows, n_cols


def _pad(row: list[str], width: int) -> list[str]:
    return row[:width] + [""] * max(0, width - len(row))


def normalize_fragment(fragment: dict, ordinal: int = 0) -> Table:
    """Normalize one extractor grid and attach derived, non-semantic metadata."""
    headers = _strings(fragment.get("headers") or [])
    rows = [_strings(row) for row in (fragment.get("rows") or [])]
    n_rows, n_cols = _shape(headers, rows)
    headers = _pad(headers, n_cols)
    rows = [_pad(row, n_cols) for row in rows]

    pages = fragment.get("pages") or [fragment.get("page", 1)]
    pages = sorted({int(page) for page in pages if page is not None}) or [1]
    chunks = fragment.get("chunks")
    if chunks is None:
        chunk = fragment.get("chunk_id")
        chunks = [] if chunk is None else [chunk]

    provenance = fragment.get("row_prov") or fragment.get("prov")
    if provenance is None:
        chunk = fragment.get("chunk_id", pages[0] - 1)
        provenance = [[(chunk, pages[0], row_index)]
                      for row_index in range(n_rows)]

    table_index = fragment.get("table_index", fragment.get("position", ordinal))
    source_id = (pages[0], int(table_index))

    return {
        "headers": headers,
        "rows": rows,
        "row_prov": [list(item) for item in provenance],
        "pages": pages,
        "chunks": sorted({int(chunk) for chunk in chunks}),
        "issues": list(fragment.get("issues") or []),
        "source_fragments": [source_id],
        "assembly": [],
        "n_rows": n_rows,
        "n_cols": n_cols,
        "joined_columns": bool(fragment.get("joined_columns", False)),
    }


def table_shape(table: Table) -> tuple[int, int]:
    """Return shape from the grid itself, refreshing cached fields if needed."""
    n_rows, n_cols = _shape(table.get("headers") or [], table.get("rows") or [])
    table["n_rows"], table["n_cols"] = n_rows, n_cols
    return n_rows, n_cols


def _page_adjacent(left: Table, right: Table) -> bool:
    """Only document-order neighbors may be continuations.

    Distinct tables emitted from the same page stay distinct: the table reader
    has already done the visual task of saying "there are two tables here".
    Shuffled or distant pages are outside the reconstruction contract.
    """
    return max(left["pages"]) + 1 == min(right["pages"])


def can_join_vertically(left: Table, right: Table) -> bool:
    """Same column shape, adjacent pages: possibly more rows of one table."""
    left_rows, left_cols = table_shape(left)
    right_rows, right_cols = table_shape(right)
    return (left_rows > 0 and right_rows > 0 and left_cols > 0
            and left_cols == right_cols and _page_adjacent(left, right))


def can_join_horizontally(left: Table, right: Table) -> bool:
    """Same row shape, adjacent pages: possibly more columns of one table."""
    left_rows, left_cols = table_shape(left)
    right_rows, right_cols = table_shape(right)
    return (left_rows > 0 and left_rows == right_rows
            and left_cols > 0 and right_cols > 0
            and _page_adjacent(left, right))


def _header_is_blank(headers: list[str]) -> bool:
    return not any(str(value).strip() for value in headers)


def join_vertical(left: Table, right: Table) -> Table:
    if not can_join_vertically(left, right):
        raise ValueError("tables are not mechanically compatible vertically")

    headers = (left["headers"] if not _header_is_blank(left["headers"])
               else right["headers"])
    result = {
        "headers": list(headers),
        "rows": deepcopy(left["rows"]) + deepcopy(right["rows"]),
        "row_prov": deepcopy(left["row_prov"]) + deepcopy(right["row_prov"]),
        "pages": sorted(set(left["pages"]) | set(right["pages"])),
        "chunks": sorted(set(left["chunks"]) | set(right["chunks"])),
        "issues": deepcopy(left["issues"]) + deepcopy(right["issues"]),
        "source_fragments": (list(left["source_fragments"])
                             + list(right["source_fragments"])),
        "assembly": (list(left["assembly"]) + list(right["assembly"])
                     + [{"op": "vertical",
                         "left_pages": list(left["pages"]),
                         "right_pages": list(right["pages"])}]),
        "joined_columns": bool(left.get("joined_columns")
                               or right.get("joined_columns")),
    }
    result["n_rows"], result["n_cols"] = _shape(result["headers"], result["rows"])
    return result


def join_horizontal(left: Table, right: Table) -> Table:
    if not can_join_horizontally(left, right):
        raise ValueError("tables are not mechanically compatible horizontally")

    rows = [list(lrow) + list(rrow)
            for lrow, rrow in zip(left["rows"], right["rows"])]
    provenance = [list(lp) + list(rp)
                  for lp, rp in zip(left["row_prov"], right["row_prov"])]
    result = {
        "headers": list(left["headers"]) + list(right["headers"]),
        "rows": rows,
        "row_prov": provenance,
        "pages": sorted(set(left["pages"]) | set(right["pages"])),
        "chunks": sorted(set(left["chunks"]) | set(right["chunks"])),
        "issues": deepcopy(left["issues"]) + deepcopy(right["issues"]),
        "source_fragments": (list(left["source_fragments"])
                             + list(right["source_fragments"])),
        "assembly": (list(left["assembly"]) + list(right["assembly"])
                     + [{"op": "horizontal",
                         "left_pages": list(left["pages"]),
                         "right_pages": list(right["pages"])}]),
        "joined_columns": True,
    }
    result["n_rows"], result["n_cols"] = _shape(result["headers"], result["rows"])
    return result


def _table_fingerprint(table: Table) -> tuple:
    return (tuple(table.get("source_fragments") or []),
            tuple(item.get("op") for item in table.get("assembly") or []),
            table.get("n_rows"), table.get("n_cols"))


def _layout_fingerprint(layout: Layout) -> tuple:
    return tuple(_table_fingerprint(table) for table in layout)


def _dedupe(layouts: Iterable[Layout]) -> list[Layout]:
    out, seen = [], set()
    for layout in layouts:
        key = _layout_fingerprint(layout)
        if key in seen:
            continue
        seen.add(key)
        out.append(layout)
    return out


def _closure_variants(layout: Layout) -> list[Layout]:
    """After a local join, allow the two newest logical blocks to collapse.

    This is what permits the ordinary four-page pattern:
      horizontal(1,2), horizontal(3,4), then vertical(the two wide blocks).
    Only the final adjacent blocks are considered; page order is never permuted.
    """
    out, queue, seen = [], [layout], set()
    while queue:
        current = queue.pop()
        key = _layout_fingerprint(current)
        if key in seen:
            continue
        seen.add(key)
        out.append(current)
        if len(current) < 2:
            continue
        left, right = current[-2], current[-1]
        prefix = current[:-2]
        if can_join_vertically(left, right):
            queue.append(prefix + [join_vertical(left, right)])
        if can_join_horizontally(left, right):
            queue.append(prefix + [join_horizontal(left, right)])
    return out


def enumerate_layouts(fragments: list[dict], max_candidates: int = 256) -> list[Layout]:
    """Enumerate mechanically plausible document-order table assemblies.

    No candidate is called WIP or CC here.  No validator runs here.  When shape
    leaves more than one real interpretation, the caller evaluates these layouts
    with the cheap accounting math and chooses the coherent one.
    """
    normalized = [normalize_fragment(fragment, ordinal)
                  for ordinal, fragment in enumerate(fragments)]
    normalized.sort(key=lambda table: (min(table["pages"]),
                                       table["source_fragments"][0][1]))
    if not normalized:
        return []

    layouts: list[Layout] = [[normalized[0]]]
    for fragment in normalized[1:]:
        next_layouts = []
        for layout in layouts:
            # Separate is always legal.  It is essential for multiple tables on
            # one page and for unsupported future financial-statement sections.
            next_layouts.extend(_closure_variants(layout + [fragment]))

            last = layout[-1]
            if can_join_vertically(last, fragment):
                next_layouts.extend(_closure_variants(
                    layout[:-1] + [join_vertical(last, fragment)]))
            if can_join_horizontally(last, fragment):
                next_layouts.extend(_closure_variants(
                    layout[:-1] + [join_horizontal(last, fragment)]))

        layouts = _dedupe(next_layouts)
        if len(layouts) > max_candidates:
            # This is a hard safety bound, not a semantic ranking.  Normal
            # financial documents generate only a handful of candidates because
            # incompatible shapes eliminate almost every branch immediately.
            layouts = layouts[:max_candidates]
    return layouts


def choose_layout(layouts: list[Layout],
                  evaluator: Callable[[Layout], float]) -> Layout | None:
    """Choose using a caller-supplied validator-backed evaluator.

    Keeping the evaluator outside this module prevents subjective continuation
    heuristics or schema-specific policy from leaking into table reconstruction.
    """
    if not layouts:
        return None
    return max(layouts, key=evaluator)
