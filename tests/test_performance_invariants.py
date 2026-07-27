from __future__ import annotations

import copy

import numpy as np

from wipple.accounting import validation as validation_mod
from wipple.accounting.validation import parse_node, validate_node
from wipple.accounting.wip import (
    GRIDS,
    _detect_grid_cached,
    detect_grid,
)
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


def uncached_detect_grid(vals):
    values = np.asarray(vals, dtype=float)
    values = values[np.isfinite(values)]
    if values.size == 0:
        return None
    for grid in GRIDS:
        rounded = np.round(values / grid)
        if np.all(
            np.abs(values - rounded * grid)
            <= 1e-9 + 1e-9 * np.abs(values)
        ):
            return grid
    return None


def test_detect_grid_cache_is_exact_and_reuses_content():
    vectors = [
        np.asarray([0.125, 0.250, 0.375, np.nan]),
        np.asarray([0.1, 0.2, 0.3]),
        np.asarray([0.1234567, 0.2345678, np.inf]),
        np.asarray([np.nan, np.inf]),
    ]
    _detect_grid_cached.cache_clear()
    for values in vectors:
        expected = uncached_detect_grid(values)
        assert detect_grid(values) == expected
        for _ in range(3):
            assert detect_grid(values.copy()) == expected
    info = _detect_grid_cached.cache_info()
    # The all-nonfinite vector returns before entering the cache.
    assert info.misses == 3
    assert info.hits == 9


def test_real_validation_reuses_percent_grid_inputs():
    raw = clean_raw_table()
    parsed = parse_node({"raw_table": raw})
    _detect_grid_cached.cache_clear()
    validate_node({**parsed, "raw_table": raw})
    info = _detect_grid_cached.cache_info()
    assert info.misses > 0
    assert info.hits > info.misses


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



def test_decisive_wip_skips_cc(monkeypatch):
    raw = clean_raw_table()
    parsed = parse_node({"raw_table": raw})

    def forbidden_cc(*args, **kwargs):
        raise AssertionError("CC validator should not run for a certified WIP")

    monkeypatch.setattr(validation_mod, "validate_cc", forbidden_cc)
    chosen, race = validation_mod.run_schema_race(
        parsed["matrix"], parsed["job_labels"])

    assert chosen.status == "success"
    assert race["chosen"] == "wip"
    assert race["resolution"] == "wip_certified"
    assert race["cc"]["status"] == "skipped"


def test_decisive_wip_with_findings_still_skips_cc(monkeypatch):
    raw = clean_raw_table()
    # Estimated GP remains identifiable from the other eleven rows, then fails
    # strict certification on this planted bad cell.
    raw["rows"][3][3] = f"{float(raw['rows'][3][3]) + 10000:.8f}"
    parsed = parse_node({"raw_table": raw})

    def forbidden_cc(*args, **kwargs):
        raise AssertionError("CC validator should not run for a certified WIP")

    monkeypatch.setattr(validation_mod, "validate_cc", forbidden_cc)
    chosen, race = validation_mod.run_schema_race(
        parsed["matrix"], parsed["job_labels"])

    assert chosen.status == "validation_failed"
    assert chosen.findings
    assert race["chosen"] == "wip"
    assert race["cc"]["status"] == "skipped"


def test_insufficient_wip_runs_cc(monkeypatch):
    calls = 0
    original_cc = validation_mod.validate_cc

    def counted_cc(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original_cc(*args, **kwargs)

    monkeypatch.setattr(validation_mod, "validate_cc", counted_cc)
    # A pure completed-contract additive lattice has no progress/billing
    # evidence capable of certifying the WIP schema.
    rows = []
    for i in range(8):
        rt = 1_000_000 + i * 100_000
        kt = 800_000 + i * 75_000
        gt = rt - kt
        rp = 600_000 + i * 45_000
        rc = rt - rp
        kp = 480_000 + i * 35_000
        kc = kt - kp
        gp = rp - kp
        gc = rc - kc
        rows.append([rt, kt, gt, rp, rc, kp, kc, gp, gc])
    matrix = np.asarray(rows, dtype=float)

    chosen, race = validation_mod.run_schema_race(
        matrix, [f"CC-{i + 1}" for i in range(len(rows))])

    assert calls == 1
    assert race["cc"]["status"] != "skipped"
    assert race["chosen"] == "cc"
    assert chosen.mapping


def test_all_complete_wip_layout_keeps_cc_escape_hatch(monkeypatch):
    raw = clean_raw_table()
    raw["rows"] = raw["rows"][:-1]
    for row in raw["rows"]:
        value = float(row[1])
        cost = float(row[2])
        row[4] = f"{cost:.8f}"   # D = C
        row[5] = "0.00000000"   # Q = 0
        row[6] = "1.00000000"   # P = 100%
        row[7] = f"{value:.8f}"  # E = V
        row[8] = f"{value:.8f}"  # B = V
        row[9] = "0.00000000"   # U = 0
        row[10] = "0.00000000"  # O = 0
    parsed = parse_node({"raw_table": raw})
    calls = 0
    original_cc = validation_mod.validate_cc

    def counted_cc(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original_cc(*args, **kwargs)

    monkeypatch.setattr(validation_mod, "validate_cc", counted_cc)
    _, race = validation_mod.run_schema_race(
        parsed["matrix"], parsed["job_labels"])

    assert calls == 1
    assert race["cc"]["status"] != "skipped"
