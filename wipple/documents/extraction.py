"""Vision extraction: printed tables -> verbatim cell-string grids.

The model's only job is perception. It finds each table visible in the input,
transcribes the printed cells, and preserves row/column order. It does not
classify schedules, infer continuations, count its output, assign accounting
meaning, fix values, or summarize the page.
"""

from __future__ import annotations

import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor

from ..core.model_client import Metrics, extract_json, get_client
from ..core.state import WippleState

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Legacy v2 whole-document extraction. Kept while the standalone section graph
# remains supported; the live document path uses CHUNK_PROMPT.
# ---------------------------------------------------------------------------

EXTRACTION_PROMPT = """You are transcribing a contractor Work-in-Progress (WIP) schedule.

Return ONLY a JSON object with this exact shape:

{
  "headers": ["<column header 1>", "..."],
  "rows": [["<cell>", "<cell>", "..."], ...],
  "page_count": <int>,
  "notes": ["<anything unusual about the document structure>"]
}

Rules -- these matter more than anything else:
1. Transcribe every cell VERBATIM, as a string, exactly as printed:
   keep commas, periods, parentheses, $ signs, % signs, and dashes.
   "1,234.56" stays "1,234.56". "(45,000)" stays "(45,000)". "-" stays "-".
2. Do NOT compute, correct, round, reformat, or normalize any value.
   If a printed number looks wrong, transcribe it wrong.
3. Preserve the left-to-right column order of the document. Every row must
   have the same number of cells as there are headers; use "" for cells
   that are blank on the page.
4. If the table spans multiple pages, the columns are the same on every
   page: continue the same rows array across pages in reading order.
   Do not repeat header rows as data rows.
5. Include total/subtotal rows as ordinary rows (transcribe their label in
   the same position as job names). Downstream logic handles them.
6. Include every row of the job table. Exclude narrative text, footers,
   page numbers, and accountant letterhead.
7. The first column is usually a job name/number: transcribe it as printed.

Return the JSON object and nothing else."""

EXTRACTION_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "headers": {"type": "array", "items": {"type": "string"}},
        "rows": {"type": "array", "items": {
            "type": "array", "items": {"type": "string"}}},
        "page_count": {"type": "integer"},
        "notes": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["headers", "rows", "page_count", "notes"],
    "additionalProperties": False,
}


def extract_node(state: WippleState) -> dict:
    tier = state.get("extraction_tier", "primary")
    metrics: Metrics = state["_metrics"]
    attempts = list(state.get("extraction_attempts", []))

    try:
        text = get_client().generate(
            EXTRACTION_PROMPT,
            tier=tier,
            pdf_bytes=state["pdf_bytes"],
            media_type=state.get("media_type") or "application/pdf",
            model_override=state.get("model_override") or None,
            json_only=True,
            output_schema=EXTRACTION_OUTPUT_SCHEMA,
            metrics=metrics,
            purpose=f"extraction[{tier}]",
        )
        obj = extract_json(text)
        raw_table = {
            "headers": [str(h) for h in obj.get("headers", [])],
            "rows": [[str(c) for c in r] for r in obj.get("rows", [])],
            "page_count": int(obj.get("page_count", 1) or 1),
            "notes": [str(n) for n in obj.get("notes", [])],
        }
        attempts.append({"tier": tier, "ok": True,
                         "rows": len(raw_table["rows"])})
        return {"raw_table": raw_table, "extraction_attempts": attempts}
    except Exception as e:  # noqa: BLE001 -- routed state, not a crash
        logger.exception("extraction failed on tier=%s", tier)
        attempts.append({"tier": tier, "ok": False, "error": str(e)})
        return {"raw_table": None, "extraction_attempts": attempts}


def re_extract_node(state: WippleState) -> dict:
    """Escalate tier; re-extraction remains independent of proposed repairs."""
    return {
        "extraction_tier": "escalated",
        "reextract_count": int(state.get("reextract_count", 0)) + 1,
    }


# ---------------------------------------------------------------------------
# Page reader. The page is the perception unit. The table contract contains
# only printed grids; one separate page-level field preserves an exact printed
# reporting-period phrase for deterministic date parsing downstream.
# ---------------------------------------------------------------------------

CHUNK_PROMPT = """You are a table detector and table reader.

This input contains exactly one page or image slice from a possibly longer
financial document. Find every distinct table visible in this input and
transcribe each one.

Return ONLY a JSON object with this exact shape:

{
  "reporting_period_text": "<exact printed reporting-period phrase or null>",
  "tables": [
    {
      "title_text": "<exact printed title attached to this table, or null>",
      "headers": ["<column header 1>", "..."],
      "rows": [["<cell>", "<cell>", "..."], ...]
    }
  ]
}

Rules -- these matter more than anything else:
1. Transcribe every visible table cell VERBATIM as a string. Keep commas,
   periods, parentheses, currency signs, percent signs, and dashes exactly as
   printed. If a printed number looks wrong, transcribe it wrong.
2. Do NOT compute, correct, round, normalize, classify, summarize, or assign
   accounting meaning to anything.
3. Preserve each table's printed top-to-bottom row order and left-to-right
   column order. Use "" for a blank cell so rows retain their column shape.
4. Transcribe only what is visible in this input; never infer, repeat, or carry over rows or columns from another page.
5. If a continued table prints no headers on this page, return an empty string
   for each visible column header.
6. Do not include repeated header rows as data rows.
7. Include printed total and subtotal rows as ordinary rows. Downstream code
   determines how they are used.
8. For title_text, copy only the exact printed title or section heading
   visibly attached to that table, such as "Schedule of Completed Contracts".
   Return null when the table has no printed title on this page. Do not
   classify the table or invent a title.
9. If the page visibly contains multiple separate tables, return each as its
   own item in top-to-bottom reading order.
10. Exclude narrative paragraphs, page numbers, signatures, letterhead, and
   other non-tabular page content.
11. For reporting_period_text, copy only an exact phrase printed on this page
    that states a reporting or period-end date, such as "Year ended December
    31, 2025". Return null when no such phrase is printed. Do not infer a date,
    classify the document, or use dates found only inside table rows.
12. If there is no table on the page, return an empty tables array. Still
    return reporting_period_text when a reporting-period phrase is visible.

Return the JSON object and nothing else."""

