from __future__ import annotations

import base64
import gzip
import json
from pathlib import Path
import re
import subprocess


def _replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    assert count == 1, f"{label}: expected one match, found {count}"
    return text.replace(old, new)


def _remove_regex(text: str, pattern: str, label: str) -> str:
    updated, count = re.subn(pattern, "", text, count=1, flags=re.MULTILINE | re.DOTALL)
    assert count == 1, f"{label}: expected one match, found {count}"
    return updated


def _tracked_files() -> list[str]:
    return subprocess.check_output(["git", "ls-files"], text=True).splitlines()


HEADER_NAMES = '''"""Conservative static header names for sparse mapping fallback.

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
    return re.sub(r"\\s+", " ", value).strip()


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
'''


def test_build_repository_cleanup_payload() -> None:
    tracked = _tracked_files()
    modified: dict[str, str] = {
        "wipple/accounting/header_names.py": HEADER_NAMES,
    }

    server_path = Path("server.py")
    server = server_path.read_text(encoding="utf-8")
    server = _replace_once(
        server,
        "misalignment, concordance -- each narrate what they proved, not what they\n"
        "did. Everything the old endpoints accepted still works: spreadsheets and",
        "misalignment -- each narrates what it proved, not what it did. Everything\n"
        "the old endpoints accepted still works: spreadsheets and",
        "server overview",
    )
    server = _replace_once(
        server,
        "from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse",
        "from fastapi.responses import HTMLResponse, StreamingResponse",
        "FileResponse import",
    )
    server = _remove_regex(
        server,
        r'    if node == "concordance":\n.*?(?=    if node == "emit":)',
        "concordance narration",
    )
    server = _remove_regex(
        server,
        r'\n@app\.get\("/how"\)\ndef how\(\):\n    return FileResponse\("static/how\.html"\)\n',
        "how route",
    )
    modified[str(server_path)] = server

    document_path = Path("wipple/pipeline/document.py")
    document = document_path.read_text(encoding="utf-8")
    document = _replace_once(
        document,
        "    ingest -> chunk -> extract_chunks -> assemble -> tables -+-> concordance -> emit",
        "    ingest -> chunk -> extract_chunks -> assemble -> tables -+-> emit",
        "document graph overview",
    )
    document = _replace_once(
        document,
        "from ..accounting.concordance import concordance_node\n",
        "",
        "concordance import",
    )
    document = _replace_once(document, "    concordance: dict\n", "", "document state field")
    document = _replace_once(
        document,
        '        entry["validation"] = v          # concordance reads the mapping\n',
        "",
        "table validation handoff",
    )
    document = _replace_once(document, '    return "concordance"\n', '    return "emit"\n', "post-table route")
    document = _replace_once(
        document,
        '            "concordance": state.get("concordance", {}),\n',
        "",
        "report concordance field",
    )
    document = _replace_once(document, '    g.add_node("concordance", concordance_node)\n', "", "concordance graph node")
    document = _replace_once(
        document,
        '                            {"re_extract": "re_extract",\n'
        '                             "concordance": "concordance"})',
        '                            {"re_extract": "re_extract",\n'
        '                             "emit": "emit"})',
        "post-table edge map",
    )
    document = _replace_once(document, '    g.add_edge("concordance", "emit")\n', "", "concordance emit edge")
    modified[str(document_path)] = document

    html_path = Path("static/index.html")
    html = html_path.read_text(encoding="utf-8")
    html = _replace_once(
        html,
        '  <div class="tag" id="tagline">verified WIP extraction · <a href="/how" style="color:var(--sage-deep)">how it works</a></div>',
        '  <div class="tag" id="tagline">verified WIP extraction</div>',
        "header how link",
    )
    html = _replace_once(
        html,
        '  <p class="quiet">The sample generates a fresh 60-job schedule with five planted transcription errors, different every run. <a href="/how" style="color:var(--sage-deep)">How the math works</a></p>',
        '  <p class="quiet">The sample generates a fresh 60-job schedule with five planted transcription errors, different every run.</p>',
        "landing how link",
    )
    modified[str(html_path)] = html

    frontend_path = Path("static/app/30-document.js")
    frontend = frontend_path.read_text(encoding="utf-8")
    frontend = _replace_once(
        frontend,
        '  const disc=(((doc.document||{}).concordance)||{}).discordant||[];\n',
        "",
        "frontend concordance fallback",
    )
    frontend = _replace_once(
        frontend,
        '      rep._headerComparison=buildHeaderComparison(t,rep,disc);',
        '      rep._headerComparison=buildHeaderComparison(t,rep);',
        "header comparison call",
    )
    modified[str(frontend_path)] = frontend

    fallback_path = Path("wipple/pipeline/fallback.py")
    fallback = fallback_path.read_text(encoding="utf-8")
    fallback = _replace_once(
        fallback,
        "from ..accounting.concordance import match_header",
        "from ..accounting.header_names import match_header",
        "fallback registry import",
    )
    modified[str(fallback_path)] = fallback

    schema_test_path = Path("tests/test_schema_semantics.py")
    schema_tests = schema_test_path.read_text(encoding="utf-8")
    schema_tests = _replace_once(
        schema_tests,
        "from wipple.accounting.concordance import match_header",
        "from wipple.accounting.header_names import match_header",
        "schema-test registry import",
    )
    modified[str(schema_test_path)] = schema_tests

    runner_path = Path("run_wipple.py")
    runner = runner_path.read_text(encoding="utf-8")
    runner = _remove_regex(
        runner,
        r'    doc = report\.get\("document"\) or \{\}\n'
        r'    disc = \(doc\.get\("concordance"\) or \{\}\)\.get\("discordant"\) or \[\]\n'
        r'    for d in disc:\n'
        r'        print\(f"--   header .*?file=err\)\n',
        "CLI concordance summary",
    )
    modified[str(runner_path)] = runner

    cc_path = Path("wipple/accounting/cc.py")
    cc = cc_path.read_text(encoding="utf-8")
    cc = _replace_once(
        cc,
        "    # More than one duplicate-total leftover is not semantically identifiable\n"
        "    # from values alone. Header concordance may describe it later, but the CC\n"
        "    # validator will not guess which duplicate is billings.",
        "    # More than one duplicate-total leftover is not semantically identifiable\n"
        "    # from values alone. The validator will not guess which duplicate is\n"
        "    # billings.",
        "CC stale comment",
    )
    modified[str(cc_path)] = cc

    schemas_path = Path("wipple/accounting/schemas.py")
    schemas = schemas_path.read_text(encoding="utf-8")
    schemas = _replace_once(
        schemas,
        "    # of revenue. It stays in the vocabulary for display/header concordance,\n"
        "    # but the CC math engine intentionally does not map it.",
        "    # of revenue. It stays in the vocabulary for display/header matching,\n"
        "    # but the CC math engine intentionally does not map it.",
        "schema retainage comment",
    )
    schemas = _replace_once(
        schemas,
        "# Merged lookup for emit/concordance layers.",
        "# Merged lookup for emit and sparse-header fallback layers.",
        "schema lookup comment",
    )
    modified[str(schemas_path)] = schemas

    gates_path = Path("wipple/support/gates.py")
    gates = gates_path.read_text(encoding="utf-8")
    gates = _remove_regex(
        gates,
        r'\ndef g7_concordance\(\):\n.*?(?=\ndef main\(\):)',
        "obsolete concordance gate",
    )
    gates = _replace_once(gates, "    g7_concordance()\n", "", "concordance gate call")
    modified[str(gates_path)] = gates

    wip2_paths = sorted(path for path in tracked if "wip2" in path.lower())
    exp_paths = sorted(path for path in tracked if path == "exp" or path.startswith("exp/"))
    deletions = sorted(set([
        "wipple/accounting/concordance.py",
        "static/how.html",
        *wip2_paths,
        *exp_paths,
    ]))

    reference_terms = {
        "concordance": re.compile(r"concordance", re.IGNORECASE),
        "wip2": re.compile(r"wip2", re.IGNORECASE),
        "how_route": re.compile(r'(?:href=[\"\']/how|static/how\.html|@app\.get\([\"\']/how)', re.IGNORECASE),
    }
    remaining: dict[str, list[str]] = {key: [] for key in reference_terms}
    deletion_set = set(deletions)
    ignored = {"tests/test_cleanup_payload.py"}
    for path in tracked:
        if path in deletion_set or path in ignored or path.startswith(".github/"):
            continue
        candidate = modified.get(path)
        if candidate is None:
            try:
                candidate = Path(path).read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
        for key, pattern in reference_terms.items():
            if pattern.search(candidate):
                remaining[key].append(path)

    payload = {
        "modified": modified,
        "deletions": deletions,
        "wip2_paths": wip2_paths,
        "exp_paths": exp_paths,
        "remaining_references": remaining,
    }
    encoded = base64.b64encode(
        gzip.compress(json.dumps(payload, sort_keys=True).encode("utf-8"))
    ).decode("ascii")
    marker = "stitch" + "ing_cleanup_payload="
    Path("cleanup_payload.txt").write_text(marker + encoded + "\n", encoding="utf-8")

    assert wip2_paths == [
        "tests/test_wip2_constraint_engine.py",
        "wipple/accounting/wip2.py",
    ]
    assert exp_paths == [
        "exp/diag.py", "exp/evolve_test.py", "exp/job_match.py", "exp/mc_test.py",
    ]
    assert not remaining["concordance"], remaining["concordance"]
    assert not remaining["wip2"], remaining["wip2"]
    assert not remaining["how_route"], remaining["how_route"]
