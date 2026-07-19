"""Reporting-period extraction with a binary contract.

The extractor returns one exact ISO date or no date.  It deliberately does
not emit a self-assessed confidence score: ambiguous or missing dates are a
user-editable failure state in the batch UI.
"""

from __future__ import annotations

from datetime import date
import re


_MONTHS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2,
    "mar": 3, "march": 3, "apr": 4, "april": 4, "may": 5,
    "jun": 6, "june": 6, "jul": 7, "july": 7, "aug": 8,
    "august": 8, "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10, "nov": 11, "november": 11,
    "dec": 12, "december": 12,
}
_MONTH_RE = "|".join(sorted(_MONTHS, key=len, reverse=True))
_WORD_DATE = re.compile(
    rf"\b({_MONTH_RE})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?[,]?\s+"
    r"((?:19|20)\d{2})\b", re.I)
_ISO_DATE = re.compile(r"\b((?:19|20)\d{2})[-/](\d{1,2})[-/](\d{1,2})\b")
_US_DATE = re.compile(r"\b(\d{1,2})/(\d{1,2})/((?:19|20)?\d{2})\b")
_FYE_YEAR = re.compile(
    r"\b(?:fye|fy\s*end(?:ed|ing)?|fiscal\s+year\s+end(?:ed|ing)?|"
    r"year\s+end(?:ed|ing)?)\s*(?:on\s*)?((?:19|20)\d{2})\b", re.I)


def _iso(year: int, month: int, day: int) -> str | None:
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return None


def extract_period_end(texts, source_name: str = "") -> dict:
    """Return exactly one reporting date, otherwise an actionable failure."""
    candidates: set[str] = set()
    corpus = [str(x) for x in (texts or []) if str(x).strip()]
    if source_name:
        corpus.append(str(source_name))

    for text in corpus:
        for month, day, year in _WORD_DATE.findall(text):
            value = _iso(int(year), _MONTHS[month.lower().rstrip(".")],
                         int(day))
            if value:
                candidates.add(value)
        for year, month, day in _ISO_DATE.findall(text):
            value = _iso(int(year), int(month), int(day))
            if value:
                candidates.add(value)
        for month, day, year in _US_DATE.findall(text):
            y = int(year)
            if y < 100:
                y += 2000
            value = _iso(y, int(month), int(day))
            if value:
                candidates.add(value)
        for year in _FYE_YEAR.findall(text):
            candidates.add(f"{int(year):04d}-12-31")

    if len(candidates) == 1:
        return {"reporting_date": next(iter(candidates)),
                "reporting_date_error": None}
    if len(candidates) > 1:
        return {"reporting_date": None,
                "reporting_date_error": "multiple_reporting_dates"}
    return {"reporting_date": None,
            "reporting_date_error": "reporting_date_not_found"}
