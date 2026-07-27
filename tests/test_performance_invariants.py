from __future__ import annotations

import copy

import numpy as np

from wipple.accounting import validation as validation_mod
from wipple.accounting.validation import parse_node, validate_node
from wipple.accounting.wip import _detect_grid_cached, detect_grid
from wipple.core.model_client import Metrics
from wipple.pipeline.document import tables_node
from wipple.pipeline.graph import build_graph


def clean_raw_table(n_rows=12):
    headers = [
        "Job #", "Contract Value", "Estimated Cost", "Estimated GP",
        "Cost to Date", "Cost to Complete", "Percent Complete",
        "Earned Revenue", "Billings", "Underbillings", "Overbillings",
    ]
    rows = []
    totals = np.zeros(10)
    for i in range(n_rows):
        V = 1_000_000.0 + i * 37_000.0
        C = round(V * (0.78 + (i % 3) * 0.01), 2)
        G = round(V - C, 2)
        D = round(C * (0.25 + (i % 6) * 0.10), 2)
        Q = round(C - D, 2)
        P = D / C
        E = round(V * P, 2)
        B = round(E + (25_000 if i % 2 else -18_000), 2)
        U = max(E - B, 0.0)
        O = max(B - E, 0.0)
        values = [V, C, G, D, Q, P, E, B, U, O]
        totals += np.asarray(values)
        rows.append([f"J-{i + 1:03d}"] + [f"{v:.8f}" for v in values])
    # Preserve a realistic stated-total row. The percentage cell is not
    # additive, but enough money columns identify the row as an aggregate.
    rows.append(["TOTAL"] + [f"{v:.8f}" for v in totals])
    return {"headers": headers, "rows": rows}


def logical_table(raw):
    return {
        "title_texts": ["Work in Progress"],
        "headers": copy.deepcopy(raw["headers"]),
        "rows": copy.deepcopy(raw["rows"]),
        "row_prov": [[(0, 1, i)] for i in range(len(raw["rows"]))],
        "issues": [], "chunks": [0], "pages": [1],
        "joined_columns": False,
    }


def test_detect_grid_cache_is_exact_and_reuses_content():
    values = np.asarray([0.125, 0.250, 0.375, np.nan])
    _detect_grid_cached.cache_clear()
    expected = detect_grid(values)
    for _ in range(7):
        assert detect_grid(values.copy()) == expected
    info = _detect_grid_cached.cache_info()
    assert info.misses == 1
    assert info.hits == 7


def test_seeded_section_graph_is_report_identical():
    raw = clean_raw_table()
    base_state = {
        "raw_table": raw, "pdf_bytes": b"", "source_name": "clean.csv",
        "extraction_tier": "primary", "reextract_count": 1,
        "extraction_attempts": [], "_metrics": Metrics(),
    }
    ordinary = build_graph().invoke(base_state)["report"]

    parsed = parse_node({"raw_table": raw})
    validation = validate_node({**parsed, "raw_table": raw})["validation"]
    seeded = build_graph().invoke({
        **base_state, **parsed, "validation": validation,
        "_metrics": Metrics(),
    })["report"]

    assert seeded == ordinary


def test_unchanged_document_table_runs_one_schema_race(monkeypatch):
    raw = clean_raw_table()
    calls = 0
    original = validation_mod.run_schema_race

    def counted(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(validation_mod, "run_schema_race", counted)
    result = tables_node({
        "logical_tables": [logical_table(raw)],
        "failed_chunks": [], "source_name": "clean.csv",
        "model_override": None, "_metrics": Metrics(),
    })

    assert result["tables"][0]["sections"]
    assert calls == 1
