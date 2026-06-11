"""
Deterministic parsing: raw extracted cell strings -> validator-ready matrix.

No LLM anywhere in this module. Every transformation is recorded in the
parse report so the emit node can surface exactly what was done to the data
before validation.

Decisions encoded here (and why):

- Dash variants ("-", "--", em/en dash) mean ZERO. On a WIP a dash is an
  explicit zero (no billings yet on a new job), not missing data.
- Blank cells default to ZERO as well (configurable via blank_as_zero).
  Rationale: blank-as-NaN drops the entire row inside the validator
  (non-finite rows are excluded), which can push a dense document under
  min_rows and silently degrade SUCCESS into INSUFFICIENT. Blank-as-zero,
  when wrong, instead produces a strict-certification failure pointing at
  exactly that cell -- visible and diagnosable beats silent. Every
  blank->0 is flagged so the finding can be cross-referenced.
- Truly unparseable garbage -> NaN (row quarantined by the validator).
- OCR-confusable repair (O->0, l->1, S->5, ...) runs ONLY after a strict
  parse fails -- repair-after-plausibility, never blanket translation.
- Decimal convention (US 1,234.56 vs EU 1.234,56) is decided once per
  document from aggregate evidence, never per cell. Mixed conventions in
  one document are an extraction error, not something to paper over.
- Percent columns are normalized to 0..1 fractions (the validator's percent
  rules are ratio-scale: P = D/C). Scale detection may use a '%' glyph in
  cells, or in the header -- a formatting signal, not a semantic one;
  headers still never influence which VARIABLE a column is.
- Rows labeled total/subtotal are stripped from the matrix. The final
  stated totals row is retained separately and checked against computed
  column sums -- a free deterministic pre-check, and totals left in the
  matrix would partially satisfy additive identities while corrupting
  ratio ones (the worst case: a poisoned hypothesis space, not a clean
  failure).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

DASH_TOKENS = {"-", "--", "\u2013", "\u2014", "\u2212", "\u2012", "\u2010"}
CURRENCY = "$\u20ac\u00a3\u00a5"
CONFUSABLES = str.maketrans({
    "O": "0", "o": "0", "l": "1", "I": "1", "|": "1",
    "S": "5", "s": "5", "B": "8", "Z": "2", "z": "2",
})

_US_PAT = re.compile(r"^\d{1,3}(,\d{3})+(\.\d+)?$")
_EU_PAT = re.compile(r"^\d{1,3}(\.\d{3})+(,\d+)?$")
_PLAIN_EU_DEC = re.compile(r"^\d+,\d{1,2}$")   # 1234,56 -- comma as decimal
_TOTAL_LABEL = re.compile(r"\b(sub)?\s*totals?\b|\bgrand\s+total\b", re.I)
_PCT_HEADER = re.compile(r"%|percent|pct", re.I)


@dataclass
class CellFlag:
    row: int            # raw_table row index (0-based, body rows)
    col: int            # raw_table column index
    raw: str
    flag: str           # dash_as_zero | blank_as_zero | confusable_repair |
                        # unparseable | paren_negative
    value: Optional[float] = None


@dataclass
class ParseResult:
    matrix: Optional[np.ndarray]        # rows x numeric-cols, float
    job_labels: list[str]
    numeric_col_map: list[int]          # matrix col j -> original column index
    headers: list[str]                  # passed through, still quarantined
    cell_flags: list[CellFlag] = field(default_factory=list)
    dropped_columns: list[dict] = field(default_factory=list)
    stripped_total_rows: list[dict] = field(default_factory=list)
    totals_check: Optional[dict] = None  # stated vs computed per column
    decimal_convention: str = "us"
    percent_scaled_cols: list[int] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def report(self) -> dict:
        return {
            "decimal_convention": self.decimal_convention,
            "n_rows": 0 if self.matrix is None else int(self.matrix.shape[0]),
            "n_numeric_cols": len(self.numeric_col_map),
            "numeric_col_map": list(self.numeric_col_map),
            "percent_scaled_cols": list(self.percent_scaled_cols),
            "dropped_columns": self.dropped_columns,
            "stripped_total_rows": self.stripped_total_rows,
            "totals_check": self.totals_check,
            "cell_flags": [
                {"row": f.row, "col": f.col, "raw": f.raw,
                 "flag": f.flag, "value": f.value}
                for f in self.cell_flags
            ],
            "notes": self.notes,
        }


# ---------------------------------------------------------------------------
# Cell-level parsing
# ---------------------------------------------------------------------------

def _clean(raw: str) -> str:
    s = unicodedata.normalize("NFKC", str(raw)).strip()
    s = s.replace("\u00a0", " ").strip()
    return s


def detect_decimal_convention(rows: list[list[str]]) -> str:
    """One decision per document, from aggregate cell evidence."""
    us = eu = 0
    for r in rows:
        for c in r:
            s = _clean(c).strip(CURRENCY + " ()%")
            if _US_PAT.match(s):
                us += 1
            elif _EU_PAT.match(s) or _PLAIN_EU_DEC.match(s):
                eu += 1
    return "eu" if eu > us else "us"


def parse_cell(raw: str, convention: str = "us",
               repair: bool = True) -> tuple[float, list[str]]:
    """Return (value, flags). value is NaN when genuinely unreadable.

    repair=False disables the confusable pass entirely -- used by
    parse_table's first (classification) pass so that repair eligibility is
    decided by column context, never cell by cell in isolation.
    """
    flags: list[str] = []
    s = _clean(raw)

    if s == "":
        return 0.0, ["blank_as_zero"]
    if s in DASH_TOKENS:
        return 0.0, ["dash_as_zero"]

    if s.startswith("(") and s.endswith(")"):
        s = s[1:-1].strip()
        negative = True
        flags.append("paren_negative")
    else:
        negative = False

    is_pct = "%" in s
    if is_pct:
        flags.append("pct_glyph")

    s = s.strip().lstrip(CURRENCY).rstrip("%").strip()
    s = s.replace(" ", "")
    if s.startswith("-"):
        negative = not negative
        s = s[1:]

    def _strict(t: str) -> Optional[float]:
        if convention == "eu":
            if _EU_PAT.match(t) or _PLAIN_EU_DEC.match(t):
                t = t.replace(".", "").replace(",", ".")
            elif re.match(r"^\d+(\.\d+)?$", t):
                pass  # plain number, period as decimal is unambiguous
            else:
                return None
        else:
            if _US_PAT.match(t):
                t = t.replace(",", "")
            elif re.match(r"^\d+(\.\d+)?$", t):
                pass
            elif re.match(r"^\d+,\d{3}(\.\d+)?$", t):
                t = t.replace(",", "")
            else:
                return None
        try:
            return float(t)
        except ValueError:
            return None

    v = _strict(s)
    if v is None and repair:
        # Repair pass runs ONLY after strict parsing fails (plausibility first).
        repaired = s.translate(CONFUSABLES)
        if repaired != s:
            v = _strict(repaired)
            if v is not None:
                flags.append("confusable_repair")
    if v is None:
        return float("nan"), flags + ["unparseable"]

    return (-v if negative else v), flags


# ---------------------------------------------------------------------------
# Table-level assembly
# ---------------------------------------------------------------------------

NUMERIC_COL_MIN_FRAC = 0.60


def parse_table(
    rows: list[list[str]],
    headers: Optional[list[str]] = None,
    blank_as_zero: bool = True,
) -> ParseResult:
    headers = [str(h) for h in (headers or [])]
    if not rows:
        return ParseResult(matrix=None, job_labels=[], numeric_col_map=[],
                           headers=headers, notes=["empty table"])

    width = max(len(r) for r in rows)
    rows = [list(r) + [""] * (width - len(r)) for r in rows]
    convention = detect_decimal_convention(rows)

    # PASS 1 -- strict parse only, no repair. Classification must run on
    # evidence the parser did not manufacture.
    parsed = np.full((len(rows), width), np.nan)
    all_flags: list[CellFlag] = []
    pct_glyph_cols: set[int] = set()
    strict_failed: list[tuple[int, int]] = []
    for i, r in enumerate(rows):
        for j, c in enumerate(r):
            v, fl = parse_cell(c, convention, repair=False)
            if "blank_as_zero" in fl and not blank_as_zero:
                v = float("nan")
            parsed[i, j] = v
            if "pct_glyph" in fl:
                pct_glyph_cols.add(j)
            if "unparseable" in fl:
                strict_failed.append((i, j))
            for f in fl:
                if f in ("dash_as_zero", "blank_as_zero", "paren_negative"):
                    all_flags.append(CellFlag(i, j, _clean(c), f, v))

    # Column classification: numeric vs label/junk.
    numeric_cols: list[int] = []
    dropped: list[dict] = []
    for j in range(width):
        col_cells = [_clean(rows[i][j]) for i in range(len(rows))]
        nonblank = [i for i, c in enumerate(col_cells) if c != ""]
        if not nonblank:
            dropped.append({"col": j, "reason": "all blank",
                            "header": headers[j] if j < len(headers) else ""})
            continue
        finite = sum(1 for i in nonblank if np.isfinite(parsed[i, j]))
        if finite / len(nonblank) >= NUMERIC_COL_MIN_FRAC:
            numeric_cols.append(j)
        else:
            dropped.append({"col": j, "reason": "non-numeric",
                            "header": headers[j] if j < len(headers) else ""})

    # PASS 2 -- confusable repair, gated by column context: only cells whose
    # column ALREADY qualified as numeric on strict evidence may be repaired.
    # A job-ID column of "S101"s never qualifies, so "S101" is never turned
    # into 5101; a money column with 7/8 clean cells earns the right to
    # repair its "4O,000".
    numeric_set = set(numeric_cols)
    for (i, j) in strict_failed:
        if j not in numeric_set:
            all_flags.append(CellFlag(i, j, _clean(rows[i][j]),
                                      "unparseable", None))
            continue
        v, fl = parse_cell(rows[i][j], convention, repair=True)
        parsed[i, j] = v
        if "confusable_repair" in fl:
            all_flags.append(CellFlag(i, j, _clean(rows[i][j]),
                                      "confusable_repair", v))
        else:
            all_flags.append(CellFlag(i, j, _clean(rows[i][j]),
                                      "unparseable", None))

    # Job labels: leftmost dropped (non-numeric) column with mostly-distinct
    # values; otherwise synthesized.
    label_col = None
    for d in dropped:
        if d["reason"] != "non-numeric":
            continue
        j = d["col"]
        vals = [_clean(rows[i][j]) for i in range(len(rows))]
        nonblank = [v for v in vals if v]
        if nonblank and len(set(nonblank)) / len(nonblank) >= 0.5:
            label_col = j
            d["reason"] = "job_labels"
            break
    if label_col is not None:
        job_labels = [_clean(rows[i][label_col]) or f"Row {i + 1}"
                      for i in range(len(rows))]
    else:
        job_labels = [f"Row {i + 1}" for i in range(len(rows))]

    # Totals rows: label-matched anywhere; the final body row is additionally
    # tested numerically (stated total ~= sum of remaining rows).
    body = list(range(len(rows)))
    stripped: list[dict] = []

    def _strip(i: int, why: str) -> None:
        body.remove(i)
        stripped.append({"row": i, "label": job_labels[i], "reason": why,
                         "values": {j: (None
                                        if (not np.isfinite(parsed[i, j])
                                            or _clean(rows[i][j]) == "")
                                        else float(parsed[i, j]))
                                    for j in numeric_cols}})

    for i in range(len(rows)):
        if _TOTAL_LABEL.search(job_labels[i]):
            _strip(i, "label matched total/subtotal")

    if body and len(body) >= 3:
        last = body[-1]
        rest = body[:-1]
        money_like = [j for j in numeric_cols if j not in pct_glyph_cols]
        if money_like:
            sums = np.nansum(parsed[np.ix_(rest, money_like)], axis=0)
            stated = parsed[last, money_like]
            with np.errstate(invalid="ignore"):
                close = np.isclose(stated, sums, rtol=0.005, atol=1.0)
            informative = np.abs(sums) > 1.0
            if informative.sum() >= 3 and close[informative].all():
                _strip(last, "numerically matches column sums")

    # Totals check: stated (last stripped totals row) vs computed body sums.
    totals_check = None
    if stripped and body:
        grand = stripped[-1]
        per_col = {}
        for j in numeric_cols:
            if j in pct_glyph_cols:
                continue          # a sum of display percents is meaningless
            stated_v = grand["values"].get(j)
            if stated_v is None:
                continue
            computed = float(np.nansum(parsed[body, j]))
            per_col[j] = {
                "stated": stated_v,
                "computed": round(computed, 2),
                "difference": round(stated_v - computed, 2),
                "matches": bool(abs(stated_v - computed)
                                <= max(1.0, 0.005 * abs(computed))),
            }
        if per_col:
            totals_check = {
                "source_row": grand["row"],
                "columns": per_col,
                "all_match": all(c["matches"] for c in per_col.values()),
            }

    # Build matrix over body rows and numeric columns.
    if not body or not numeric_cols:
        return ParseResult(matrix=None, job_labels=[],
                           numeric_col_map=[], headers=headers,
                           cell_flags=all_flags, dropped_columns=dropped,
                           stripped_total_rows=stripped,
                           totals_check=totals_check,
                           decimal_convention=convention,
                           notes=["no usable body rows or numeric columns"])

    matrix = parsed[np.ix_(body, numeric_cols)].astype(float)
    labels = [job_labels[i] for i in body]

    # Percent scaling to 0..1 fractions. Signal: '%' glyph in cells, or a
    # percent-ish header (formatting use only) -- and magnitudes that look
    # like display percents (median > 1.5, bounded ~[-5, 130]).
    scaled: list[int] = []
    for mcol, j in enumerate(numeric_cols):
        col = matrix[:, mcol]
        fin = col[np.isfinite(col)]
        if fin.size == 0:
            continue
        header_pct = j < len(headers) and bool(_PCT_HEADER.search(headers[j]))
        glyph = j in pct_glyph_cols
        if not (glyph or header_pct):
            continue
        med = float(np.median(np.abs(fin[fin != 0]))) if (fin != 0).any() else 0.0
        if med > 1.5 and fin.min() > -5 and fin.max() < 130:
            matrix[:, mcol] = col / 100.0
            scaled.append(mcol)

    notes = []
    if scaled:
        notes.append(f"{len(scaled)} column(s) scaled from display percents "
                     "to fractions")

    return ParseResult(
        matrix=matrix,
        job_labels=labels,
        numeric_col_map=numeric_cols,
        headers=headers,
        cell_flags=all_flags,
        dropped_columns=dropped,
        stripped_total_rows=stripped,
        totals_check=totals_check,
        decimal_convention=convention,
        percent_scaled_cols=scaled,
        notes=notes,
    )
