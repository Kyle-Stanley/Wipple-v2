"""
Document graph: the v3 assembly around the v2 engine.

    ingest -> chunk -> extract_chunks -> stitch -> tables -+-> concordance -> emit
                ^                                          |
                +----------- re_extract_doc <--------------+   (bad chunks, x1)

The v2 graph survives INTACT as the per-section engine: after stitching,
schema race, misalignment repair, and splitting, every final section is a
clean single-table document -- exactly what v2 was built for -- so the
tables node invokes the compiled v2 subgraph per section with an injected
raw_table. That is the LangGraph maintainability argument made literal: the
old pipeline drops in as one node of the new one.

Page is provenance, not process: row_prov flows fragment -> logical table ->
section, and every section finding gains a "page" field at assembly. The
report shape is {source, document, tables: [...]}: WIP and CC never merge
in data; a combined view is a presentation-layer join.
"""

from __future__ import annotations

from langgraph.graph import END, StateGraph
import re
from typing import Any, Optional, TypedDict

from . import ingest as ingest_mod
from .block_misalign import check_bands
from .chunking import chunk_document
from .concordance import concordance_node
from .extraction import extract_chunks_node
from .graph import build_graph
from .model_client import Metrics
from .parsing import parse_table
from .periods import extract_period_end
from .stitching import stitch
from .splitting import find_cc_block, split_sections
from .validation import run_schema_race, serialize_validation


class DocState(TypedDict, total=False):
    source_name: str
    doc_bytes: bytes
    media_type: str
    model_override: Optional[str]
    chunks: list
    fragments: list                 # injectable: tests run modelless
    bad_chunks: Optional[list]
    failed_chunks: list
    extraction_tier: str
    reextract_count: int
    extraction_attempts: list
    logical_tables: list
    tables: list
    concordance: dict
    report: dict
    reporting_date: Optional[str]
    reporting_date_error: Optional[str]
    _metrics: Any


def ingest_doc_node(state: DocState) -> dict:
    if state.get("fragments"):
        return {"chunks": []}          # pre-extracted; nothing to perceive
    data = state.get("doc_bytes") or b""
    kind = state.get("media_type") or ingest_mod.sniff(
        data, state.get("source_name", ""))
    if kind == "xlsx":
        raw = ingest_mod.xlsx_to_raw_table(data)
    elif kind == "csv":
        raw = ingest_mod.csv_to_raw_table(data)
    else:
        return {"chunks": chunk_document(data, kind), "media_type": kind}
    period = extract_period_end(raw.get("metadata_texts", []),
                                state.get("source_name", ""))
    frag = {"chunk_id": 0, "pages": [1], "headers": raw.get("headers", []),
            "rows": raw.get("rows", []), "position": 0,
            "notes": ["spreadsheet ingest; no vision extraction"],
            "reporting_period_text": None}
    return {"chunks": [], "fragments": [frag], "media_type": kind, **period}


def stitch_node(state: DocState) -> dict:
    return {"logical_tables": stitch(state.get("fragments") or [])}


def _page_of(row_prov, raw_row):
    if 0 <= raw_row < len(row_prov) and row_prov[raw_row]:
        return row_prov[raw_row][0][1]
    return None


def _attach_pages(report: dict, section_prov: list) -> None:
    """Section findings/failures carry matrix-row indices; the section's own
    parse row_index maps those to section raw rows, and section_prov maps
    raw rows to (chunk, page). Every finding learns its page."""
    ridx = (report.get("parse") or {}).get("row_index") or []

    def page(mr):
        if mr is None or mr >= len(ridx):
            return None
        return _page_of(section_prov, ridx[mr])

    for f in report.get("findings", []) or []:
        f["page"] = page(f.get("row_index"))
    for f in report.get("failures", []) or []:
        f["page"] = page(f.get("row_index"))


_ID_HEAD = re.compile(
    r"(?:job|project|contract).*(?:\bid\b|\bno\b|number|#)|"
    r"^(?:id|job\s*#|job\s*no\.?)$", re.I)
_NAME_HEAD = re.compile(
    r"(?:job|project|contract).*(?:name|description)|"
    r"^(?:name|description|project)$", re.I)
_ID_VALUE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/#-]*$")


