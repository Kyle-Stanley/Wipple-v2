# Wipple v2 — pipeline

Deterministic-first financial-table pipeline. Vision does perception; everything
that can be ordinary code or accounting math is ordinary code or accounting math.

## Document graph

```
ingest -> chunk -> extract page grids -> assemble logical tables -> validate/analyze
                                             |                        |
                                             +-- validator math ------+
```

The page vision call is deliberately schema-blind. It returns only visible table
grids (`headers` and `rows`). It does not classify WIP/CC, count rows, infer page
continuations, read reporting dates, or produce corrections.

`wipple/layout.py` owns reconstruction:

1. derive each returned grid's row/column shape in Python;
2. rule out mechanically impossible adjacent-page joins;
3. construct the remaining separate/vertical/horizontal layouts;
4. compare viable layouts with the existing WIP and CC validators;
5. return logical tables with row/page provenance.

No bounding boxes, pixel geometry, label-overlap thresholds, numeric-density
continuation rules, or page-level accounting classification decide assembly.
Schema selection, corrections, and underwriting analysis happen only after the
logical tables exist.

## Per-section graph

Each reconstructed logical table is parsed and validated. Clean WIP/CC sections
then run through the existing per-section graph:

```
parse -> validate -> disambiguate/fallback/re-extract -> analyze -> emit
```

The document graph owns page-aware extraction and re-extraction. The section graph
owns column mapping, findings, corrections, totals, and analysis.

## Provenance

Every logical-table row retains `(chunk_id, page, local_row)` provenance. Findings
and failures are mapped back to their source page. Horizontally reconstructed rows
retain provenance from every contributing page band.

## Module map

- `wipple/extraction.py` — schema-blind page table reader
- `wipple/reconstruction.py` — shape-only candidate generation and joins
- `wipple/layout_validation.py` — cheap validator-backed layout comparison
- `wipple/layout.py` — public fragment-to-logical-table assembly facade
- `wipple/docgraph.py` — document orchestration and page-aware re-extraction
- `wipple/wip_validator.py` — header-blind WIP validation
- `wipple/cc_validator.py` — header-blind completed-contract validation
- `wipple/parsing.py` — deterministic strings-to-matrix parsing
- `wipple/validation.py` — schema validation and serialization
- `wipple/splitting.py` — final WIP-format completed-contract row handling
- `wipple/block_misalign.py` — page-band alignment repair
- `wipple/concordance.py` — cross-table/document reconciliation
- `wipple/graph.py` — per-section LangGraph
- `wipple/analysis.py` — KPIs, signals, and correction application
- `wipple/model_client.py` — model client and metrics
- `wipple/ingest.py` — PDF/image/spreadsheet ingestion

## Parse-layer decisions

- **Blank → 0 (flagged), unparseable → NaN.** Visible certification failure is
  preferred over silently dropping a row.
- **Totals rows are stripped from validation, then checked.** This avoids poisoning
  ratio identities while retaining stated-versus-computed evidence.
- **Confusable repair is column-gated.** Job IDs cannot be silently converted into
  fabricated numbers.
- **Headers do not assign accounting variables.** They may support formatting or a
  fallback question only after deterministic math is exhausted.

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

Reporting dates, statement titles, narrative text, and other non-tabular document
features will be handled by a separate document-understanding pass. They are not
part of the page table reader's contract.
