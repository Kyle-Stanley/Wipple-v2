from __future__ import annotations

from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def write(path: str, text: str) -> None:
    (ROOT / path).write_text(text, encoding="utf-8")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one match, found {count}")
    return text.replace(old, new, 1)


def patch_wip() -> None:
    path = "wipple/accounting/wip.py"
    text = read(path)
    if "from functools import lru_cache" not in text:
        match = re.search(r"^(from dataclasses import .+)$", text, re.MULTILINE)
        if not match:
            raise RuntimeError("wip imports: dataclasses import not found")
        text = text[:match.end()] + "\nfrom functools import lru_cache" + text[match.end():]

    pattern = re.compile(
        r"def detect_grid\(vals: np\.ndarray\) -> Optional\[float\]:\n"
        r"    \"\"\"Coarsest display grid.*?\n"
        r"    return None\n",
        re.DOTALL,
    )
    replacement = '''@lru_cache(maxsize=4096)
def _detect_grid_cached(dtype: str, shape: tuple[int, ...], payload: bytes) -> Optional[float]:
    """Pure cached implementation keyed by the finite values' exact bytes."""
    v = np.frombuffer(payload, dtype=np.dtype(dtype)).reshape(shape)
    for g in GRIDS:
        k = np.round(v / g)
        if np.all(np.abs(v - k * g) <= 1e-9 + 1e-9 * np.abs(v)):
            return g
    return None


def detect_grid(vals: np.ndarray) -> Optional[float]:
    """Coarsest display grid (ratio space) the values satisfy, or None.

    Coarsest-first iteration is load-bearing (anti-bug 5): returning the
    finest satisfying grid would make percent certification stricter than the
    visible display precision supports. The same candidate column/scale arrays
    recur across many hypotheses, so cache the exact finite vector. The cache
    is bounded and the key contains dtype, shape, and bytes; equal inputs take
    the identical numeric path while unrelated documents cannot grow it forever.
    """
    v = np.asarray(vals, dtype=float)
    v = v[np.isfinite(v)]
    if v.size == 0:
        return None
    return _detect_grid_cached(v.dtype.str, tuple(v.shape), v.tobytes())
'''
    text, count = pattern.subn(replacement, text, count=1)
    if count != 1:
        raise RuntimeError(f"detect_grid: expected one function, replaced {count}")
    write(path, text)


def patch_graph() -> None:
    path = "wipple/pipeline/graph.py"
    text = read(path)
    old = '''    # Entry: demo / pre-extracted runs may inject raw_table and skip extract.
    g.set_conditional_entry_point(
        lambda s: "parse" if s.get("raw_table") else "extract",
        {"parse": "parse", "extract": "extract"})
'''
    new = '''    # Entry: the document graph may seed the exact parse + validation state
    # it already computed for an unchanged section. Route from that result
    # instead of parsing and running the WIP/CC race a second time.
    def entry_node(state):
        if state.get("matrix") is not None and state.get("validation") is not None:
            return route_after_validate(state)
        return "parse" if state.get("raw_table") else "extract"

    g.set_conditional_entry_point(
        entry_node,
        {"parse": "parse", "extract": "extract",
         "emit": "analyze", "fallback": "fallback",
         "disambiguate": "disambiguate", "re_extract": "re_extract"})
'''
    text = replace_once(text, old, new, "graph conditional entry")
    write(path, text)


