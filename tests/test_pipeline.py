"""End-to-end graph tests with a fake model client. No API keys needed."""

import json
import sys
sys.path.insert(0, ".")
sys.path.insert(0, "tests")

import numpy as np
import pytest

import wipple.extraction as extraction
import wipple.fallback as fallback
from wipple.graph import build_graph
from wipple.model_client import Metrics
from wipple.parsing import parse_cell, parse_table
from synth import raw_table, rows_numeric


class FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def generate(self, prompt, tier="primary", pdf_bytes=None, json_only=True,
                 max_tokens=16384, metrics=None, purpose="", **kw):
        self.calls.append({"tier": tier, "purpose": purpose})
        return self.responses.pop(0)


@pytest.fixture
def patch_client(monkeypatch):
    def _patch(responses):
        fake = FakeClient(responses)
        monkeypatch.setattr(extraction, "get_client", lambda: fake)
        monkeypatch.setattr(fallback, "get_client", lambda: fake)
        return fake
    return _patch


def invoke(graph):
    return graph.invoke({
        "pdf_bytes": b"%PDF-fake", "source_name": "synthetic.pdf",
        "extraction_tier": "primary", "reextract_count": 0,
        "extraction_attempts": [], "_metrics": Metrics(),
    })


# ---------------------------------------------------------------- parse unit

def test_parse_cells():
    assert parse_cell("1,234.56")[0] == 1234.56
    assert parse_cell("(45,000)")[0] == -45000
    assert parse_cell("$2,000")[0] == 2000
    assert parse_cell("-")[0] == 0.0
    v, fl = parse_cell("")
    assert v == 0.0 and "blank_as_zero" in fl
    v, fl = parse_cell("1,2O4")          # confusable O -> 0
    assert v == 1204 and "confusable_repair" in fl
    v, fl = parse_cell("garbage!!")
    assert np.isnan(v) and "unparseable" in fl
    assert parse_cell("1.234.567,89", "eu")[0] == 1234567.89
    v, fl = parse_cell("45.2%")
    assert v == 45.2 and "pct_glyph" in fl


def test_parse_table_structure():
    rt = raw_table()
    res = parse_table(rt["rows"], headers=rt["headers"])
    assert res.matrix is not None
    assert res.matrix.shape == (8, 10)               # totals stripped
    assert res.job_labels[0] == "J-101"
    assert len(res.stripped_total_rows) == 1
    assert res.totals_check is not None and res.totals_check["all_match"]
    # percent column scaled to fractions
    pcol = res.numeric_col_map.index(6)
    assert abs(res.matrix[0, pcol] - 0.10) < 1e-9


def test_parse_totals_mismatch_detected():
    rt = raw_table()
    rt["rows"][-1][1] = "9,999,999"                  # corrupt stated total V
    res = parse_table(rt["rows"], headers=rt["headers"])
    assert not res.totals_check["all_match"]


# ------------------------------------------------------------- e2e: success

def test_e2e_success(patch_client):
    patch_client([json.dumps(raw_table())])
    final = invoke(build_graph())
    rep = final["report"]
    assert rep["validator_status"] == "success"
    assert rep["overall_status"] == "verified"
    by_var = {c["variable"]: c for c in rep["columns"] if c["variable"]}
    # spot-check semantic placements (matrix col order == doc numeric order)
    assert by_var["V"]["col"] == 0
    assert by_var["D"]["col"] == 3
    assert by_var["B"]["col"] == 7
    assert by_var["E"]["col"] == 6
    provs = {c["provenance"] for c in rep["columns"]}
    assert provs <= {"math-verified", "math-identified", "virtual",
                     "unassigned"}
    assert rep["totals_check"]["all_match"]


# ---------------------------------------------- e2e: corrupt -> re-extract

def test_e2e_reextract_recovers(patch_client):
    # First extraction has a digit transposition in one Costs to Date cell;
    # escalated re-extraction returns the clean table.
    bad = raw_table(corrupt={(4, "Costs to Date"): "800,000"})  # true 800,000? no:
    # J-105: C=1,600,000 P=.5 -> D=800,000. Use transposition 080... invalid.
    bad = raw_table(corrupt={(3, "Costs to Date"): "210,000"})  # true 120,000
    fake = patch_client([json.dumps(bad), json.dumps(raw_table())])
    final = invoke(build_graph())
    rep = final["report"]
    assert rep["reextract_count"] == 1
    assert [c["tier"] for c in fake.calls] == ["primary", "escalated"]
    assert rep["validator_status"] == "success"
    assert rep["overall_status"] == "verified"


def test_e2e_failed_emits_finding_when_not_ocr_shaped(patch_client):
    # Flatly wrong value (no OCR pattern): should NOT burn the re-extract,
    # should emit as an underwriting finding.
    bad = raw_table(corrupt={(2, "Revenues Earned"): "287,451"})  # true 300,000
    fake = patch_client([json.dumps(bad), json.dumps(raw_table())])
    final = invoke(build_graph())
    rep = final["report"]
    assert rep["reextract_count"] == 0          # retry NOT burned
    assert rep["overall_status"] == "verified_mapping_with_findings"
    assert len(fake.calls) == 1
    f0 = rep["findings"][0]
    assert f0["classification"] == "unexplained_substitution"
    assert f0["proposed_correction"] == 300000.0


