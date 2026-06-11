"""wipple.ai server: FastAPI + SSE streaming of pipeline node events."""

from __future__ import annotations

import json
import queue
import threading
import time

from fastapi import FastAPI, Form, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse

from wipple.demo import demo_raw_table
from wipple.ingest import csv_to_raw_table, sniff, xlsx_to_raw_table
from wipple.graph import build_graph
from wipple.model_client import MODEL_REGISTRY, Metrics

app = FastAPI(title="wipple")
# Allow the frontend to be served from a different origin (e.g. Firebase
# Hosting at wipple.ai) while the API lives here. No cookies or auth are
# involved, so a permissive policy is fine.
app.add_middleware(CORSMiddleware, allow_origins=["*"],
                   allow_methods=["*"], allow_headers=["*"])
GRAPH = build_graph()


def _narrate(node: str, up: dict, state: dict) -> list[str]:
    if node == "extract":
        rt = up.get("raw_table")
        if not rt:
            return ["Could not read the document."]
        tier = state.get("extraction_tier", "primary")
        msg = (f"Transcribed {len(rt['rows'])} rows x "
               f"{len(rt['headers'])} columns")
        return [("Re-reading with a stronger model... " if tier == "escalated"
                 else "Reading document... ") + msg.lower()]
    if node == "parse":
        pr = up.get("parse_report", {})
        out = [f"Parsed {pr.get('n_rows', 0)} jobs, "
               f"{pr.get('n_numeric_cols', 0)} numeric columns"]
        reps = [f for f in pr.get("cell_flags", [])
                if f["flag"] == "confusable_repair"]
        if reps:
            out.append(f"Repaired {len(reps)} OCR-damaged cell(s) in place")
        tc = pr.get("totals_check")
        if tc:
            out.append("Stated totals reconcile with column sums"
                       if tc["all_match"] else
                       "Stated totals do not match the column sums")
        return out
    if node == "validate":
        v = up.get("validation", {})
        st = v.get("status")
        if st == "success":
            nw = len(v.get("witnesses", []))
            return [f"Column mapping certified from {nw} accounting "
                    "identities, headers not used"]
        if st == "validation_failed":
            k = len(v.get("findings", []))
            return [f"{k} cell(s) fail the row identities, diagnosing"]
        return ["Too little numeric structure to certify, "
                "reading headers as fallback"]
    if node == "re_extract":
        return ["A value fails the row identities, "
                "re-reading the document independently"]
    if node == "fallback":
        n = len(up.get("fallback_mapping", {}))
        return [f"Headers mapped {n} columns, marked unverified"]
    if node == "disambiguate":
        return ["Two readings both certified, headers broke the tie"]
    if node == "analyze":
        a = up.get("analysis") or {}
        k = len(a.get("signals", []))
        return [f"Computed portfolio KPIs and {k} underwriting "
                f"signal{'s' if k != 1 else ''}"]
    return []


def _stream(initial: dict):
    metrics = Metrics()
    initial["_metrics"] = metrics
    state = dict(initial)
    q: queue.Queue = queue.Queue()
    t0 = time.time()

    def run():
        try:
            for update in GRAPH.stream(initial, stream_mode="updates"):
                q.put(("update", update))
        except Exception as e:  # noqa: BLE001
            q.put(("error", str(e)))
        q.put(("done", None))

    threading.Thread(target=run, daemon=True).start()

    def gen():
        # Heartbeat every 10s so proxies don't kill the connection while a
        # long model call is in flight.
        while True:
            try:
                kind, payload = q.get(timeout=10)
            except queue.Empty:
                yield ": keepalive\n\n"
                continue
            if kind == "update":
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
        report = state.get("report") or {"overall_status": "pipeline_error",
                                         "validator_reason": "no report produced"}
        report["metrics"] = metrics.summary()
        report["metrics"]["elapsed_seconds"] = round(time.time() - t0, 1)
        yield "event: report\ndata: " + json.dumps(report, default=str) + "\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache"})


@app.get("/api/models")
def models():
    return {"models": [{"id": k, "provider": v.provider}
                       for k, v in MODEL_REGISTRY.items()]}


def _initial(**kw) -> dict:
    return {"pdf_bytes": b"", "source_name": "", "raw_table": None,
            "extraction_tier": "primary", "reextract_count": 0,
            "extraction_attempts": [], **kw}


@app.get("/")
def index():
    return FileResponse("static/index.html")


@app.get("/api/sample")
def sample():
    # Demo injects a pre-transcribed table: extraction is bypassed (no key
    # needed); parse, validation, analysis -- the deterministic spine -- run
    # for real. reextract budget is pre-spent so the planted decimal slip
    # emits as a finding instead of looping into a model call.
    return _stream(_initial(raw_table=demo_raw_table(),
                            source_name="sample_wip.pdf",
                            reextract_count=1))


@app.post("/api/scan")
async def scan(file: UploadFile, model: str = Form("")):
    data = await file.read()
    name = file.filename or "upload"
    kind = sniff(data, name)
    override = model.strip() if model and model.strip() in MODEL_REGISTRY else None
    if kind == "xlsx":
        # spreadsheets carry their cells natively: deterministic ingest,
        # no model call, no re-extract loop to fall into
        return _stream(_initial(raw_table=xlsx_to_raw_table(data),
                                source_name=name, reextract_count=1))
    if kind == "csv":
        return _stream(_initial(raw_table=csv_to_raw_table(data),
                                source_name=name, reextract_count=1))
    media = kind if kind != "unknown" else "application/pdf"
    return _stream(_initial(pdf_bytes=data, media_type=media,
                            source_name=name, model_override=override))
