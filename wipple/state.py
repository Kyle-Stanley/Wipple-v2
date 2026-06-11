"""
Graph state for the Wipple pipeline.

One state object flows through every node. Fields are grouped by which node
owns (writes) them; everything else treats them as read-only.

Design rules
------------
- Headers are QUARANTINED: they live in `raw_table["headers"]` and may be
  read only by the fallback and disambiguator nodes (semantic use) and by
  the parse node for FORMATTING decisions only (e.g. a '%' in a header may
  inform percent-scale normalization, never variable assignment).
- `validation` is a plain serializable dict (numpy already converted), so
  LangGraph checkpointing works if we turn it on later. The live
  ValidationResult object never enters state.
- `extraction_tier` + `reextract_count` drive the single-retry escalation
  loop: validate -> re_extract -> extract -> parse -> validate.
"""

from __future__ import annotations

from typing import Any, Optional, TypedDict


class RawTable(TypedDict):
    """Verbatim output of the vision extraction call (post JSON-parse)."""
    headers: list[str]          # quarantined -- see module docstring
    rows: list[list[str]]       # cell strings exactly as printed on the page
    page_count: int
    notes: list[str]            # extractor's own remarks (multi-page stitch etc.)


class WippleState(TypedDict, total=False):
    # ---- input -------------------------------------------------------------
    pdf_bytes: bytes
    media_type: str                 # application/pdf | image/* for vision path
    model_override: Optional[str]   # pinned model id, or None for tiering
    source_name: str

    # ---- extract node ------------------------------------------------------
    raw_table: Optional[RawTable]
    extraction_tier: str            # "primary" | "escalated"
    extraction_attempts: list[dict]  # [{tier, model_id, ok, error}]

    # ---- parse node ----------------------------------------------------------
    matrix: Optional[Any]           # 2-D numpy array (rows x numeric cols)
    job_labels: list[str]
    numeric_col_map: list[int]      # matrix col j -> original raw_table col
    parse_report: dict              # flags, totals check, dropped cols, scaling

    # ---- validate node -------------------------------------------------------
    validation: dict                # serialized ValidationResult
    reextract_count: int

    # ---- fallback / disambiguate nodes ---------------------------------------
    fallback_mapping: dict          # matrix col -> var code (LLM-assigned)
    fallback_confidence: dict       # matrix col -> "high"|"medium"|"low"
    fallback_notes: str
    disambiguation: dict            # {"chosen": "best"|"competing", "rationale": str}

    # ---- analyze node ------------------------------------------------------------
    analysis: dict                  # kpis, signals, provenance basis
    table: Any                      # serialized matrix view for UI/CSV

    # ---- emit node -------------------------------------------------------------
    report: dict                    # final assembled output

    # ---- runner-injected (not a node output) ------------------------------------
    _metrics: Any                   # model_client.Metrics, shared across nodes