def _attach_job_identity(report: dict, raw_rows: list,
                         headers: list) -> None:
    """Preserve ID and name separately without changing numeric validation."""
    parse = report.get("parse") or {}
    raw_indices = parse.get("row_index") or []
    dropped = [int(d["col"]) for d in parse.get("dropped_columns", [])
               if d.get("reason") in ("non-numeric", "job_labels")]
    if not dropped:
        return

    id_col = next((j for j in dropped
                   if j < len(headers) and _ID_HEAD.search(headers[j] or "")),
                  None)
    name_col = next((j for j in dropped
                     if j < len(headers)
                     and _NAME_HEAD.search(headers[j] or "")), None)

    def values(j):
        return [str(raw_rows[i][j]).strip()
                for i in raw_indices if i < len(raw_rows)
                and j < len(raw_rows[i]) and str(raw_rows[i][j]).strip()]

    if id_col is None:
        for j in dropped:
            vals = values(j)
            if vals and sum(bool(_ID_VALUE.match(v) and
                                 any(ch.isdigit() for ch in v))
                            for v in vals) / len(vals) >= .7:
                id_col = j
                break
    if name_col is None:
        for j in dropped:
            if j == id_col:
                continue
            vals = values(j)
            if vals and sum(bool(re.search(r"[A-Za-z]", v))
                            for v in vals) / len(vals) >= .7:
                name_col = j
                break

    def cell(raw_i, col):
        if col is None or raw_i >= len(raw_rows) or col >= len(raw_rows[raw_i]):
            return ""
        return str(raw_rows[raw_i][col]).strip()

    ids = [cell(i, id_col) for i in raw_indices]
    names = [cell(i, name_col) for i in raw_indices]
    table = report.get("table") or {}
    table["job_ids"] = ids
    table["job_names"] = names
    if isinstance(table.get("rows"), list):
        for i, row in enumerate(table["rows"]):
            row["job_id"] = ids[i] if i < len(ids) else ""
            row["job_name"] = names[i] if i < len(names) else ""
    for i, job in enumerate((report.get("analysis") or {}).get("jobs") or []):
        job["job_id"] = ids[i] if i < len(ids) else ""
        job["job_name"] = names[i] if i < len(names) else ""


