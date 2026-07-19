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
from wipple.wip_validator import validate_wip
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
    assert f0["proof_kind"] == "direct"
    correction = rep["analysis"]["corrections"][0]
    assert correction["corroborated"]
    revenue_total = rep["analysis"]["totals_after_corrections"][7]
    assert revenue_total["matches_after_corrections"]
    assert revenue_total["computed_after_corrections"] == 3860000.0


def test_bad_stated_total_does_not_block_a_proven_row_correction(patch_client):
    bad = raw_table(corrupt={(2, "Revenues Earned"): "287,451"})
    revenue_col = bad["headers"].index("Revenues Earned")
    bad["rows"][-1][revenue_col] = "3,900,000"  # separate footing error
    patch_client([json.dumps(bad)])

    rep = invoke(build_graph())["report"]

    assert rep["findings"][0]["proposed_correction"] == 300000.0
    total = rep["analysis"]["totals_after_corrections"][revenue_col]
    assert total["computed_after_corrections"] == 3860000.0
    assert total["stated"] == 3900000.0
    assert not total["matches_after_corrections"]
    assert not rep["analysis"]["corrections"][0]["corroborated"]


def test_e2e_persistent_error_is_document_finding(patch_client):
    # Magnitude slip that the escalated re-extraction REPRODUCES: the
    # document itself is wrong. One retry spent, then a first-class finding.
    bad = raw_table(corrupt={(5, "Costs to Date"): "4,200,000"})
    patch_client([json.dumps(bad), json.dumps(bad)])
    final = invoke(build_graph())
    rep = final["report"]
    assert rep["reextract_count"] == 1
    assert rep["overall_status"] == "verified_mapping_with_findings"
    assert rep["findings"][0]["classification"] == "extra_character"


def test_single_formula_inherits_proof_from_the_rest_of_the_row():
    bad = raw_table(corrupt={(0, "Cost to Complete"): "7,760,000"})
    parsed = parse_table(bad["rows"], headers=bad["headers"])
    result = validate_wip(parsed.matrix, parsed.job_labels)

    assert result.status == "validation_failed"
    assert len(result.findings) == 1
    finding = result.findings[0]
    assert finding.culprit_variable == "Q"
    assert finding.proposed_correction == 360000.0
    assert finding.correction_basis == ["Q = C - D"]
    assert finding.proof_kind == "inherited"


def test_two_errors_in_one_job_are_repaired_together_when_uniquely_proven():
    rows, labels = [], []
    for tup in rows_numeric():
        name, V, C, G, D, Q, P, E, B, U, O = tup
        labels.append(name)
        rows.append([V, C, G, D, Q, P, E, B, U, O, V - B])
    matrix = np.asarray(rows, dtype=float)
    matrix[0, 3] = 47000.0       # Costs to Date; true value 40,000
    matrix[0, 7] = 68000.0       # Billed to Date; true value 60,000

    result = validate_wip(matrix, labels)

    assert result.status == "validation_failed"
    corrections = {
        f.culprit_variable: f.proposed_correction for f in result.findings}
    assert corrections == {"B": 60000.0, "D": 40000.0}
    assert all(f.proof_kind == "joint" for f in result.findings)


def test_competing_minimal_repairs_remain_unresolved():
    rows, labels = [], []
    for tup in rows_numeric():
        name, V, C, G, D, Q, P, E, B, _U, _O = tup
        labels.append(name)
        rows.append([V, C, G, D, Q, P, E, B, B / V])
    matrix = np.asarray(rows, dtype=float)
    matrix[0, 7] = 68000.0       # B could be changed to agree with PB...
    matrix[0, 8] = 0.13          # ...or PB could be changed to agree with B

    result = validate_wip(matrix, labels)

    assert result.status == "validation_failed"
    assert len(result.findings) == 1
    finding = result.findings[0]
    assert finding.proposed_correction is None
    assert finding.classification == "ambiguous_multi_cell"
    assert finding.candidate_variables == ["B", "PB"]


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


# --------------------------------------------- interior subtotal handling

def _subtotal_book():
    """Two sections of jobs, a BLANK-labeled subtotal after each section,
    and a grand total that double-counts (= body + subtotals)."""
    import numpy as np
    rng = np.random.default_rng(5)
    def m(x): return f"{int(round(x)):,}"
    rows, money = [], []
    def job(name, V, P):
        mg = 0.12
        C = round(V * (1 - mg)); D = round(C * P); E = round(V * P)
        B = E + 2000
        vals = (V, C, V - C, D, C - D, E, B)
        money.append(vals)
        rows.append([name, m(V), m(C), m(V - C), m(D), m(C - D),
                     f"{P*100:.1f}%", m(E), m(B)])
        return vals
    hdr = ["Job", "Contract", "Est Cost", "Est GP", "CTD", "CTC", "% Comp",
           "Earned", "Billed"]
    sec1 = [job(f"A-{k}", 400_000 + 100_000 * k, 0.2 + 0.1 * k) for k in range(4)]
    s1 = [sum(v[i] for v in sec1) for i in range(7)]
    rows.append(["", m(s1[0]), m(s1[1]), m(s1[2]), m(s1[3]), m(s1[4]), "",
                 m(s1[5]), m(s1[6])])                       # blank-label subtotal
    sec2 = [job(f"B-{k}", 600_000 + 100_000 * k, 0.3 + 0.1 * k) for k in range(4)]
    s2 = [sum(v[i] for v in sec2) for i in range(7)]
    rows.append(["", m(s2[0]), m(s2[1]), m(s2[2]), m(s2[3]), m(s2[4]), "",
                 m(s2[5]), m(s2[6])])                       # second subtotal
    g = [s1[i] + s2[i] for i in range(7)]
    gg = [2 * x for x in g]  # double-counted grand: body + subtotals
    rows.append(["Total", m(gg[0]), m(gg[1]), m(gg[2]), m(gg[3]), m(gg[4]),
                 "", m(gg[5]), m(gg[6])])
    return {"headers": hdr, "rows": rows, "page_count": 1, "notes": []}


def test_interior_blank_subtotals_stripped(patch_client):
    book = _subtotal_book()
    patch_client([json.dumps(book)])
    final = invoke(build_graph())
    rep = final["report"]
    assert rep["table"] and len(rep["table"]["values"]) == 8   # 8 real jobs
    assert len(rep["parse"]["stripped_total_rows"]) == 3       # 2 subs + grand
    assert rep["validator_status"] == "success"
    assert rep["totals_check"]["all_match"]                    # via subtotals
    assert any(c.get("includes_subtotals")
               for c in rep["totals_check"]["columns"].values())
