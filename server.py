"""wipple.ai server v3: FastAPI + SSE streaming over the DOCUMENT graph.

Same architecture as v2 (thread runs the graph, queue feeds the SSE
generator, heartbeats keep proxies alive); what changed is the graph and
therefore the narration. Extraction is per-page with live progress lines,
and the new stages -- stitching, the schema race, splitting, block
misalignment, concordance -- each narrate what they proved, not what they
did. Everything the old endpoints accepted still works: spreadsheets and
CSVs are sniffed inside the graph's own ingest node now, so /api/scan just
forwards bytes.

The final SSE `report` event carries the v3 shape:
    {source, tables: [{sections: [{type, report}], ...}], document, metrics}
Each section report is v2-shaped by design -- the existing renderer works
per section; the frontend's only new job is the loop.
"""

from __future__ import annotations

import json
import queue
import threading
import time

from fastapi import FastAPI, Form, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from wipple.demo import demo_raw_table
from wipple.docgraph import build_doc_graph
from wipple.model_client import MODEL_REGISTRY, Metrics

app = FastAPI(title="wipple")
app.add_middleware(CORSMiddleware, allow_origins=["*"],
                   allow_methods=["*"], allow_headers=["*"])
app.mount("/static", StaticFiles(directory="static"), name="static")
GRAPH = build_doc_graph()


def _plural(n, word):
    if n == 1:
        return f"1 {word}"
    return f"{n} {word[:-1] + 'ies' if word.endswith('y') else word + 's'}"


def _narrate(node: str, up: dict, state: dict) -> list[str]:
    if node == "ingest":
        if up.get("fragments") and not up.get("chunks"):
            f = up["fragments"][0]
            return [f"Spreadsheet cells read natively: "
                    f"{_plural(len(f['rows']), 'row')} x "
                    f"{len(f['headers'])} columns, no vision model needed"]
        n = len(up.get("chunks") or [])
        if n:
            kind = state.get("media_type", "")
            unit = "strip" if str(kind).startswith("image/") else "page"
            return [f"Document split into {_plural(n, unit)}"]
        return []
    if node == "extract_chunks":
        frs = up.get("fragments") or []
        failed = up.get("failed_chunks") or []
        msg = (f"Extraction complete: {_plural(len(frs), 'table fragment')} "
               f"transcribed")
        if failed:
            msg += f" ({_plural(len(failed), 'page')} unreadable)"
        return [msg]
    if node == "stitch":
        lts = up.get("logical_tables") or []
        if not lts:
            return ["No tables found in the document"]
        out = []
        for t in lts:
            p0, p1 = t["pages"][0], t["pages"][-1]
            span = (f"page {p0}" if p0 == p1 else f"pages {p0}-{p1}")
            piece = f"{_plural(len(t['rows']), 'row')} across {span}"
            if t.get("joined_columns"):
                piece += ", facing-page columns rejoined"
            out.append(piece)
        lines = [f"Assembled {_plural(len(lts), 'logical table')}: "
                 + "; ".join(out)]
        issues = [i for t in lts for i in t.get("issues", [])]
        for i in issues[:3]:
            if i["kind"] == "hjoin_missing_row":
                lines.append(f"Row '{i.get('row_label', '?')}' missing from "
                             f"the continuation page {i.get('page', '?')}")
            elif i["kind"] == "overlap_mismatch":
                lines.append("Same row extracted twice with different "
                             "values -- extraction flagged unreliable there")
        return lines
    if node == "tables":
        lines = []
        for t in (up.get("tables") or []):
            for mf in (t.get("misalignment_findings") or []):
                pg = ", ".join(map(str, mf.get("pages", [])))
                lines.append(f"Page {pg} was read with a column offset -- "
                             "repaired deterministically and re-certified, "
                             "one structural finding instead of dozens")
            secs = t.get("sections") or []
            if len(secs) > 1 and any(s["type"] == "cc" for s in secs):
                k = next(s["n_rows"] for s in secs if s["type"] == "cc")
                lines.append(f"{_plural(k, 'completed contract')} carved out "
                             "of the WIP by exact identities, validated "
                             "separately")
            for s in secs:
                r = s.get("report", {})
                v = r.get("validation") or {}
                st = r.get("overall_status", "")
                label = ("WIP schedule" if s["type"] == "wip"
                         else "Completed contracts")
                nw = len(r.get("witnesses", []) or [])
                nf = len(r.get("findings", []) or [])
                if st == "verified":
                    lines.append(f"{label}: certified from "
                                 f"{_plural(nw, 'accounting identity')}, "
                                 "headers not used")
                elif st == "verified_mapping_with_findings":
                    lines.append(f"{label}: mapping certified; "
                                 f"{_plural(nf, 'cell')} "
                                 f"fail{'s' if nf == 1 else ''} the row "
                                 "identities, diagnosed below")
                elif "ambig" in st or (v.get("competing_mapping")):
                    lines.append(f"{label}: two readings both certified, "
                                 "headers broke the tie")
                else:
                    lines.append(f"{label}: too little numeric structure to "
                                 "certify, headers used as fallback and "
                                 "marked unverified")
            note = t.get("notes") or []
            for x in note[:2]:
                lines.append(x)
        return lines
    if node == "re_extract":
        bad = state.get("bad_chunks") or []
        pages = ", ".join(str(b + 1) for b in bad) or "?"
        return [f"Re-reading page {pages} with a stronger model -- "
                "the rest of the document stands"]
    if node == "concordance":
        c = up.get("concordance") or {}
        disc = c.get("discordant") or []
        ann = c.get("annotations") or []
        if disc:
            d = disc[0]
            return [f"Header '{d.get('header')}' disagrees with what the "
                    f"numbers prove (column certifies as "
                    f"{d.get('variable')}) -- the math outranks the label"]
        if ann:
            return [f"Headers agree with the certified mapping on "
                    f"{_plural(len(ann), 'column')}"]
        return []
    if node == "emit":
        rep = up.get("report") or {}
        n = len(rep.get("tables") or [])
        if n:
            return ["Report ready"]
        return []
    return []