def patch_document() -> None:
    path = "wipple/pipeline/document.py"
    text = read(path)
    text = replace_once(
        text,
        "from ..accounting.validation import run_schema_race, serialize_validation",
        "from ..accounting.validation import parse_node, validate_node",
        "document validation imports",
    )

    start = text.index("def tables_node(state: DocState) -> dict:\n")
    end = text.index("\n\ndef _prov_chunk", start)
    replacement = '''def tables_node(state: DocState) -> dict:
    """Per logical table: canonical parse -> one schema race -> optional
    repair/split -> per-section analysis. An unchanged single section reuses
    the exact parse and validation dictionaries instead of recomputing them."""
    subgraph = build_graph()
    metrics = state["_metrics"]
    out_tables, bad = [], set(state.get("failed_chunks") or [])

    for t in state.get("logical_tables") or []:
        raw_table = {
            "headers": t["headers"],
            "rows": t["rows"],
            "page_count": 1,
            "notes": [],
            "title_texts": t.get("title_texts") or [],
        }
        # Keep the ParseResult only for structural metadata used by the band
        # checker. parse_node is the canonical parse used by the final section
        # graph: it also separates and preserves a trailing stated-total row.
        structural = parse_table(t["rows"], headers=t["headers"])
        parsed = parse_node({"raw_table": raw_table})
        parsed_matrix = parsed.get("matrix")
        parsed_report = parsed.get("parse_report") or {}
        parsed_row_index = parsed_report.get("row_index") or []

        entry = {"pages": t["pages"], "chunks": t["chunks"],
                 "stitch_issues": t["issues"],
                 "joined_columns": t["joined_columns"],
                 "title_texts": t.get("title_texts") or [],
                 "headers": t["headers"],
                 "numeric_col_map": parsed.get("numeric_col_map") or [],
                 "sections": []}
        if parsed_matrix is None:
            entry["note"] = "no numeric body after parse"
            out_tables.append(entry)
            continue

        validation_state = {**parsed, "raw_table": raw_table}
        v = validate_node(validation_state)["validation"]
        race = (v.get("diagnostics") or {}).get("schema_race") or {
            "chosen": v.get("schema", "wip")}
        entry["schema_race"] = race

        # -- block misalignment: band-shaped failures -> shift sweep --------
        matrix, mis_findings = parsed_matrix, []
        partial_multipage_mapping = (
            len(t.get("chunks") or []) > 1
            and bool(v.get("mapping"))
            and len(v["mapping"]) < int(parsed_matrix.shape[1])
        )
        if (v.get("failures")
                or v.get("status")
                == "insufficient_information_for_validation"
                or partial_multipage_mapping):
            band_of_row = {mr: _prov_chunk(t["row_prov"], raw)
                           for mr, raw in enumerate(parsed_row_index)
                           if _prov_chunk(t["row_prov"], raw) is not None}
            repaired, mis_findings, mis_bad = check_bands(
                parsed_matrix, v["mapping"], v["schema"], v["failures"],
                band_of_row, scaled=structural.percent_scaled_cols)
            for finding in mis_findings:
                finding["pages"] = sorted({
                    _page_of(t["row_prov"], parsed_row_index[mr])
                    for mr in finding.get("rows", [])
                    if mr < len(parsed_row_index)} - {None})
            bad.update(mis_bad)
            if repaired is not None:
                matrix = repaired
                repaired_state = {**parsed, "matrix": matrix,
                                  "raw_table": raw_table}
                v = validate_node(repaired_state)["validation"]
                race = (v.get("diagnostics") or {}).get("schema_race") or {
                    "chosen": v.get("schema", "wip")}
                entry["schema_race"] = race
                entry["misalignment_repaired"] = True
        entry["misalignment_findings"] = mis_findings
        entry["validation_summary"] = {"status": v["status"],
                                       "schema": v["schema"],
                                       "reason": v["reason"]}
        entry["validation"] = v          # concordance reads the mapping

        # -- split: over-merged WIP+CC comes apart on exact degeneracy ------
        seg = {"split_at": None, "lone_rows": []}
        if v["schema"] == "wip" and v.get("mapping"):
            seg = find_cc_block(matrix, v["mapping"])
        sections = split_sections(t["rows"], t["headers"], t["row_prov"],
                                  parsed_row_index, seg)
        if v["schema"] == "cc" and len(sections) == 1:
            sections[0]["type"] = "cc"
        if seg.get("lone_rows"):
            labels = parsed.get("job_labels") or []
            entry["notes"] = [
                f"row {labels[r]!r} is complete (E=V, D=C, Q=0, "
                "P=100%) but still carried in progress -- finished job not "
                "yet closed out of the WIP"
                for r in seg["lone_rows"] if r < len(labels)]

        # -- per-section: reuse exact parse/validation when raw input is same -
        for sec in sections:
            sec_raw = {
                "headers": sec["headers"], "rows": sec["rows"],
                "page_count": 1, "notes": [],
                "title_texts": t.get("title_texts") or [],
            }
            unchanged = (
                not entry.get("misalignment_repaired")
                and sec_raw["headers"] == raw_table["headers"]
                and sec_raw["rows"] == raw_table["rows"]
            )
            if unchanged:
                sec_parsed = parsed
                sec_validation = v
            else:
                sec_parsed = parse_node({"raw_table": sec_raw})
                sec_validation = validate_node(
                    {**sec_parsed, "raw_table": sec_raw})["validation"]

            final = subgraph.invoke({
                **sec_parsed,
                "validation": sec_validation,
                "raw_table": sec_raw,
                "source_name": state.get("source_name", ""),
                "model_override": state.get("model_override"),
                # re-extraction budget pre-spent: the DOCUMENT graph owns
                # re-extraction (it knows which chunk); the section engine
                # must never loop back to a perception step it does not have.
                "extraction_tier": "primary", "reextract_count": 1,
                "extraction_attempts": [], "_metrics": metrics,
            })
            rep = final.get("report", {})
            _attach_job_identity(rep, sec["rows"], sec["headers"])
            _attach_pages(rep, sec["row_prov"])
            entry["sections"].append({
                "type": sec["type"],
                "schema": (rep.get("analysis") or {}).get("schema", "wip"),
                "note": sec.get("note"),
                "n_rows": len(sec["rows"]),
                "pages": sorted({p[0][1] for p in sec["row_prov"] if p}),
                "report": rep})
        out_tables.append(entry)

    return {"tables": out_tables,
            "bad_chunks": sorted(bad) if bad else None}
'''
    text = text[:start] + replacement + text[end:]
    write(path, text)


def add_tests() -> None:
    path = ROOT / "tests/test_performance_invariants.py"
    path.write_text('''from __future__ import annotations

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
''', encoding="utf-8")


def cleanup() -> None:
    for rel in (
        "tmp_probe.txt",
        "tools/apply_performance_patch.py",
        ".github/workflows/apply-performance-patch.yml",
    ):
        path = ROOT / rel
        if path.exists():
            path.unlink()


if __name__ == "__main__":
    patch_wip()
    patch_graph()
    patch_document()
    add_tests()
    cleanup()
