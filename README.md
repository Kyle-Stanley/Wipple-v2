# Wipple v2 — pipeline

Deterministic-first financial-table pipeline. Vision does perception; everything
that can be ordinary code or accounting math is ordinary code or accounting math.

## Document graph

```
ingest -> chunk -> extract page grids -> assemble logical tables -> validate/analyze
                                             |                        |
                                             +-- validator math ------+
```

The page vision call is deliberately schema-blind. It returns visible table
grids (`headers` and `rows`), the exact printed title attached to each table,
and one separate exact reporting-period phrase when printed on the page. It
does not classify WIP/CC, count rows, infer page continuations, interpret the
date, or produce corrections.

`wipple/reconstruction/layout.py` owns reconstruction:

1. derive each returned grid's row/column shape in Python;
2. rule out mechanically impossible adjacent-page joins;
3. construct the remaining separate/vertical/horizontal layouts;
4. compare viable layouts with the existing WIP and CC validators;
5. return logical tables with row/page provenance.

No bounding boxes, pixel geometry, label-overlap thresholds, numeric-density
continuation rules, or page-level accounting classification decide assembly.
Schema selection, corrections, and underwriting analysis happen only after the
logical tables exist. Numeric identities decide schema except for the sparse
revenue/cost/profit triangle, where WIP and completed contracts are
algebraically identical; only there may an exact printed title or
schema-specific header resolve the type.

## Per-section graph

Each reconstructed logical table is parsed and validated. Clean WIP/CC sections
then run through the existing per-section graph:

```
parse -> validate -> disambiguate/header-match/re-extract -> analyze -> emit
```

The document graph owns page-aware extraction and re-extraction. The section graph
owns column mapping, findings, corrections, totals, and analysis.

## Provenance

Every logical-table row retains `(chunk_id, page, local_row)` provenance. Findings
and failures are mapped back to their source page. Horizontally reconstructed rows
retain provenance from every contributing page band.

## Package map

- `wipple/core/` — shared graph state, model clients, and metrics
- `wipple/documents/` — ingestion, page chunking, table reading, and dates
- `wipple/reconstruction/` — candidate joins, scoring, splitting, and alignment
- `wipple/accounting/` — parsing, WIP/CC schemas, validation, and analysis
- `wipple/pipeline/` — per-section and document-level orchestration
- `wipple/support/` — demo data, deterministic corpus, and acceptance gates

The root `wipple` package remains the small public API for `build_graph`,
`run_pipeline`, parsing helpers, and the WIP validator.

## Parse-layer decisions

- **Blank → 0 (flagged), unparseable → NaN.** Visible certification failure is
  preferred over silently dropping a row.
- **Totals rows are stripped from validation, then checked.** This avoids poisoning
  ratio identities while retaining stated-versus-computed evidence.
- **Confusable repair is column-gated.** Job IDs cannot be silently converted into
  fabricated numbers.
- **Headers never create mathematical certification.** On sparse schedules,
  conservative synonym matches may assign columns as an explicitly unverified
  fallback after deterministic math is exhausted. The browser then presents the
  verbatim extracted grid for review, with a three-part readiness check for
  profitability, progress, and billing inputs.

## Run the site

```
pip install langgraph numpy scipy fastapi uvicorn python-multipart google-genai anthropic
export GOOGLE_API_KEY=...        # only needed for real uploads
uvicorn server:app --port 8000
```

The sample schedule works without keys by injecting pre-transcribed grids.

Tests:

```
python -m pytest tests/ -q
```

## Future document metadata

The exact reporting-period phrase is parsed deterministically into a date.
Statement titles, narrative text, and other non-tabular document features remain
outside the page table reader's contract.
