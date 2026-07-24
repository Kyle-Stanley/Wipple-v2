"""
Corpus gates: the document acceptance suite. Zero model calls, zero API keys --
fragments are injected from the layout engine, so assertions are exact against
known ground truth.

Run: python -m wipple.support.gates
"""

from __future__ import annotations

import numpy as np

from .corpus import build_book, layout_fragments, render_pdf, render_png
from ..accounting.cc import validate_cc
from ..accounting.wip import validate_wip
from ..accounting.parsing import parse_table
from ..reconstruction.layout import assemble
from ..reconstruction.splitting import find_cc_block
from ..reconstruction.alignment import check_bands
from ..accounting.validation import run_schema_race, serialize_validation
from ..pipeline.document import run_document

PASS, FAIL = "PASS", "FAIL"
_results = []


def gate(name, ok, detail=""):
    _results.append((name, ok))
    print(f"[{PASS if ok else FAIL}] {name}" + (f" -- {detail}" if detail
                                                else ""))


def g0_corpus():
    book = build_book(seed=42)
    frags, meta = layout_fragments(book)
    gate("G0 corpus renders", len(render_pdf(frags)) > 5000
         and len(render_png(frags)) > 5000
         and meta["n_pages"] >= 5)


def g1_cc_engine():
    rng = np.random.default_rng(7)
    n = 20
    RT = np.round(10 ** rng.uniform(5.2, 6.5, n) / 1000) * 1000
    KT = np.round(RT * (1 - rng.uniform(0.06, 0.18, n)))
    GT = RT - KT
    fr = rng.uniform(0, 1, n)
    RP, KP = np.round(RT * fr), np.round(KT * fr)
    M9 = np.column_stack([RP, RT - RP, RT, KP, KT - KP, KT,
                          RP - KP, (RT - RP) - (KT - KP), GT])
    r9 = validate_cc(M9)
    gate("G1a 9-col lattice mapped, period ambiguity honest",
         len(r9.mapping) == 9 and r9.competing_mapping is not None
         and len(r9.witnesses) >= 5)
    r3 = validate_cc(np.column_stack([RT, KT, GT]))
    gate("G1b 3-col certifies", r3.status == "success"
         and sorted(r3.mapping.values()) == ["GT", "KT", "RT"])
    B = RT.copy()
    M5 = np.column_stack([RT, KT, GT, B, np.round(RT * 0.05),
                          rng.integers(2019, 2026, n).astype(float)])
    r5 = validate_cc(M5)
    gate("G1c completed billings equal revenue; retainage/year unassigned",
         r5.status == "success" and r5.mapping.get(1) == "KT"
         and r5.mapping.get(3) == "BC"
         and 4 not in r5.mapping and 5 not in r5.mapping)
    M9e = M9.copy()
    M9e[4, 4] *= 10
    re_ = validate_cc(M9e)
    f = re_.findings[0] if re_.findings else None
    gate("G1d planted error -> exact culprit + correction",
         f is not None and f.culprit_variable == "KC"
         and abs(f.proposed_correction - M9[4, 4]) < 1.0)
    book = build_book(seed=42, wip_errors=0)
    pr = parse_table(book["wip"]["rows"], headers=book["wip"]["headers"])
    chosen, race = run_schema_race(pr.matrix, pr.job_labels)
    gate("G1e race: WIP table -> wip engine", race["chosen"] == "wip")
    chosen, race = run_schema_race(M9, None)
    gate("G1f race: CC table -> cc engine", race["chosen"] == "cc")


def g3_assembly():
    book = build_book(seed=42)
    t = assemble(layout_fragments(book, cc_placement="own_page")[0])
    gate("G3a plain 5-pager -> 2 logical tables",
         len(t) == 2 and len(t[0]["rows"]) == 49 and len(t[1]["rows"]) == 13)

    t = assemble(layout_fragments(book, cc_placement="consolidated",
                                  page_subtotals=True)[0])
    gate("G3b consolidated -> 1 logical table",
         len(t) == 1 and len(t[0]["rows"]) == 68)

    t = assemble(layout_fragments(book, vsplit=(6, True))[0])
    w = [x for x in t if len(x["rows"]) > 20][0]
    gate("G3c columnar split rejoined (names repeated)",
         w["joined_columns"] and len(w["headers"]) == 11
         and len(w["rows"]) == 49)

    t = assemble(layout_fragments(book, vsplit=(6, False))[0])
    w = [x for x in t if len(x["rows"]) > 20][0]
    gate("G3d columnar split rejoined (positional)",
         w["joined_columns"] and len(w["headers"]) == 11)

    frags = layout_fragments(book, vsplit=(6, True), drop_row=(1, 4))[0]
    t = assemble(frags)
    gate("G3e incompatible horizontal join preserves both source grids",
         t[0]["rows"] == frags[0]["rows"]
         and t[1]["rows"] == frags[1]["rows"])

    frags = layout_fragments(book)[0][:2]
    frags[1] = {**frags[1], "rows": [list(r) for r in frags[0]["rows"][-3:]]
                + [list(r) for r in frags[1]["rows"]], "overlaps_prev": True}
    frags[1]["rows"][1][4] = "999,999"
    t = assemble(frags)
    gate("G3f declared strip overlap deduped, disagreement witnessed",
         len(t[0]["rows"]) == 24 and any(i["kind"] == "overlap_mismatch"
                                         for i in t[0]["issues"]))