CHUNK_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "reporting_period_text": {"type": ["string", "null"]},
        "tables": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "title_text": {"type": ["string", "null"]},
                "headers": {"type": "array", "items": {"type": "string"}},
                "rows": {"type": "array", "items": {
                    "type": "array", "items": {"type": "string"}}},
            },
            "required": ["title_text", "headers", "rows"],
            "additionalProperties": False,
        }},
    },
    "required": ["reporting_period_text", "tables"],
    "additionalProperties": False,
}


# One page's transcription never informs another's -- the prompt forbids
# carrying anything across pages -- so the reads are independent calls and run
# concurrently. Results are merged back in document order, leaving fragment
# ordering, provenance, and the attempt log identical to a serial read.
EXTRACT_CONCURRENCY = max(
    1, int(os.environ.get("WIPPLE_EXTRACT_CONCURRENCY", "8") or 8))


def _read_chunk(ch, tier, state, metrics) -> tuple:
    """Transcribe one chunk. Returns (obj, error); never raises."""
    try:
        text = get_client().generate(
            CHUNK_PROMPT, tier=tier, pdf_bytes=ch["bytes"],
            media_type=ch["media_type"], json_only=True,
            model_override=state.get("model_override") or None,
            output_schema=CHUNK_OUTPUT_SCHEMA,
            metrics=metrics,
            purpose=f"extract[chunk={ch['chunk_id']},{tier}]")
        return extract_json(text), None
    except Exception as e:  # noqa: BLE001 -- routed state, not a crash
        logger.exception("chunk %s extraction failed", ch["chunk_id"])
        return None, e


def extract_chunks_node(state) -> dict:
    """Extract pending pages into schema-blind table fragments.

    The caller supplies provenance. Table order is simply the order of the
    returned list; shape metadata is deliberately not requested from the model.
    """
    chunks = state.get("chunks") or []
    pending = state.get("bad_chunks")
    pending_set = None if pending is None else set(pending)
    tier = state.get("extraction_tier", "primary")
    metrics = state["_metrics"]
    attempts = list(state.get("extraction_attempts", []))
    fragments = [f for f in (state.get("fragments") or [])
                 if pending_set is None or f["chunk_id"] not in pending_set]
    period_texts = [
        item for item in (state.get("reporting_period_texts") or [])
        if pending_set is None or item.get("chunk_id") not in pending_set
    ]
    failed = []

    todo = [ch for ch in chunks
            if pending_set is None or ch["chunk_id"] in pending_set]
    total = len(todo)
    # Pages finish out of order, so the live line counts completions rather
    # than naming a page: "Read 3 of 10 pages" stays true either way.
    unit = ("strip" if str(state.get("media_type") or "").startswith("image/")
            else "page")
    progress = state.get("_progress") if total > 1 else None
    counter = {"done": 0}
    counter_lock = threading.Lock()

    def read(ch):
        result = _read_chunk(ch, tier, state, metrics)
        if progress:
            with counter_lock:
                counter["done"] += 1
                done = counter["done"]
            progress(f"Read {done} of {total} {unit}s")
        return result

    workers = min(EXTRACT_CONCURRENCY, total)
    if workers > 1:
        with ThreadPoolExecutor(max_workers=workers,
                                thread_name_prefix="wipple-extract") as pool:
            results = list(pool.map(read, todo))   # input order, not completion
    else:
        results = [read(ch) for ch in todo]

    for ch, (obj, error) in zip(todo, results):
        if error is not None:
            attempts.append({"chunk": ch["chunk_id"], "tier": tier,
                             "ok": False, "error": str(error)})
            failed.append(ch["chunk_id"])
            continue
        tables = obj.get("tables", [])
        period_text = obj.get("reporting_period_text")
        if period_text:
            period_texts.append({
                "chunk_id": ch["chunk_id"],
                "pages": list(ch["pages"]),
                "text": str(period_text),
            })
        for table_index, table in enumerate(tables):
            fragments.append({
                "chunk_id": ch["chunk_id"],
                "pages": ch["pages"],
                "table_index": table_index,
                "title_text": (str(table.get("title_text"))
                               if table.get("title_text") else None),
                "headers": [str(h) for h in table.get("headers", [])],
                "rows": [[str(c) for c in row]
                         for row in table.get("rows", [])],
                "overlaps_prev": bool(ch.get("overlaps_prev")),
            })
        attempts.append({"chunk": ch["chunk_id"], "tier": tier,
                         "ok": True, "tables": len(tables)})

    return {"fragments": fragments,
            "reporting_period_texts": period_texts,
            "extraction_attempts": attempts,
            "failed_chunks": failed, "bad_chunks": None}
