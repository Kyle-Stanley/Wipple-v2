"""
Header concordance: the one place header TEXT is allowed to speak, and only
AFTER the math has ruled. Certification assigns variables from numbers
alone; this layer then asks whether the printed header AGREES.

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
