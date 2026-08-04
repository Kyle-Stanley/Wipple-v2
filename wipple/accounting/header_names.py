"""Conservative static header names for sparse mapping fallback.

This is deliberately small and deterministic. Exact normalized matches win;
a clear fuzzy typo may match, and everything ambiguous stays unassigned.
There is no runtime learning, model call, or persistent corpus mutation.
"""

from __future__ import annotations

from difflib import SequenceMatcher
import re

_NAMES = {
    "V": ["contract price", "contract value", "contract amount",
          "total contract", "revised contract"],
    "C": ["est total cost", "estimated cost", "estimated total cost",
          "total estimated cost", "est cost", "revised est cost"],
    "G": ["est gross profit", "estimated gross profit", "gross profit",
          "estimated profit"],
    "D": ["costs to date", "cost to date", "cost incurred",
          "costs incurred to date", "jtd cost"],
    "Q": ["cost to complete", "costs to complete", "estimated cost to "
          "complete", "remaining cost"],
    "P": ["% complete", "percent complete", "pct complete", "% comp"],
    "E": ["revenues earned", "revenue earned", "earned revenue",
          "revenue recognized", "earned to date"],
    "B": ["billed to date", "billings to date", "total billed",
          "progress billings"],
    "U": ["under billings", "underbillings", "costs in excess of billings",
          "cie", "unbilled"],
    "O": ["over billings", "overbillings", "billings in excess of costs",
          "bie"],
    "H": ["gross profit to date", "earned gross profit", "profit to date"],
    "M": ["margin", "gross margin", "profit %", "margin %"],
    "RT": ["total revenues earned", "total revenue", "contract price"],
    "KT": ["total costs", "total cost", "cost of revenues"],
    "GT": ["total gross profit", "gross profit"],
    "RP": ["revenues earned prior years", "prior years revenue",
           "revenue prior"],
    "RC": ["revenues earned current year", "current year revenue",
           "revenue current"],
    "KP": ["costs prior years", "prior years cost"],
    "KC": ["costs current year", "current year cost"],
    "GP": ["gross profit prior years", "prior years gross profit"],
    "GC": ["gross profit current year", "current year gross profit"],
    "BC": ["billed to date", "contract billings"],
    "RR": ["retainage", "retainage receivable", "retention"],
}


def _norm(value: str) -> str:
    value = re.sub(r"[^a-z0-9% ]+", " ", str(value).lower())
    return re.sub(r"\s+", " ", value).strip()


_CORPUS = {
    name: frozenset(
        variable
        for variable, names in _NAMES.items()
        if name in {_norm(item) for item in names}
    )
    for name in {_norm(item) for names in _NAMES.values() for item in names}
}


def match_header(header: str, allowed_variables) -> dict | None:
    """Return one conservative synonym match, or ``None``."""
    name = _norm(header)
    allowed = set(allowed_variables)
    if not name or not allowed:
        return None

    exact = _CORPUS.get(name, frozenset()) & allowed
    if len(exact) == 1:
        return {"variable": next(iter(exact)), "match": "exact",
                "synonym": name, "score": 1.0}
    if exact or len(name) < 6:
        return None

    best_by_variable: dict[str, tuple[float, str]] = {}
    for synonym, variables in _CORPUS.items():
        score = SequenceMatcher(None, name, synonym).ratio()
        for variable in variables & allowed:
            if score > best_by_variable.get(variable, (0.0, ""))[0]:
                best_by_variable[variable] = (score, synonym)
    ranked = sorted(
        ((score, variable, synonym)
         for variable, (score, synonym) in best_by_variable.items()),
        reverse=True,
    )
    if not ranked:
        return None
    best_score, variable, synonym = ranked[0]
    runner_up = ranked[1][0] if len(ranked) > 1 else 0.0
    if best_score < 0.90 or best_score - runner_up < 0.08:
        return None
    return {"variable": variable, "match": "close",
            "synonym": synonym, "score": round(best_score, 3)}
