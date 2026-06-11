"""
Header fallback + disambiguator: the ONLY nodes allowed to read headers
semantically.

fallback   -- INSUFFICIENT without a competing mapping: the document is too
              sparse for the math to certify. The LLM assigns variables from
              headers, but it does not start from zero: the validator's
              uncertified best mapping and its reason string are injected as
              soft constraints, and any variable the validator did place is
              presented as the stronger prior.

disambiguate -- INSUFFICIENT with a competing mapping: the math certified two
              incomparable readings. This is NOT a full remap; the LLM
              answers exactly one question (the validator literally supplies
              it in suggested_disambiguator), choosing between two complete,
              already-certified mappings.
"""

from __future__ import annotations

import logging

from .model_client import Metrics, extract_json, get_client
from .state import WippleState
from .wip_validator import VAR_NAMES

logger = logging.getLogger(__name__)

_GLOSSARY = "\n".join(f"  {code}: {name}" for code, name in VAR_NAMES.items())

FALLBACK_PROMPT = """You are mapping the columns of a contractor Work-in-Progress (WIP) schedule to a fixed variable schema. A deterministic math engine already tried to identify the columns from the numbers alone and could not fully certify a mapping; you are the fallback that uses the column HEADERS.

Variable schema (code: meaning):
{glossary}

Columns to map (index, header, first sample values):
{columns}

What the math engine reported:
- Reason it could not certify: {reason}
- Its best uncertified guess (treat as a strong prior; only override with a
  clearly contradicting header): {prior}

Rules:
1. Map each column index to ONE variable code from the schema, or to null if
   no variable fits (e.g. a date, a job-type tag, an unrelated memo column).
2. Never assign the same variable code to two columns.
3. The schema is GAAP percentage-of-completion accounting. Use standard
   surety/CPA conventions: "Billed to Date"/"BTD" is B; "Cost to Date"/"CTD"
   /"Costs Incurred" is D; "Revenues Earned"/"Earned Revenue" is E;
   "Contract Price"/"Contract Amount" is V; "Estimated Cost" (total, not to
   complete) is C; "Cost to Complete"/"CTC" is Q.
4. Report a confidence ("high"|"medium"|"low") per assignment.

Return ONLY JSON:
{{"mapping": {{"<col index>": "<VAR or null>", ...}},
  "confidence": {{"<col index>": "high|medium|low", ...}},
  "notes": "<one short paragraph on anything ambiguous>"}}"""

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


def _columns_block(state: WippleState) -> str:
    raw = state.get("raw_table") or {}
    headers = raw.get("headers", [])
    rows = raw.get("rows", [])
    lines = []
    for mcol, j in enumerate(state.get("numeric_col_map", [])):
        h = headers[j] if j < len(headers) else "(no header)"
        samples = [r[j] for r in rows[:4] if j < len(r)]
        lines.append(f"  matrix col {mcol} (doc col {j}): \"{h}\" "
                     f"samples={samples}")
    return "\n".join(lines) or "  (none)"


def fallback_node(state: WippleState) -> dict:
    v = state.get("validation", {})
    metrics: Metrics = state["_metrics"]
    prior = (v.get("diagnostics") or {}).get("uncertified_best_mapping") or {}
    prompt = FALLBACK_PROMPT.format(
        glossary=_GLOSSARY,
        columns=_columns_block(state),
        reason=v.get("reason", "(none given)"),
        prior=prior or "(none)",
    )
    try:
        text = get_client().generate(prompt, tier="fallback", json_only=True,
                                     metrics=metrics, purpose="header_fallback")
        obj = extract_json(text)
        raw_mapping = obj.get("mapping", {}) or {}
        conf = obj.get("confidence", {}) or {}
        mapping, seen = {}, set()
        for k, var in raw_mapping.items():
            if var is None or var not in VAR_NAMES or var in seen:
                continue
            mapping[int(k)] = var
            seen.add(var)
        return {
            "fallback_mapping": mapping,
            "fallback_notes": str(obj.get("notes", "")),
            "fallback_confidence": {int(k): str(c) for k, c in conf.items()
                                    if int(k) in mapping},
        }
    except Exception as e:  # noqa: BLE001
        logger.exception("header fallback failed")
        return {"fallback_mapping": {},
                "fallback_notes": f"fallback LLM call failed: {e}"}


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
