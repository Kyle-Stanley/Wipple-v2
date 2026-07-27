from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one match, found {count}")
    return text.replace(old, new, 1)


validation_path = ROOT / "wipple/accounting/validation.py"
text = validation_path.read_text(encoding="utf-8")
text = replace_once(
    text,
    "from .semantics import resolve_schema_text\nfrom .wip import VAR_NAMES, ValidationResult, validate_wip",
    "from .semantics import resolve_schema_text\nfrom .schemas import degenerate_wip_rows\nfrom .wip import VAR_NAMES, ValidationResult, validate_wip",
    "validation imports",
)

old = '''def run_schema_race(matrix, labels, headers=None, title_texts=None):
    """Run both engines, consulting printed text only for algebraic ties.

    The sparse CC triangle ``cost + profit = revenue`` is identical to the
    WIP triangle ``estimated cost + profit = contract value``. In that one
    underdetermined case, an exact attached title or schema-specific header
    may select between the already available interpretations. Text never
    creates a column mapping.
    """
    wip = validate_wip(matrix, job_labels=labels)
    cc = validate_cc(matrix, job_labels=labels)
    m = matrix.shape[1]
    kw = (_race_rank(wip, m), _race_score(wip, m))
    kc = (_race_rank(cc, m), _race_score(cc, m))
    chosen, name = (wip, "wip") if kw >= kc else (cc, "cc")

    cc_vars = set(cc.mapping.values())
    cc_triangle_only = (
        bool(cc.witnesses)
        and {"RT", "KT", "GT"} <= cc_vars
        and cc_vars <= {"RT", "KT", "GT"}
    )
    text_evidence = None
    resolution = "math"
    if cc_triangle_only:
        text_evidence = resolve_schema_text(
            headers=headers, title_texts=title_texts)
        if text_evidence["chosen"] == "wip":
            chosen, name = wip, "wip"
            resolution = f"text_{text_evidence['source']}"
        elif text_evidence["chosen"] == "cc":
            chosen, name = cc, "cc"
            resolution = f"text_{text_evidence['source']}"
        else:
            # Preserve the existing numeric result, but disclose that the
            # schema itself was not identifiable from the sparse equation.
            resolution = "unresolved_sparse_triangle"

    return chosen, {
        "chosen": name,
        "resolution": resolution,
        "text_evidence": text_evidence,
        "wip": {"status": wip.status, "rank": kw[0],
                "score": round(kw[1], 3),
                "explained": len(wip.mapping)},
        "cc": {"status": cc.status, "rank": kc[0],
               "score": round(kc[1], 3),
               "explained": len(cc.mapping)},
    }
'''

new = '''def _all_rows_are_completed_wip(matrix, result: ValidationResult) -> bool:
    """Whether every row has the exact completed-job signature in WIP columns.

    A certified nondegenerate WIP cannot simultaneously be a completed-contract
    schedule. The one legitimate overlap is a section printed in WIP layout
    whose jobs are all complete (E=V, D=C, Q=0, P=1, U=O=0). Keep the CC check
    for that case so the document splitter and section classifier retain their
    existing escape hatch.
    """
    a = np.asarray(matrix, dtype=float)
    if a.ndim != 2 or not result.mapping:
        return False
    core = {
        variable: a[:, int(column)]
        for column, variable in result.mapping.items()
        if variable in {"V", "C", "D", "Q", "P", "E", "U", "O"}
        and 0 <= int(column) < a.shape[1]
    }
    hits = degenerate_wip_rows(core)
    return bool(hits) and all(
        available >= 3 and matched == available
        for matched, available in hits
    )


def run_schema_race(matrix, labels, headers=None, title_texts=None):
    """Try WIP first; run CC only when WIP does not establish the schema.

    A witnessed WIP mapping explaining at least half of the numeric columns is
    already a schema verdict, whether its row values pass or produce findings.
    CC remains the fallback for insufficient/partial WIP interpretations and
    for the exact all-complete WIP-layout overlap. Printed text is consulted
    only for the sparse additive triangle after both interpretations exist.
    """
    wip = validate_wip(matrix, job_labels=labels)
    m = matrix.shape[1]
    kw = (_race_rank(wip, m), _race_score(wip, m))
    all_complete = _all_rows_are_completed_wip(matrix, wip)

    if kw[0] == 2 and not all_complete:
        return wip, {
            "chosen": "wip",
            "resolution": "wip_certified",
            "text_evidence": None,
            "wip": {"status": wip.status, "rank": kw[0],
                    "score": round(kw[1], 3),
                    "explained": len(wip.mapping)},
            "cc": {"status": "skipped", "rank": None, "score": None,
                   "explained": 0,
                   "reason": "certified nondegenerate WIP mapping"},
        }

    cc = validate_cc(matrix, job_labels=labels)
    kc = (_race_rank(cc, m), _race_score(cc, m))
    chosen, name = (wip, "wip") if kw >= kc else (cc, "cc")

    cc_vars = set(cc.mapping.values())
    cc_triangle_only = (
        bool(cc.witnesses)
        and {"RT", "KT", "GT"} <= cc_vars
        and cc_vars <= {"RT", "KT", "GT"}
    )
    text_evidence = None
    resolution = "math_all_complete_overlap" if all_complete else "math"
    if cc_triangle_only:
        text_evidence = resolve_schema_text(
            headers=headers, title_texts=title_texts)
        if text_evidence["chosen"] == "wip":
            chosen, name = wip, "wip"
            resolution = f"text_{text_evidence['source']}"
        elif text_evidence["chosen"] == "cc":
            chosen, name = cc, "cc"
            resolution = f"text_{text_evidence['source']}"
        else:
            # Preserve the existing numeric result, but disclose that the
            # schema itself was not identifiable from the sparse equation.
            resolution = "unresolved_sparse_triangle"

    return chosen, {
        "chosen": name,
        "resolution": resolution,
        "text_evidence": text_evidence,
        "wip": {"status": wip.status, "rank": kw[0],
                "score": round(kw[1], 3),
                "explained": len(wip.mapping)},
        "cc": {"status": cc.status, "rank": kc[0],
               "score": round(kc[1], 3),
               "explained": len(cc.mapping)},
    }
'''
text = replace_once(text, old, new, "schema race")
validation_path.write_text(text, encoding="utf-8")