def g4_splitter():
    book = build_book(seed=9, wip_errors=0)
    t = assemble(layout_fragments(book, cc_placement="consolidated")[0])[0]
    pr = parse_table(t["rows"], headers=t["headers"])
    v = validate_wip(pr.matrix, job_labels=pr.job_labels)
    seg = find_cc_block(pr.matrix, v.mapping)
    gate("G4a consolidated splits at the exact row", seg["split_at"] == 48)

    book2 = build_book(seed=11, wip_errors=0)
    V, C = book2["wip"]["true"][10][0], book2["wip"]["true"][10][1]
    book2["wip"]["true"][10] = [V, C, V - C, C, 0, 1.0, V, V, 0, 0]
    F = lambda x: f"{int(round(x)):,}"
    nm = book2["wip"]["rows"][10][0]
    book2["wip"]["rows"][10] = [nm, F(V), F(C), F(V - C), F(C), "0",
                                "100.0%", F(V), F(V), "-", "-"]
    t2 = assemble(layout_fragments(book2)[0])[0]
    pr2 = parse_table(t2["rows"], headers=t2["headers"])
    v2 = validate_wip(pr2.matrix, job_labels=pr2.job_labels)
    seg2 = find_cc_block(pr2.matrix, v2.mapping)
    gate("G4b lone complete job stays in WIP (soft finding)",
         seg2["split_at"] is None and seg2["lone_rows"] == [10])


def g5_misalignment():
    book = build_book(seed=5, wip_errors=0)
    frags, _ = layout_fragments(book, shift_chunk=(2, 4))
    t = [x for x in assemble(frags) if len(x["rows"]) > 20][0]
    pr = parse_table(t["rows"], headers=t["headers"])
    v = serialize_validation(validate_wip(pr.matrix,
                                          job_labels=pr.job_labels))
    band_of_row = {mr: t["row_prov"][raw][0][0]
                   for mr, raw in enumerate(pr.row_index)}
    rep, finds, bad = check_bands(pr.matrix, v["mapping"], "wip",
                                  v["failures"], band_of_row,
                                  scaled=pr.percent_scaled_cols)
    f = next((x for x in finds if x["kind"] == "block_misalignment"), None)
    ok = (f is not None and f["chunk_id"] == 2 and f["shift"] == 1
          and len(finds) == 1 and bad == [2])
    gate("G5a shifted chunk -> ONE finding, chunk cited", ok)
    truth = np.array(book["wip"]["true"], float)
    band = sorted(m for m, raw in enumerate(pr.row_index)
                  if t["row_prov"][raw][0][0] == 2)
    rec = [0, 1, 2, 4, 6, 7, 8, 9]
    diff = float(np.nanmax(np.abs(rep[np.ix_(band, rec)]
                                  - truth[24:36][:, rec])))
    gate("G5b repair exact to the dollar; lost column NaN",
         diff == 0.0 and bool(np.all(~np.isfinite(rep[band, 3]))))


def g6_document():
    book = build_book(seed=13, n_wip=60, wip_errors=2, cc_cols=9)
    frags, meta = layout_fragments(book, rows_per_page=12,
                                   cc_placement="own_page")
    rep, _ = run_document(fragments=frags, source_name="g6")
    secs = [s for t in rep["tables"] for s in t["sections"]]
    wip = next(s for s in secs if s["type"] == "wip")
    cc = next(s for s in secs if s["type"] == "cc")
    finds = wip["report"]["findings"]
    exp_pages = sorted(meta["page_of"][("wip", e["row"])]
                       for e in book["errors"])
    got_pages = sorted(f["page"] for f in finds)
    exp_corr = sorted(float(e["true"]) for e in book["errors"])
    got_corr = sorted(float(f["proposed_correction"]) for f in finds)
    gate("G6a 15-pager: findings cite correct pages + exact corrections",
         got_pages == exp_pages and got_corr == exp_corr
         and wip["report"]["overall_status"]
         == "verified_mapping_with_findings")
    gate("G6b CC section routed through disambiguation honestly",
         cc["report"]["overall_status"] in ("disambiguated", "verified"))

    book2 = build_book(seed=9, wip_errors=0)
    frags2, _ = layout_fragments(book2, cc_placement="consolidated")
    rep2, _ = run_document(fragments=frags2, source_name="g6-consolidated")
    secs2 = [(s["type"], s["n_rows"]) for t in rep2["tables"]
             for s in t["sections"]]
    gate("G6c consolidated document splits into wip + cc sections",
         ("wip", 49) in secs2 and any(ty == "cc" for ty, _ in secs2))

    book3 = build_book(seed=21, n_wip=12, wip_errors=1)
    frag = [{"chunk_id": 0, "pages": [1], "headers": book3["wip"]["headers"],
             "rows": book3["wip"]["rows"], "table_index": 0}]
    rep3, _ = run_document(fragments=frag, source_name="g6-single")
    s3 = rep3["tables"][0]["sections"][0]["report"]
    gate("G6d single-table regression: v2 behavior preserved",
         s3["overall_status"] == "verified_mapping_with_findings"
         and len(s3["findings"]) == 1)


def g7_concordance():
    book = build_book(seed=3, n_wip=14, wip_errors=0)
    hdrs = list(book["wip"]["headers"])
    hdrs[4] = "Billed to Date"
    frag = [{"chunk_id": 0, "pages": [1], "headers": hdrs,
             "rows": book["wip"]["rows"], "table_index": 0}]
    rep, _ = run_document(fragments=frag, source_name="g7")
    disc = rep["document"]["concordance"]["discordant"]
    gate("G7 lying header -> discordance finding, math outranks label",
         any(d["column"] == 4 and d["variable"] == "D" for d in disc))


def main():
    g0_corpus()
    g1_cc_engine()
    g3_assembly()
    g4_splitter()
    g5_misalignment()
    g6_document()
    g7_concordance()
    n_ok = sum(1 for _, ok in _results if ok)
    print(f"\n{n_ok}/{len(_results)} gates pass")
    return 0 if n_ok == len(_results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
