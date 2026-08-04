"""
Deterministic sparse-header fallback + mapping disambiguator.

The schema race has one separate, narrower text use: exact title/header
vocabulary can resolve the sparse WIP/CC triangle because those two schemas
are algebraically identical there. This module handles column mapping only
after the math reports insufficient information.

fallback   -- INSUFFICIENT without a competing mapping: match the transcribed
              headers against the seeded accounting synonym corpus. Exact
              matches and one clear close match are accepted; ambiguity stays
              unassigned. No model call is made.

disambiguate -- INSUFFICIENT with a competing mapping: the math certified two
              incomparable readings. This is NOT a full remap; the LLM
              answers exactly one question (the validator literally supplies
              it in suggested_disambiguator), choosing between two complete,
              already-certified mappings.
"""

from __future__ import annotations

import logging

from ..core.model_client import Metrics, extract_json, get_client
from ..core.state import WippleState
from ..accounting.header_names import match_header
from ..accounting.schemas import (
    ALL_VAR_NAMES as VAR_NAMES,
    CC_VAR_NAMES,
    WIP_VAR_NAMES,
)

logger = logging.getLogger(__name__)

_GLOSSARY = "\n".join(f"  {code}: {name}" for code, name in VAR_NAMES.items())

DISAMBIGUATION_PROMPT = """A deterministic math engine certified TWO incomparable column mappings for a contractor WIP schedule -- the numbers alone cannot break the tie, but the column headers can.

Variable schema (code: meaning):
{glossary}

Column headers by index:
{headers}

Mapping A: {mapping_a}
Mapping B: {mapping_b}

The engine suggests the deciding question is: {question}

Using ONLY the headers, decide which mapping is correct.
Return ONLY JSON: {{"chosen": "A" or "B", "rationale": "<one sentence>"}}"""

DISAMBIGUATION_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "chosen": {"type": "string", "enum": ["A", "B"]},
        "rationale": {"type": "string"},
    },
    "required": ["chosen", "rationale"],
    "additionalProperties": False,
}


def fallback_node(state: WippleState) -> dict:
    """Map only headers that have one conservative corpus match."""
    v = state.get("validation", {})
    allowed = (CC_VAR_NAMES if v.get("schema") == "cc" else WIP_VAR_NAMES)
    raw = state.get("raw_table") or {}
    headers = raw.get("headers", [])
    candidates: dict[int, dict] = {}
    by_variable: dict[str, list[int]] = {}
    for mcol, doc_col in enumerate(state.get("numeric_col_map", [])):
        header = headers[doc_col] if doc_col < len(headers) else ""
        matched = match_header(header, allowed)
        if matched:
            candidates[mcol] = matched
            by_variable.setdefault(matched["variable"], []).append(mcol)

    # Two columns claiming the same variable is ambiguous; accept neither.
    mapping = {
        mcol: match["variable"] for mcol, match in candidates.items()
        if len(by_variable[match["variable"]]) == 1
    }
    kinds = {
        mcol: candidates[mcol]["match"] for mcol in mapping
    }
    return {
        "fallback_mapping": mapping,
        "fallback_confidence": kinds,
        "fallback_notes": (
            "Too little numeric structure for mathematical validation. "
            "Columns were matched from their headers."
            if mapping else
            "Too little numeric structure for mathematical validation. "
            "The column headers were not clear enough to match."
        ),
    }


def disambiguate_node(state: WippleState) -> dict:
    v = state.get("validation", {})
    metrics: Metrics = state["_metrics"]
    raw = state.get("raw_table") or {}
    col_map = state.get("numeric_col_map", [])
    headers = raw.get("headers", [])

    def _doc_header(mcol: int) -> str:
        j = col_map[mcol] if mcol < len(col_map) else -1
        return headers[j] if 0 <= j < len(headers) else "(no header)"

    headers_block = "\n".join(
        f"  col {mcol}: \"{_doc_header(mcol)}\"" for mcol in range(len(col_map)))
    prompt = DISAMBIGUATION_PROMPT.format(
        glossary=_GLOSSARY,
        headers=headers_block,
        mapping_a=v.get("mapping", {}),
        mapping_b=v.get("competing_mapping", {}),
        question=v.get("suggested_disambiguator") or
        "which reading matches the headers",
    )
    try:
        text = get_client().generate(prompt, tier="fallback", json_only=True,
                                     model_override=state.get("model_override") or None,
                                     output_schema=DISAMBIGUATION_OUTPUT_SCHEMA,
                                     metrics=metrics, purpose="disambiguation")
        obj = extract_json(text)
        chosen = "competing" if str(obj.get("chosen", "A")).upper() == "B" \
            else "best"
        return {"disambiguation": {"chosen": chosen,
                                   "rationale": str(obj.get("rationale", ""))}}
    except Exception as e:  # noqa: BLE001
        logger.exception("disambiguation failed")
        return {"disambiguation": {"chosen": "best",
                                   "rationale": f"LLM call failed ({e}); "
                                   "kept the engine's higher-scoring reading"}}