test_path = ROOT / "tests/test_performance_invariants.py"
tests = test_path.read_text(encoding="utf-8")
addition = '''


def test_decisive_wip_skips_cc(monkeypatch):
    raw = clean_raw_table()
    parsed = parse_node({"raw_table": raw})

    def forbidden_cc(*args, **kwargs):
        raise AssertionError("CC validator should not run for a certified WIP")

    monkeypatch.setattr(validation_mod, "validate_cc", forbidden_cc)
    chosen, race = validation_mod.run_schema_race(
        parsed["matrix"], parsed["job_labels"])

    assert chosen.status == "success"
    assert race["chosen"] == "wip"
    assert race["resolution"] == "wip_certified"
    assert race["cc"]["status"] == "skipped"


def test_decisive_wip_with_findings_still_skips_cc(monkeypatch):
    raw = clean_raw_table()
    # Estimated GP remains identifiable from the other eleven rows, then fails
    # strict certification on this planted bad cell.
    raw["rows"][3][3] = f"{float(raw['rows'][3][3]) + 10000:.8f}"
    parsed = parse_node({"raw_table": raw})

    def forbidden_cc(*args, **kwargs):
        raise AssertionError("CC validator should not run for a certified WIP")

    monkeypatch.setattr(validation_mod, "validate_cc", forbidden_cc)
    chosen, race = validation_mod.run_schema_race(
        parsed["matrix"], parsed["job_labels"])

    assert chosen.status == "validation_failed"
    assert chosen.findings
    assert race["chosen"] == "wip"
    assert race["cc"]["status"] == "skipped"


def test_insufficient_wip_runs_cc(monkeypatch):
    calls = 0
    original_cc = validation_mod.validate_cc

    def counted_cc(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original_cc(*args, **kwargs)

    monkeypatch.setattr(validation_mod, "validate_cc", counted_cc)
    # A pure completed-contract additive lattice has no progress/billing
    # evidence capable of certifying the WIP schema.
    rows = []
    for i in range(8):
        rt = 1_000_000 + i * 100_000
        kt = 800_000 + i * 75_000
        gt = rt - kt
        rp = 600_000 + i * 45_000
        rc = rt - rp
        kp = 480_000 + i * 35_000
        kc = kt - kp
        gp = rp - kp
        gc = rc - kc
        rows.append([rt, kt, gt, rp, rc, kp, kc, gp, gc])
    matrix = np.asarray(rows, dtype=float)

    chosen, race = validation_mod.run_schema_race(
        matrix, [f"CC-{i + 1}" for i in range(len(rows))])

    assert calls == 1
    assert race["cc"]["status"] != "skipped"
    assert race["chosen"] == "cc"
    assert chosen.mapping


def test_all_complete_wip_layout_keeps_cc_escape_hatch(monkeypatch):
    raw = clean_raw_table()
    raw["rows"] = raw["rows"][:-1]
    for row in raw["rows"]:
        value = float(row[1])
        cost = float(row[2])
        row[4] = f"{cost:.8f}"   # D = C
        row[5] = "0.00000000"   # Q = 0
        row[6] = "1.00000000"   # P = 100%
        row[7] = f"{value:.8f}"  # E = V
        row[8] = f"{value:.8f}"  # B = V
        row[9] = "0.00000000"   # U = 0
        row[10] = "0.00000000"  # O = 0
    parsed = parse_node({"raw_table": raw})
    calls = 0
    original_cc = validation_mod.validate_cc

    def counted_cc(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original_cc(*args, **kwargs)

    monkeypatch.setattr(validation_mod, "validate_cc", counted_cc)
    _, race = validation_mod.run_schema_race(
        parsed["matrix"], parsed["job_labels"])

    assert calls == 1
    assert race["cc"]["status"] != "skipped"
'''
if "test_decisive_wip_skips_cc" in tests:
    raise RuntimeError("schema routing tests already present")
test_path.write_text(tests + addition, encoding="utf-8")
