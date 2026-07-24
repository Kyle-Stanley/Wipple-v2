"""Table reconstruction tests: grids first, accounting semantics later."""

from wipple.reconstruction import (
    can_join_horizontally,
    can_join_vertically,
    enumerate_layouts,
    join_horizontal,
    join_vertical,
    normalize_fragment,
)


def fragment(page, rows, cols, table_index=0, headers=True):
    return {
        "chunk_id": page - 1,
        "pages": [page],
        "table_index": table_index,
        "headers": ([f"h{j}" for j in range(cols)]
                    if headers else ["" for _ in range(cols)]),
        "rows": [[f"p{page}r{i}c{j}" for j in range(cols)]
                 for i in range(rows)],
    }


def shapes(layout):
    return [(table["n_rows"], table["n_cols"]) for table in layout]


def operations(layout):
    return [[item["op"] for item in table["assembly"]] for table in layout]


def test_shape_is_derived_from_returned_grid():
    table = normalize_fragment({
        "pages": [1],
        "headers": ["a", "b"],
        "rows": [["1", "2", "3"], ["4"]],
    })
    assert (table["n_rows"], table["n_cols"]) == (2, 3)
    assert table["headers"] == ["a", "b", ""]
    assert table["rows"][1] == ["4", "", ""]


def test_incompatible_shapes_cannot_be_combined():
    a = normalize_fragment(fragment(1, 10, 10))
    b = normalize_fragment(fragment(2, 4, 6))
    assert not can_join_vertically(a, b)
    assert not can_join_horizontally(a, b)


def test_vertical_is_possible_for_same_columns():
    a = normalize_fragment(fragment(1, 20, 12))
    b = normalize_fragment(fragment(2, 14, 12, headers=False))
    assert can_join_vertically(a, b)
    assert not can_join_horizontally(a, b)
    joined = join_vertical(a, b)
    assert (joined["n_rows"], joined["n_cols"]) == (34, 12)
    assert joined["headers"] == a["headers"]


def test_horizontal_is_possible_for_same_rows():
    a = normalize_fragment(fragment(1, 22, 8))
    b = normalize_fragment(fragment(2, 22, 7, headers=False))
    assert can_join_horizontally(a, b)
    assert not can_join_vertically(a, b)
    joined = join_horizontal(a, b)
    assert (joined["n_rows"], joined["n_cols"]) == (22, 15)
    assert all(len(prov) == 2 for prov in joined["row_prov"])


def test_equal_shapes_preserve_both_real_interpretations():
    layouts = enumerate_layouts([
        fragment(1, 18, 12),
        fragment(2, 18, 12),
    ])
    seen = {tuple(shapes(layout)) for layout in layouts}
    assert ((36, 12),) in seen       # long schedule
    assert ((18, 24),) in seen       # wide schedule
    assert ((18, 12), (18, 12)) in seen  # two independent tables


def test_two_tables_on_same_page_are_not_automatically_joined():
    layouts = enumerate_layouts([
        fragment(1, 10, 8, table_index=0),
        fragment(1, 10, 8, table_index=1),
    ])
    assert len(layouts) == 1
    assert shapes(layouts[0]) == [(10, 8), (10, 8)]


def test_four_page_wide_then_long_layout_is_generated():
    layouts = enumerate_layouts([
        fragment(1, 20, 8),
        fragment(2, 20, 7, headers=False),
        fragment(3, 18, 8),
        fragment(4, 18, 7, headers=False),
    ])
    matches = [layout for layout in layouts if shapes(layout) == [(38, 15)]]
    assert matches
    assert operations(matches[0])[0].count("horizontal") == 2
    assert operations(matches[0])[0].count("vertical") == 1


def test_consecutive_schedule_groups_can_reconstruct_as_two_tables():
    """Pages 1-2 may be one WIP while pages 3-4 are one CC schedule.

    Reconstruction does not need those labels. It must preserve the candidate
    consisting of two independently continued logical tables so the validator
    layer can later identify the first as WIP and the second as CC.
    """
    layouts = enumerate_layouts([
        fragment(1, 18, 12),
        fragment(2, 14, 12, headers=False),
        fragment(3, 16, 9),
        fragment(4, 11, 9, headers=False),
    ])
    matches = [layout for layout in layouts
               if shapes(layout) == [(32, 12), (27, 9)]]
    assert matches
    assert operations(matches[0]) == [["vertical"], ["vertical"]]


def test_nonadjacent_pages_never_secretly_reassemble():
    layouts = enumerate_layouts([
        fragment(1, 12, 8),
        fragment(4, 12, 8),
    ])
    assert len(layouts) == 1
    assert shapes(layouts[0]) == [(12, 8), (12, 8)]
