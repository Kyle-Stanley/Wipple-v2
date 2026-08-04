# Wipple frontend architecture

The frontend remains a dependency-free, server-served browser application. This cleanup changes file boundaries only; it does not change the UI, API contract, state model, validation rules, or deployment flow.

## Entry point

- `static/index.html` — page structure and ordered asset loading only.
- `static/styles/wipple.css` — all application styling, including responsive and print-shell styles.

## Application scripts

Scripts are classic browser scripts loaded in numeric order so the existing shared global scope and inline handlers continue to behave exactly as before.

- `static/app/00-core.js` — shared formatting, header terminology, upload controls, streaming, source-file handling, and totals logic.
- `static/app/10-state-batch.js` — application state, batch scanning, batch review, metadata, and batch navigation.
- `static/app/20-portfolio.js` — cross-period job matching, consolidated schedules, and portfolio/time-series analysis preparation.
- `static/app/30-document.js` — document adaptation, section navigation, sparse-column mapping, and top-level rendering.
- `static/app/40-validation.js` — validation checks, correction review, and validation certificate rendering.
- `static/app/50-analysis.js` — underwriting dashboard, signals, charts, job analysis, source review, and schedule table rendering.
- `static/app/60-printing.js` — print layouts, report generation, and CSV export.

## Existing focused helpers

- `static/job_matching.js` — identity scoring and candidate plausibility.
- `static/wip_math.js` — deterministic WIP derivation and mapping-readiness helpers.

## Deliberate next steps

The safe next refactor is to replace shared mutable globals with an explicit state object and convert direct DOM-rendering functions one workflow at a time. React or Vite can be evaluated after these boundaries are stable; neither is required for this structural cleanup.