def _stream(initial: dict):
    metrics = Metrics()
    initial["_metrics"] = metrics
    q: queue.Queue = queue.Queue()
    # Per-page extraction progress, pushed straight into the SSE queue from
    # inside the extract node -- a 10-page read narrates ten times instead
    # of going silent behind keepalives.
    initial["_progress"] = lambda msg: q.put(
        ("progress_line", {"node": "extract_chunks", "message": msg}))
    state = dict(initial)
    t0 = time.time()

    def run():
        try:
            for update in GRAPH.stream(initial, {"recursion_limit": 50},
                                       stream_mode="updates"):
                q.put(("update", update))
        except Exception as e:  # noqa: BLE001
            q.put(("error", str(e)))
        q.put(("done", None))

    threading.Thread(target=run, daemon=True).start()

    def gen():
        yield ("event: progress\ndata: "
               + json.dumps({"node": "upload",
                             "message": "Upload received. Reading document"})
               + "\n\n")
        while True:
            try:
                kind, payload = q.get(timeout=10)
            except queue.Empty:
                yield ": keepalive\n\n"
                continue
            if kind == "progress_line":
                yield ("event: progress\ndata: " + json.dumps(payload)
                       + "\n\n")
            elif kind == "update":
                for node, up in payload.items():
                    state.update(up or {})
                    for line in _narrate(node, up or {}, state):
                        yield ("event: progress\ndata: "
                               + json.dumps({"node": node, "message": line})
                               + "\n\n")
            elif kind == "error":
                state["report"] = {"overall_status": "pipeline_error",
                                   "validator_reason": payload}
                break
            else:
                break
        report = state.get("report") or {
            "overall_status": "pipeline_error",
            "validator_reason": "no report produced"}
        report["metrics"] = metrics.summary()
        report["metrics"]["elapsed_seconds"] = round(time.time() - t0, 1)
        yield ("event: report\ndata: "
               + json.dumps(report, default=str) + "\n\n")

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache"})


@app.get("/api/models")
def models():
    return {"models": [{"id": k, "provider": v.provider}
                       for k, v in MODEL_REGISTRY.items()]}


def _initial(**kw) -> dict:
    return {"doc_bytes": b"", "source_name": "", "media_type": None,
            "fragments": [], "chunks": [], "extraction_tier": "primary",
            "reextract_count": 0, "extraction_attempts": [], **kw}


@app.get("/")
def index():
    return FileResponse("static/index.html")


@app.get("/how")
def how():
    return FileResponse("static/how.html")


@app.get("/api/sample")
def sample():
    # Demo injects a pre-extracted fragment: perception is bypassed (no key
    # needed); stitching, the schema race, validation, analysis -- the
    # deterministic spine -- run for real. reextract budget pre-spent so
    # the planted decimal slip emits as a finding, not a model call.
    raw = demo_raw_table()
    frag = {"chunk_id": 0, "pages": [1], "headers": raw["headers"],
            "rows": raw["rows"], "position": 0, "notes": []}
    return _stream(_initial(fragments=[frag], source_name="sample_wip.pdf",
                            reextract_count=1))


@app.post("/api/scan")
async def scan(file: UploadFile, model: str = Form("")):
    data = await file.read()
    name = file.filename or "upload"
    override = (model.strip()
                if model and model.strip() in MODEL_REGISTRY else None)
    # Sniffing (pdf / image / xlsx / csv) now lives in the graph's own
    # ingest node; spreadsheets become fragments with no model call, and
    # their empty chunk list means the re-extract route can never fire on
    # them -- no budget pre-spend needed.
    return _stream(_initial(doc_bytes=data, source_name=name,
                            model_override=override))
