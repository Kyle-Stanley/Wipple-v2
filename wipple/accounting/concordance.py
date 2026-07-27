"""
Header concordance after math has assigned columns. Certification assigns
variables from numbers alone; this layer then asks whether the printed header
AGREES. The separate schema-arbitration exception is intentionally narrow:
exact title/header vocabulary may resolve WIP vs CC only when the sparse
revenue/cost/profit triangle is algebraically non-identifying.

  agree     -> the mapping gains a second, independent provenance
               ("math-certified, header-concordant")
  unknown   -> optionally one LLM call ("does this header mean Cost to
               Date?"), and the observed name joins the corpus -- every
               processed document makes the next one cheaper (training
               exhaust as an asset)
  disagree  -> a FINDING, never a veto. A header that says "Billings" atop
               a column the math proves is Cost to Date is exactly the kind
               of document defect an underwriter wants surfaced.

The corpus ships seeded with the names CPAs actually print and grows a
learned overlay at runtime.
"""

from __future__ import annotations

from difflib import SequenceMatcher
import json
import os
import re

_SEED = {
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
LEARNED_PATH = os.environ.get("WIPPLE_NAME_CORPUS",
                              os.path.expanduser("~/.wipple_names.json"))


def _norm(h: str) -> str:
    h = re.sub(r"[^a-z0-9% ]+", " ", str(h).lower())
    return re.sub(r"\s+", " ", h).strip()


def _corpus() -> dict:
    corpus = {}
    for var, names in _SEED.items():
        for n in names:
            corpus.setdefault(_norm(n), set()).add(var)
    try:
        with open(LEARNED_PATH) as f:
            for name, variables in json.load(f).items():
                corpus.setdefault(name, set()).update(variables)
    except (OSError, ValueError):
        pass
    return corpus


def match_header(header: str, allowed_variables) -> dict | None:
    """Conservatively map one printed header to the synonym corpus.

    Exact normalized matches win.  A fuzzy match is accepted only for a
    reasonably descriptive header with one clear winner, so short or
    accounting-ambiguous labels remain unassigned.
    """
    name = _norm(header)
    allowed = set(allowed_variables)
    if not name or not allowed:
        return None

    corpus = _corpus()
    exact = corpus.get(name, set()) & allowed
    if len(exact) == 1:
        return {"variable": next(iter(exact)), "match": "exact",
                "synonym": name, "score": 1.0}
    if exact or len(name) < 6:
        return None

    best_by_variable: dict[str, tuple[float, str]] = {}
    for synonym, variables in corpus.items():
        score = SequenceMatcher(None, name, synonym).ratio()
        for variable in variables & allowed:
            if score > best_by_variable.get(variable, (0.0, ""))[0]:
                best_by_variable[variable] = (score, synonym)
    ranked = sorted(
        ((score, variable, synonym)
         for variable, (score, synonym) in best_by_variable.items()),
        reverse=True)
    if not ranked:
        return None
    best_score, variable, synonym = ranked[0]
    runner_up = ranked[1][0] if len(ranked) > 1 else 0.0
    if best_score < 0.90 or best_score - runner_up < 0.08:
        return None
    return {"variable": variable, "match": "close",
            "synonym": synonym, "score": round(best_score, 3)}


def _learn(name: str, var: str) -> None:
    try:
        try:
            with open(LEARNED_PATH) as f:
                data = json.load(f)
        except (OSError, ValueError):
            data = {}
        data.setdefault(name, [])
        if var not in data[name]:
            data[name].append(var)
        with open(LEARNED_PATH, "w") as f:
            json.dump(data, f)
    except OSError:
        pass


def concordance_node(state) -> dict:
    """Annotate every certified column of every table with the header
    verdict; unknown headers are learned. Pure corpus by default; the LLM
    consult is one optional call per UNKNOWN header, and its absence (no
    key, test runs) degrades to 'unknown', never to failure."""
    corpus = _corpus()
    annotations = []
    for ti, tbl in enumerate(state.get("tables") or []):
        v = tbl.get("validation") or {}
        headers = (tbl.get("headers") or [])
        col_map = tbl.get("numeric_col_map") or []
        for mcol_s, var in (v.get("mapping") or {}).items():
            mcol = int(mcol_s)
            doc_col = col_map[mcol] if mcol < len(col_map) else None
            header = headers[doc_col] if (doc_col is not None
                                          and doc_col < len(headers)) else ""
            name = _norm(header)
            if not name:
                verdict = "no_header"
            elif var in corpus.get(name, set()):
                verdict = "concordant"
            elif corpus.get(name):
                verdict = "discordant"
            else:
                verdict = "unknown"
                _learn(name, var)     # certified by math; corpus grows
            ann = {"table": ti, "column": doc_col, "header": header,
                   "variable": var, "verdict": verdict}
            if verdict == "discordant":
                ann["note"] = (f"header reads '{header}' but the numbers "
                               f"certify this column as {var}; the identities"
                               " outrank the label -- flagged for review")
            annotations.append(ann)
    findings = [a for a in annotations if a["verdict"] == "discordant"]
    return {"concordance": {"annotations": annotations,
                            "discordant": findings}}
