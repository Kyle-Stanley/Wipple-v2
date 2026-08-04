# Wipple frontend architecture

Wipple's frontend remains a dependency-free, server-served browser application.
The files are classic scripts for now: load order is explicit, while state and the
largest feature domains have named owners. Vite, ESM, and React are not present.

## Entry point

- `static/index.html` owns page structure and the complete script order.
- `static/styles/wipple.css` owns the application, responsive, and print-shell styles.
- Inline event attributes are prohibited; listeners are attached from JavaScript.

## Explicit helper order

The focused helpers load before the numbered application scripts:

1. `static/job_matching.js` exposes deterministic identity matching.
2. `static/wip_math.js` exposes deterministic WIP derivation and mapping checks.
3. `static/mapping_ui.js` enhances manual mapping and audit presentation.
4. `static/review_refinement.js` refines the manual-review cards.

The two UI helpers are explicit assets; math code no longer injects them. Their
MutationObservers remain deliberate progressive-enhancement behavior.

## Application scripts

- `00-core.js` — formatting, header terminology, upload/streaming, source files, and progress animation.
- `01-app-state.js` — document, section, batch-analysis, processing, and billing-view state.
- `05-static-events.js` — static page listeners and image fallbacks.
- `10-state-batch.js` — batch scanning, review, metadata, and navigation.
- `15-totals.js` — totals reconciliation and default correction selection.
- `20-portfolio.js` — matching, consolidation, and portfolio/time-series preparation.
- `30-document.js` — document adaptation, section navigation, and sparse-column mapping.
- `40-validation.js` — validation checks, correction review, and certificate rendering.
- `50-analysis.js` — underwriting dashboard, signals, source review, and schedule rendering.
- `55-job-modal.js` — independent job analysis, charts, history, and modal interactions.
- `60-printing.js` — print layouts, report generation, and CSV export.

## Enforced boundaries

Frontend CI verifies syntax, ordered assets, state ownership, section correction
persistence, absence of inline handlers, and absence of dynamic script injection.
It permits exactly the five documented `mapping_ui.js` owner patches and rejects
all other runtime owner replacements. Progress-animation state is owned by
`00-core.js`; the old ticker cannot return.

## Remaining pre-Vite integration debt

- `review_refinement.js` receives explicit notifications from
  `applyColumnMapping` and `renderCertificate`; its body observer remains
  deliberate progressive-enhancement behavior.
- `mapping_ui.js` still replaces five owner functions. It also reads
  `renderCertificate` at three manual-editor rerender sites and `tableLabels`
  once. Those nine dependencies must become explicit before ESM.
- The mapping and review MutationObservers are intentionally retained until their
  rendering responsibilities move into an explicit component lifecycle.
