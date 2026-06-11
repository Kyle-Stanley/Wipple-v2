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
            json_only=True,
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