def tables_node(state: DocState) -> dict:
    """Per logical table: parse -> schema race -> misalignment sweep ->
    split -> per-section v2 subgraph. Returns assembled tables + the chunks
    that need re-extraction."""
    subgraph = build_graph()
    metrics = state["_metrics"]
    out_tables, bad = [], set(state.get("failed_chunks") or [])

    for t in state.get("logical_tables") or []:
        pr = parse_table(t["rows"], headers=t["headers"])
        entry = {"pages": t["pages"], "chunks": t["chunks"],
                 "stitch_issues": t["issues"],
                 "joined_columns": t["joined_columns"],
                 "headers": t["headers"],
                 "numeric_col_map": pr.numeric_col_map,
                 "sections": []}
        if pr.matrix is None:
            entry["note"] = "no numeric body after parse"
            out_tables.append(entry)
            continue

        chosen, race = run_schema_race(pr.matrix, pr.job_labels)
        v = serialize_validation(chosen)
        v["schema"] = race["chosen"]
        entry["schema_race"] = race

        # -- block misalignment: band-shaped failures -> shift sweep --------
        matrix, mis_findings = pr.matrix, []
        if v.get("failures"):
            band_of_row = {mr: _prov_chunk(t["row_prov"], raw)
                           for mr, raw in enumerate(pr.row_index)
                           if _prov_chunk(t["row_prov"], raw) is not None}
            repaired, mis_findings, mis_bad = check_bands(
                pr.matrix, v["mapping"], v["schema"], v["failures"],
                band_of_row, scaled=pr.percent_scaled_cols)
            for f in mis_findings:
                f["pages"] = sorted({_page_of(t["row_prov"], pr.row_index[mr])
                                     for mr in f.get("rows", [])
                                     if mr < len(pr.row_index)} - {None})
            bad.update(mis_bad)
            if repaired is not None:
                matrix = repaired
                chosen, race = run_schema_race(matrix, pr.job_labels)
                v = serialize_validation(chosen)
                v["schema"] = race["chosen"]
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
                                  pr.row_index, seg)
        if v["schema"] == "cc" and len(sections) == 1:
            sections[0]["type"] = "cc"
        if seg.get("lone_rows"):
            entry["notes"] = [
                f"row {pr.job_labels[r]!r} is complete (E=V, D=C, Q=0, "
                "P=100%) but still carried in progress -- finished job not "
                "yet closed out of the WIP"
                for r in seg["lone_rows"]]

        # -- per-section: the v2 engine, one clean table at a time ----------
        for sec in sections:
            final = subgraph.invoke({
                "raw_table": {"headers": sec["headers"], "rows": sec["rows"],
                              "page_count": 1, "notes": []},
                "source_name": state.get("source_name", ""),
                "model_override": state.get("model_override"),
                # re-extraction budget pre-spent: the DOCUMENT graph owns
                # re-extraction (it knows which chunk); the section engine
                # must never loop back to a perception step it doesn't have.
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


def _prov_chunk(row_prov, raw_row):
    if 0 <= raw_row < len(row_prov) and row_prov[raw_row]:
        return row_prov[raw_row][0][0]
    return None


def re_extract_doc_node(state: DocState) -> dict:
    return {"extraction_tier": "escalated",
            "reextract_count": int(state.get("reextract_count", 0)) + 1}


def route_after_tables(state: DocState) -> str:
    if state.get("bad_chunks") and state.get("chunks") \
            and int(state.get("reextract_count", 0)) < 1:
        return "re_extract"
    return "concordance"


def emit_doc_node(state: DocState) -> dict:
    tables = state.get("tables") or []
    period = {
        "reporting_date": state.get("reporting_date"),
        "reporting_date_error": state.get("reporting_date_error"),
    }
    if not period["reporting_date"] and not period["reporting_date_error"]:
        texts = [f.get("reporting_period_text") for f in
                 (state.get("fragments") or [])
                 if f.get("reporting_period_text")]
        period = extract_period_end(texts, state.get("source_name", ""))
    schedule_types = sorted({
        s.get("type") for t in tables for s in (t.get("sections") or [])
        if s.get("type")
    })
    return {"report": {
        "source": state.get("source_name", ""),
        "document": {
            "n_chunks": len(state.get("chunks") or []) or
                        len({f["chunk_id"]
                             for f in state.get("fragments") or []}),
            "n_logical_tables": len(state.get("logical_tables") or []),
            "extraction_attempts": state.get("extraction_attempts", []),
            "reextract_count": state.get("reextract_count", 0),
            "unresolved_chunks": state.get("bad_chunks") or [],
            "concordance": state.get("concordance", {}),
            **period,
            "schedule_types": schedule_types,
        },
        "tables": tables,
    }}


def build_doc_graph():
    g = StateGraph(DocState)
    g.add_node("ingest", ingest_doc_node)
    g.add_node("extract_chunks", extract_chunks_node)
    g.add_node("stitch", stitch_node)
    g.add_node("tables", tables_node)
    g.add_node("re_extract", re_extract_doc_node)
    g.add_node("concordance", concordance_node)
    g.add_node("emit", emit_doc_node)

    g.set_entry_point("ingest")
    g.add_conditional_edges(
        "ingest", lambda s: "stitch" if s.get("fragments") else
        "extract_chunks",
        {"stitch": "stitch", "extract_chunks": "extract_chunks"})
    g.add_edge("extract_chunks", "stitch")
    g.add_edge("stitch", "tables")
    g.add_conditional_edges("tables", route_after_tables,
                            {"re_extract": "re_extract",
                             "concordance": "concordance"})
    g.add_edge("re_extract", "extract_chunks")
    g.add_edge("concordance", "emit")
    g.add_edge("emit", END)
    return g.compile()


def run_document(doc_bytes: bytes = b"", source_name: str = "",
                 fragments: list | None = None,
                 media_type: str | None = None,
                 model_override: str | None = None) -> tuple[dict, dict]:
    graph = build_doc_graph()
    metrics = Metrics()
    final = graph.invoke({
        "doc_bytes": doc_bytes, "source_name": source_name,
        "media_type": media_type, "fragments": fragments or [],
        "model_override": model_override,
        "extraction_tier": "primary", "reextract_count": 0,
        "extraction_attempts": [], "_metrics": metrics,
    }, {"recursion_limit": 50})
    report = final.get("report", {})
    report["metrics"] = metrics.summary()
    return report, metrics.summary()
