"""
Extract node: PDF -> verbatim cell-string table via one vision call.

The contract is deliberately minimal. The model's ONLY job is perception:
transcribe what is printed, preserve column order, do not interpret. Every
instruction that asks the model to "fix", "normalize", or "compute" anything
moves work from the deterministic layer (auditable) to the stochastic layer
(not) -- so there are none.
"""

from __future__ import annotations

import logging

from .model_client import Metrics, extract_json, get_client
from .state import WippleState

logger = logging.getLogger(__name__)

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
    metrics: Metrics = state["_metrics"]  # injected by the runner
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
        attempts.append({"tier": tier, "ok": True, "rows": len(raw_table["rows"])})
        return {"raw_table": raw_table, "extraction_attempts": attempts}
    except Exception as e:  # noqa: BLE001 -- failure is a routed state, not a crash
        logger.exception("extraction failed on tier=%s", tier)
        attempts.append({"tier": tier, "ok": False, "error": str(e)})
        return {"raw_table": None, "extraction_attempts": attempts}


def re_extract_node(state: WippleState) -> dict:
    """Escalate tier, bump the retry counter; the graph loops back to extract.

    Carries the validator's cell-level findings nowhere on purpose: the
    re-extraction is independent. If the strong model independently produces
    the value the validator's identities implied, that is two independent
    witnesses agreeing -- feeding the expected value into the prompt would
    collapse them into one.
    """
    return {
        "extraction_tier": "escalated",
        "reextract_count": int(state.get("reextract_count", 0)) + 1,
    }


# ---------------------------------------------------------------------------
# v3: per-chunk extraction. The chunk is the perception unit; the page is
# provenance. The prompt's only addition over v2 is the tables array (a page
# can carry two distinct tables) -- every verbatim rule is unchanged.
# ---------------------------------------------------------------------------

_V2_SHAPE = (
    '{\n'
    '  "headers": ["<column header 1>", "..."],\n'
    '  "rows": [["<cell>", "<cell>", "..."], ...],\n'
    '  "page_count": <int>,\n'
    '  "notes": ["<anything unusual about the document structure>"]\n'
    '}')
_V3_SHAPE = (
    '{\n'
    '  "reporting_period_text": "<exact printed reporting-period phrase or null>",\n'
    '  "tables": [\n'
    '    {"headers": ["<column header 1>", "..."],\n'
    '     "rows": [["<cell>", "<cell>", "..."], ...],\n'
    '     "position": <0-based order of this table on the page>,\n'
    '     "notes": ["<anything unusual>"]}\n'
    '  ]\n'
    '}\n\n'
    'If the page continues a table from a previous page and reprints no\n'
    'headers, return "headers" as a list of empty strings matching the\n'
    'column count. If the page holds TWO separate tables (e.g. contracts in\n'
    'progress and completed contracts), return both, in reading order.')

_MULTIPAGE_RULE = (
    '4. If the table spans multiple pages, the columns are the same on every\n'
    '   page: continue the same rows array across pages in reading order.\n'
    '   Do not repeat header rows as data rows.')
_ONE_PAGE_RULE = (
    '4. This input contains only one page or image slice. Transcribe only the\n'
    '   rows visible in this input; never infer, repeat, or carry over rows\n'
    '   from pages you cannot see. Do not include a repeated header as data.')

CHUNK_PROMPT = EXTRACTION_PROMPT.replace(
    'Return ONLY a JSON object with this exact shape:',
    'This is ONE PAGE (or one slice) of a possibly longer document. '
    'Return ONLY a JSON object with this exact shape:').replace(
    _V2_SHAPE, _V3_SHAPE).replace(
    _MULTIPAGE_RULE, _ONE_PAGE_RULE) + """

For reporting_period_text, copy the exact printed phrase that states the
schedule's reporting or period-end date (for example, "Year ended December
31, 2025"). If this page does not print one, return null. Do not guess a date
from the table values or page number."""

# Haiku performs this task more reliably with the same terse instruction used
# in Claude.ai. The output schema already communicates the JSON shape, so do
# not make the smaller model spend its attention re-reading that contract.
HAIKU_CHUNK_PROMPT = """Extract every visible table from the attached PDF page.
Transcribe every header and every row exactly as printed, including blank
cells. Tables may be untitled and may use aligned columns instead of borders.
If any aligned header/data rows are visible, the tables array must not be
empty. For reporting_period_text, copy the printed reporting-date phrase or
return null. Use position for each table's top-to-bottom reading order and put
any uncertainty in notes."""

_HAIKU_MODEL_IDS = {"claude-haiku-4-5", "claude-haiku-4-5-20251001"}


def _chunk_prompt(model_override: str | None) -> str:
    if (model_override or "").strip() in _HAIKU_MODEL_IDS:
        return HAIKU_CHUNK_PROMPT
    return CHUNK_PROMPT

CHUNK_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "reporting_period_text": {"type": ["string", "null"]},
        "tables": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "headers": {"type": "array", "items": {"type": "string"}},
                "rows": {"type": "array", "items": {
                    "type": "array", "items": {"type": "string"}}},
                "position": {"type": "integer"},
                "notes": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["headers", "rows", "position", "notes"],
            "additionalProperties": False,
        }},
    },
    "required": ["reporting_period_text", "tables"],
    "additionalProperties": False,
}


def extract_chunks_node(state) -> dict:
    """Extract every pending chunk (all on the first pass; the re-queued
    subset on the escalated retry). Fragments accumulate with provenance."""
    chunks = state.get("chunks") or []
    pending = state.get("bad_chunks")
    tier = state.get("extraction_tier", "primary")
    metrics = state["_metrics"]
    attempts = list(state.get("extraction_attempts", []))
    fragments = [f for f in (state.get("fragments") or [])
                 if pending is None or f["chunk_id"] not in set(pending)]
    failed = []
    for ch in chunks:
        if pending is not None and ch["chunk_id"] not in set(pending):
            continue
        try:
            text = get_client().generate(
                _chunk_prompt(state.get("model_override")), tier=tier,
                pdf_bytes=ch["bytes"],
                media_type=ch["media_type"], json_only=True,
                model_override=state.get("model_override") or None,
                output_schema=CHUNK_OUTPUT_SCHEMA,
                metrics=metrics,
                purpose=f"extract[chunk={ch['chunk_id']},{tier}]")
            obj = extract_json(text)
            period_text = obj.get("reporting_period_text")
            for t in obj.get("tables", []):
                fragments.append({
                    "chunk_id": ch["chunk_id"], "pages": ch["pages"],
                    "headers": [str(h) for h in t.get("headers", [])],
                    "rows": [[str(c) for c in r] for r in t.get("rows", [])],
                    "position": int(t.get("position", 0)),
                    "notes": [str(n) for n in t.get("notes", [])],
                    "reporting_period_text": (
                        str(period_text) if period_text else None),
                    "overlaps_prev": bool(ch.get("overlaps_prev"))})
            attempts.append({"chunk": ch["chunk_id"], "tier": tier,
                             "ok": True})
        except Exception as e:  # noqa: BLE001 -- routed state, not a crash
            logger.exception("chunk %s extraction failed", ch["chunk_id"])
            attempts.append({"chunk": ch["chunk_id"], "tier": tier,
                             "ok": False, "error": str(e)})
            failed.append(ch["chunk_id"])
    return {"fragments": fragments, "extraction_attempts": attempts,
            "failed_chunks": failed, "bad_chunks": None}
