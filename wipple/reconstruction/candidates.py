"""Reconstruct logical tables from page-level table fragments.

The vision model returns grids. This module derives their array shape and
constructs only page-order assemblies that are mechanically possible:

* separate table
* vertical continuation (same columns, more rows)
* horizontal continuation (same rows, more columns)

It does not classify WIP/CC, interpret headers, inspect pixel coordinates, or use
label-density/signature rules to decide continuation. Repeated-header cleanup,
exact duplicate label-column cleanup, and explicit image-strip overlap dedup are
deterministic normalization after a candidate join has already been constructed.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Iterable


Table = dict
Layout = list[Table]


def _strings(values: Iterable) -> list[str]:
    return ["" if value is None else str(value) for value in values]


def _shape(headers: list, rows: list[list]) -> tuple[int, int]:
    """Return grid shape in code; never ask the model to count."""
    n_rows = len(rows)
    n_cols = max([len(headers), *(len(row) for row in rows)], default=0)
    return n_rows, n_cols


def _pad(row: list[str], width: int) -> list[str]:
    return row[:width] + [""] * max(0, width - len(row))


def normalize_fragment(fragment: dict, ordinal: int = 0) -> Table:
    """Normalize one extractor grid and attach derived provenance/shape."""
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
    title_texts = fragment.get("title_texts")
    if title_texts is None:
        title = fragment.get("title_text")
        title_texts = [] if not title else [str(title)]
    title_texts = list(dict.fromkeys(
        str(title) for title in title_texts if str(title).strip()))

    return {
        "title_texts": title_texts,
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
        "overlaps_prev": bool(fragment.get("overlaps_prev", False)),
    }


def table_shape(table: Table) -> tuple[int, int]:
    """Return shape from the grid itself, refreshing cached fields."""
    n_rows, n_cols = _shape(table.get("headers") or [], table.get("rows") or [])
    table["n_rows"], table["n_cols"] = n_rows, n_cols
    return n_rows, n_cols


def _page_adjacent(left: Table, right: Table) -> bool:
    """Only document-order neighbors may be continuations."""
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


def _is_header_repeat(row: list[str], headers: list[str]) -> bool:
    """Exact printed-header repetition cleanup, not semantic interpretation."""
    nonblank = sum(1 for header in headers if str(header).strip())
    if nonblank == 0:
        return False
    hits = sum(
        1 for cell, header in zip(row, headers)
        if str(cell).strip()
        and str(cell).strip().casefold() == str(header).strip().casefold()
    )
    return hits >= max(3, 0.6 * nonblank)


def _row_key(row: list[str]) -> str:
    """First printed nonblank cell, used only inside declared strip overlap."""
    return next((str(cell).strip() for cell in row if str(cell).strip()), "")


def _dedup_declared_overlap(
    left_rows: list[list[str]],
    right_rows: list[list[str]],
    right_prov: list[list[tuple]],
    issues: list[dict],
) -> tuple[list[list[str]], list[list[tuple]]]:
    """Remove duplicated physical rows from explicitly overlapping image strips."""
    max_k = min(len(left_rows), len(right_rows), 12)
    for k in range(max_k, 0, -1):
        tail = left_rows[-k:]
        head = right_rows[:k]
        if tail == head:
            return right_rows[k:], right_prov[k:]

        tail_keys = [_row_key(row) for row in tail]
        head_keys = [_row_key(row) for row in head]
        if not all(a and a == b for a, b in zip(tail_keys, head_keys)):
            continue

        for old, new, prov, key in zip(tail, head, right_prov[:k], tail_keys):
            if old == new:
                continue
            width = max(len(old), len(new))
            differing = [
                index for index in range(width)
                if (old[index] if index < len(old) else "")
                != (new[index] if index < len(new) else "")
            ]
            first = prov[0] if prov else (None, None, None)
            issues.append({
                "kind": "overlap_mismatch",
                "chunk_id": first[0],
                "page": first[1],
                "row_label": key,
                "columns": differing,
                "note": "same physical row was extracted twice with different "
                        "values in overlapping image strips",
            })
        return right_rows[k:], right_prov[k:]

    return right_rows, right_prov


def join_vertical(left: Table, right: Table) -> Table:
    if not can_join_vertically(left, right):
        raise ValueError("tables are not mechanically compatible vertically")

    headers = (left["headers"] if not _header_is_blank(left["headers"])
               else right["headers"])
    issues = deepcopy(left["issues"]) + deepcopy(right["issues"])
    right_rows, right_prov = [], []
    for row, prov in zip(deepcopy(right["rows"]),
                         deepcopy(right["row_prov"])):
        if _is_header_repeat(row, headers):
            continue
        right_rows.append(row)
        right_prov.append(prov)

    if right.get("overlaps_prev"):
        right_rows, right_prov = _dedup_declared_overlap(
            left["rows"], right_rows, right_prov, issues)

    result = {
        "title_texts": list(dict.fromkeys(
            list(left.get("title_texts") or [])
            + list(right.get("title_texts") or []))),
        "headers": list(headers),
        "rows": deepcopy(left["rows"]) + right_rows,
        "row_prov": deepcopy(left["row_prov"]) + right_prov,
        "pages": sorted(set(left["pages"]) | set(right["pages"])),
        "chunks": sorted(set(left["chunks"]) | set(right["chunks"])),
        "issues": issues,
        "source_fragments": (list(left["source_fragments"])
                             + list(right["source_fragments"])),
        "assembly": (list(left["assembly"]) + list(right["assembly"])
                     + [{"op": "vertical",
                         "left_pages": list(left["pages"]),
                         "right_pages": list(right["pages"])}]),
        "joined_columns": bool(left.get("joined_columns")
                               or right.get("joined_columns")),
        "overlaps_prev": False,
    }
    result["n_rows"], result["n_cols"] = _shape(result["headers"], result["rows"])
    return result


def can_recover_headers_as_row(left: Table, right: Table) -> bool:
    """Whether a vertical candidate can also test a promoted first data row.

    Continuation pages sometimes print no header and the reader promotes their
    first job into the header array. We do not guess whether that happened from
    header wording or numeric-density thresholds. When the grids are otherwise
    vertically compatible and the returned headers differ, layout validation
    may compare both literal and recovered interpretations.
    """
    if not can_join_vertically(left, right):
        return False
    if _header_is_blank(right.get("headers") or []):
        return False
    return list(left.get("headers") or []) != list(right.get("headers") or [])


def join_vertical_recovering_headers(left: Table, right: Table) -> Table:
    """Treat the right fragment's header array as its first printed data row."""
    if not can_recover_headers_as_row(left, right):
        raise ValueError("header-row recovery is not mechanically available")

    recovered = deepcopy(right)
    chunk = (right.get("chunks") or [None])[0]
    page = (right.get("pages") or [None])[0]
    recovered["rows"] = [list(right["headers"])] + deepcopy(right["rows"])
    recovered["row_prov"] = [[(chunk, page, -1)]] + deepcopy(
        right["row_prov"])
    recovered["headers"] = [""] * int(right["n_cols"])
    recovered["n_rows"] = len(recovered["rows"])
    recovered["issues"] = deepcopy(recovered["issues"]) + [{
        "kind": "promoted_data_row_recovered",
        "chunk_id": chunk,
        "page": page,
        "note": "the continuation page's returned header array validated as "
                "its first data row and was restored",
    }]
    result = join_vertical(left, recovered)
    result["assembly"].append({
        "op": "recover_headers_as_row",
        "right_pages": list(right["pages"]),
    })
    return result