def test_e2e_persistent_error_is_document_finding(patch_client):
    # Magnitude slip that the escalated re-extraction REPRODUCES: the
    # document itself is wrong. One retry spent, then a first-class finding.
    bad = raw_table(corrupt={(5, "Costs to Date"): "4,200,000"})
    patch_client([json.dumps(bad), json.dumps(bad)])
    final = invoke(build_graph())
    rep = final["report"]
    assert rep["reextract_count"] == 1
    assert rep["overall_status"] == "verified_mapping_with_findings"
    assert rep["findings"][0]["classification"] == "separator_or_magnitude_error"


def test_e2e_confusable_repaired_in_parse(patch_client):
    # OCR confusable fixed deterministically -- validator never sees it,
    # zero retries, zero extra LLM calls.
    bad = raw_table(corrupt={(0, "Costs to Date"): "4O,000"})
    fake = patch_client([json.dumps(bad)])
    final = invoke(build_graph())
    rep = final["report"]
    assert rep["overall_status"] == "verified"
    assert len(fake.calls) == 1
    assert any(f["flag"] == "confusable_repair"
               for f in rep["parse"]["cell_flags"])


def test_eu_decimal_convention():
    rows = [["Job A", "1.500.000", "1.200.000", "600.000", "750.000"],
            ["Job B", "2.000.000", "1.600.000", "800.000", "1.000.000"],
            ["Job C", "1.000.000", "800.000", "400.000", "500.000"]]
    res = parse_table(rows)
    assert res.decimal_convention == "eu"
    assert res.matrix[0, 0] == 1500000.0


# ------------------------------------------------------- e2e: sparse table

def test_e2e_sparse_routes_to_fallback(patch_client):
    sparse = raw_table(columns=["Job #", "Contract Price", "Est. Total Cost",
                                "Costs to Date", "Billed to Date"],
                       with_totals=False)
    fb_response = json.dumps({
        "mapping": {"0": "V", "1": "C", "2": "D", "3": "B"},
        "confidence": {"0": "high", "1": "high", "2": "high", "3": "high"},
        "notes": "headers are unambiguous",
    })
    fake = patch_client([json.dumps(sparse), fb_response])
    final = invoke(build_graph())
    rep = final["report"]
    assert rep["validator_status"] == "insufficient_information_for_validation"
    assert rep["overall_status"] == "llm_mapped_unverified"
    assert any(c["purpose"] == "header_fallback" for c in fake.calls)
    provs = {c["provenance"] for c in rep["columns"]}
    assert "llm-only" in provs or "math-constrained-llm" in provs


def test_e2e_extraction_failure_reported(patch_client):
    class Boom:
        def generate(self, *a, **k):
            raise RuntimeError("model down")
    import wipple.extraction as ex
    ex_client = Boom()
    import pytest as _;  # noqa
    # patch directly
    orig = extraction.get_client
    extraction.get_client = lambda: ex_client
    try:
        final = invoke(build_graph())
        assert final["report"]["overall_status"] == "extraction_failed"
        assert final["report"]["extraction_attempts"][0]["ok"] is False
    finally:
        extraction.get_client = orig


# ------------------------------------------- repair gating (column context)

def test_job_ids_made_of_confusables_never_repaired():
    # S, O, B, I are all in the confusable set; ungated repair would turn
    # this label column into a fabricated numeric column (5101, 5102, ...).
    rows = [["S101", "500,000", "400,000", "100,000", "40,000"],
            ["S102", "800,000", "600,000", "200,000", "120,000"],
            ["B203", "1,200,000", "1,000,000", "200,000", "250,000"],
            ["IO94", "400,000", "300,000", "100,000", "120,000"]]
    res = parse_table(rows)
    assert res.matrix.shape == (4, 4)            # ID col is NOT in the matrix
    assert res.job_labels == ["S101", "S102", "B203", "IO94"]  # verbatim
    assert not any(f.flag == "confusable_repair" for f in res.cell_flags)


def test_address_column_untouched():
    rows = [["J-1", "12 Oak St Suite 1O5", "500,000", "400,000", "40,000", "50,000"],
            ["J-2", "88 Bird Blvd",        "800,000", "600,000", "120,000", "160,000"],
            ["J-3", "5 Sole Ave",          "1,200,000", "1,000,000", "250,000", "300,000"]]
    res = parse_table(rows)
    assert res.matrix.shape == (3, 4)
    assert res.job_labels == ["J-1", "J-2", "J-3"]
    addr = [d for d in res.dropped_columns if d["col"] == 1]
    assert addr and addr[0]["reason"] == "non-numeric"
    assert not any(f.flag == "confusable_repair" for f in res.cell_flags)


def test_repair_still_fires_inside_qualified_numeric_column():
    rows = [["J-1", "5OO,000", "400,000", "40,000"],     # corrupt cell
            ["J-2", "800,000", "600,000", "120,000"],
            ["J-3", "1,200,000", "1,000,000", "250,000"],
            ["J-4", "400,000", "300,000", "120,000"]]
    res = parse_table(rows)
    reps = [f for f in res.cell_flags if f.flag == "confusable_repair"]
    assert len(reps) == 1 and reps[0].value == 500000.0
    assert res.matrix[0, 0] == 500000.0


def test_majority_corrupted_column_dropped_not_fabricated():
    # 3 of 4 cells corrupted: fails the strict 60% bar -> the column is
    # dropped and reported, never repaired into existence.
    rows = [["J-1", "5OO,OOO", "400,000", "40,000"],
            ["J-2", "8OO,000", "600,000", "120,000"],
            ["J-3", "1,2OO,000", "1,000,000", "250,000"],
            ["J-4", "400,000", "300,000", "120,000"]]
    res = parse_table(rows)
    assert res.matrix.shape[1] == 2
    assert any(d["reason"] == "non-numeric" and d["col"] == 1
               for d in res.dropped_columns)
