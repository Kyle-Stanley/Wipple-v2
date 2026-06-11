# Wipple v2 — pipeline

Deterministic-first WIP schedule pipeline. One vision call does perception;
everything that can be math, is math.

## Graph

```
extract ──► parse ──► validate ──┬─► emit                      success
   ▲                             ├─► disambiguate ─► emit      two certified readings
   │                             ├─► fallback ─────► emit      sparse / uncertifiable
   └────── re_extract ◄──────────┘                             OCR-shaped failure (×1)
```

Routing keys off `validate_wip`'s three statuses plus the `Finding`
classifications:

| Validator outcome | Route | Cost behavior |
|---|---|---|
| `success` | emit | one Flash-Lite call total |
| `insufficient` + competing mapping | disambiguate — LLM answers the validator's one suggested question from headers | +1 tiny call |
| `insufficient` (sparse) | header fallback — LLM maps headers with the validator's uncertified prior as a soft constraint | +1 tiny call |
| `validation_failed`, OCR-shaped findings | re-extract once on the escalated tier | +1 strong call, max |
| `validation_failed`, persists or flatly wrong | emit as **underwriting finding** (first-class output, with culprit cell + implied correction) | no retry burned |

The re-extraction is deliberately blind: it never sees the validator's
proposed correction. If the strong model independently reproduces the value
the identities imply, that's two independent witnesses; feeding it the
answer would collapse them into one.

## Provenance tiers (per column, in every report)

`math-verified` → `math-identified` → `math-constrained-llm` → `llm-only`,
plus `virtual` for identity-derived non-physical columns.

## Module map

- `wipple/wip_validator.py` — the header-blind validation engine (unchanged)
- `wipple/parsing.py` — deterministic strings→matrix: decimal-convention
  detection (per-document), dash/blank→0, paren negatives,
  confusable repair *only after* strict parse fails, percent→fraction
  scaling, totals-row strip + stated-vs-computed check
- `wipple/extraction.py` — verbatim-transcription vision contract + nodes
- `wipple/fallback.py` — the only two nodes allowed to read headers semantically
- `wipple/validation.py` — parse/validate nodes, full result serialization
- `wipple/routing.py` — conditional edges + emit (report assembly)
- `wipple/graph.py` — LangGraph wiring, `run_pipeline()`
- `wipple/model_client.py` — slim 2-tier client (env-overridable models)
- `wipple/ingest.py` — file sniffing; xlsx/csv read deterministically (zero model calls)
- `wipple/analysis.py` — KPIs, continuously-scored signals, correction proposals
- `wipple/demo.py` — bundled sample schedule for the no-keys demo path

## Parse-layer decisions (and why)

- **Blank → 0 (flagged), unparseable → NaN.** Blank-as-NaN drops whole rows
  inside the validator and can silently degrade a dense doc to
  `insufficient`; blank-as-zero, when wrong, produces a visible
  certification failure pointing at exactly that cell. Visible beats silent.
- **Totals rows are stripped, then used.** Left in the matrix they
  partially satisfy additive identities while corrupting ratio ones — a
  poisoned hypothesis space. Stripped, the stated-vs-computed comparison is
  a free deterministic pre-check (`totals_check` in every report).
- **Confusable repair is column-gated.** OCR-style repair (O→0, S→5, ...)
  only runs on cells whose column already qualified as numeric on
  strict-parse evidence alone. Job IDs like `S101`/`B204` and addresses can
  never be "repaired" into fabricated numbers; a majority-corrupted column
  is dropped and reported, not invented. Every repair that does fire is
  flagged with the original raw string.
- **Headers are quarantined.** Fallback/disambiguator may read them
  semantically; parse may use them for *formatting* only (a `%` glyph
  informing scale normalization), never for variable assignment.

## Run the site

```
pip install langgraph numpy scipy fastapi uvicorn python-multipart google-genai anthropic
export GOOGLE_API_KEY=...        # only needed for real uploads
uvicorn server:app --port 8000   # then open http://localhost:8000
```

"Run the sample schedule" works with NO keys: it injects a bundled
pre-transcribed 12-job book (planted decimal slip, trapped cash, job
borrow, loss job, margin outlier) and runs the full deterministic spine --
parse, validation, analysis, certificate, dashboard -- for real.

CLI alternative: `python run_wipple.py some_wip.pdf`

## Presentation layer (static/index.html + server.py)

One page, three acts: upload -> streamed pipeline narrative (real LangGraph
node events over SSE) -> certificate interstitial -> dashboard.
- The certificate is the ONLY place verification speaks. It leads with what
  was CAUGHT, not with self-congratulation; a clean doc gets two lines.
- The dashboard is silent-by-default on provenance: six naked KPIs,
  findings ("can I trust this schedule?") and signals ("should I write this
  bond?") never interleaved, table collapsed at the bottom as the receipt.
  Provenance only intrudes when something did NOT certify (honest banner,
  hedged read).
- Signals are deterministic and continuously scored (no cliff edges); every
  threshold is a placeholder in `wipple/analysis.py:TUNE` awaiting the real
  rulebook. The LLM writes no prose anywhere on the page.
- Findings with an identity-implied correction are applied to the ANALYSIS
  copy only (KPIs/signals use the implied value; the receipt table shows
  the document as printed, flagged).

## Parked prototypes

`exp/` holds the cross-period job matcher (invariant-based, certify-by-
uniqueness). Promising but unfinished -- parked until the core product
ships. Do not wire it to anything yet.

Tests (no keys needed — extraction is faked):

```
python -m pytest tests/ -q     # 17 passing
```

## Wired but stubbed-thin / next

- Real-PDF shakedown of `EXTRACTION_PROMPT` (multi-page stitch is contract
  rule 4, untested against a live model)
- Subtotal *sections* (completed vs in-progress contracts) — only labeled
  subtotal rows are stripped today; section-aware splitting is a natural
  parse-layer extension
- FastAPI/SSE wrapper for wipple.ai — stream LangGraph node events as
  progress states