def _duplicate_right_columns(left: Table, right: Table) -> set[int]:
    """Find exact repeated columns in a horizontal continuation.

    A column is removed only when its printed header matches a left header and
    every cell matches row-for-row. This safely removes a repeated job-name/ID
    column without guessing from text density or accounting meaning.
    """
    drop: set[int] = set()
    for rj, right_header in enumerate(right["headers"]):
        rh = str(right_header).strip().casefold()
        if not rh:
            continue
        for lj, left_header in enumerate(left["headers"]):
            if rh != str(left_header).strip().casefold():
                continue
            if all(
                str(left["rows"][i][lj]).strip()
                == str(right["rows"][i][rj]).strip()
                for i in range(len(left["rows"]))
            ):
                drop.add(rj)
                break
    return drop


def join_horizontal(left: Table, right: Table) -> Table:
    if not can_join_horizontally(left, right):
        raise ValueError("tables are not mechanically compatible horizontally")

    drop = _duplicate_right_columns(left, right)
    right_columns = [j for j in range(len(right["headers"])) if j not in drop]
    rows = [list(lrow) + [rrow[j] for j in right_columns]
            for lrow, rrow in zip(left["rows"], right["rows"])]
    provenance = [list(lp) + list(rp)
                  for lp, rp in zip(left["row_prov"], right["row_prov"])]
    result = {
        "title_texts": list(dict.fromkeys(
            list(left.get("title_texts") or [])
            + list(right.get("title_texts") or []))),
        "headers": (list(left["headers"])
                    + [right["headers"][j] for j in right_columns]),
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
                         "right_pages": list(right["pages"]),
                         "duplicate_columns_removed": sorted(drop)}]),
        "joined_columns": True,
        "overlaps_prev": False,
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
    """Allow the newest adjacent logical blocks to collapse repeatedly."""
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
    """Legacy test helper; the runtime uses local pairwise assembly."""
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
            layouts = layouts[:max_candidates]
    return layouts
