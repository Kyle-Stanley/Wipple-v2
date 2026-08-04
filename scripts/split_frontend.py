from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "static" / "index.html"
STYLES = ROOT / "static" / "styles" / "wipple.css"
APP_DIR = ROOT / "static" / "app"
ARCHITECTURE = ROOT / "FRONTEND_ARCHITECTURE.md"

STYLE_PATTERN = re.compile(r"<style>\s*(.*?)\s*</style>", re.DOTALL)
JOB_MATCHING_TAG = '<script src="/static/job_matching.js"></script>'
WIP_MATH_TAG = '<script src="/static/wip_math.js"></script>'

MODULES = [
    ("00-core.js", None),
    ("10-state-batch.js", "let REPORT=null;"),
    (
        "20-portfolio.js",
        "/* -----------------------------------------------------------------------\n"
        "   Cross-period job matching.",
    ),
    ("30-document.js", "/* v3 doc report -> flat list"),
    ("40-validation.js", "function computeValidationChecks("),
    ("50-analysis.js", "function renderDash("),
    ("60-printing.js", "const PRINT_COLUMN_ORDER="),
]


def split_exact(source: str) -> list[tuple[str, str]]:
    starts: list[int] = [0]
    for _, marker in MODULES[1:]:
        assert marker is not None
        position = source.find(marker)
        if position < 0:
            raise RuntimeError(f"Could not find JavaScript split marker: {marker!r}")
        starts.append(position)

    if starts != sorted(starts) or len(set(starts)) != len(starts):
        raise RuntimeError("JavaScript split markers are out of order or duplicated")

    chunks: list[tuple[str, str]] = []
    for index, (name, _) in enumerate(MODULES):
        end = starts[index + 1] if index + 1 < len(starts) else len(source)
        chunks.append((name, source[starts[index] : end]))

    if "".join(chunk for _, chunk in chunks) != source:
        raise RuntimeError("JavaScript split was not byte-preserving")
    return chunks


def main() -> None:
    html = INDEX.read_text(encoding="utf-8")

    # Idempotent: once the external assets are present, do not split again.
    if '/static/styles/wipple.css' in html and '/static/app/00-core.js' in html:
        print("Frontend already split; nothing to do.")
        return

    external_anchor = html.find(JOB_MATCHING_TAG)
    if external_anchor < 0:
        raise RuntimeError("Could not find the existing job_matching.js script tag")

    markup = html[:external_anchor]
    script_tail = html[external_anchor:]

    styles = STYLE_PATTERN.findall(markup)
    if len(styles) != 2:
        raise RuntimeError(f"Expected exactly two document style blocks; found {len(styles)}")

    combined_css = styles[0].rstrip() + "\n\n" + styles[1].strip() + "\n"
    STYLES.parent.mkdir(parents=True, exist_ok=True)
    STYLES.write_text(combined_css, encoding="utf-8")

    markup = STYLE_PATTERN.sub(
        '<link rel="stylesheet" href="/static/styles/wipple.css">', markup, count=1
    )
    markup = STYLE_PATTERN.sub("", markup, count=1)

    wip_math_position = script_tail.find(WIP_MATH_TAG)
    if wip_math_position < 0:
        raise RuntimeError("Could not find the existing wip_math.js script tag")

    inline_start = script_tail.find("<script>", wip_math_position)
    inline_end = script_tail.rfind("</script>")
    if inline_start < 0 or inline_end < inline_start:
        raise RuntimeError("Could not isolate the main inline application script")

    js_start = inline_start + len("<script>")
    app_js = script_tail[js_start:inline_end]
    if app_js.startswith("\n"):
        app_js = app_js[1:]
    if app_js.endswith("\n"):
        app_js = app_js[:-1]

    chunks = split_exact(app_js)
    APP_DIR.mkdir(parents=True, exist_ok=True)
    for name, content in chunks:
        (APP_DIR / name).write_text(content.rstrip() + "\n", encoding="utf-8")

    script_tags = "\n".join(
        f'<script src="/static/app/{name}"></script>' for name, _ in chunks
    )
    script_tail = (
        script_tail[:inline_start]
        + script_tags
        + script_tail[inline_end + len("</script>") :]
    )

    INDEX.write_text(markup + script_tail, encoding="utf-8")

    ARCHITECTURE.write_text(
        """# Wipple frontend architecture

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
""",
        encoding="utf-8",
    )

    print("Split static/index.html into stylesheet and ordered application scripts.")


if __name__ == "__main__":
    main()
