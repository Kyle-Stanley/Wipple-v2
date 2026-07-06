"""
Stitcher: fragments -> logical tables, on structural signatures alone.

Cheap signals assemble; the whole-table math (downstream) verifies the
assembly once. Nothing here reads a header semantically and nothing here
runs the validator -- signatures are string-and-shape work: per-column kind
(text / money / percent / blank), money magnitude decade, sign mix. The
stitcher's failures are recoverable by construction: an over-merge lands in
the splitter, an under-merge or bad zip surfaces as seam-shaped identity
failures with the provenance to route a re-extract. That recoverability is
the license to keep this layer dumb.

Two operators, driven by the same signature function from opposite sides:

  vertical    same column signature, adjacent pages -> same table continued.
              Repeated header bands dropped; for image strips, overlapping
              rows deduped by label, and any cell disagreement inside the
              overlap recorded as an extraction-quality witness.

  horizontal  a landscape table split columnwise across pages. Recognized by
              equal row counts + either a repeated label column or no label
              column at all (naked numeric columns are themselves a tell:
              standalone tables have row labels). Joined by name when names
              repeat (order-independent; absentees become findings), else
              positionally -- and the cross-seam identities downstream are
              the per-row proof the zip was right.

Every output row carries row_prov: (chunk_id, page, local_row) -- the field
re-extraction routing, page-cited findings, and block-misalignment repair
all hang off.
"""

from __future__ import annotations

from .parsing import parse_cell


# ---------------------------------------------------------------------------
# Signatures
# ---------------------------------------------------------------------------

def _cell_kind(c: str) -> str:
    c = (c or "").strip()
    if not c or c == "-":
        return "blank"
    if c.endswith("%"):
        return "pct"
    v, _ = parse_cell(c)
    if v is not None and v == v:
        return "money"
    return "text"


def col_signature(rows: list, j: int) -> dict:
    kinds, mags = {}, []
    for r in rows:
        if j >= len(r):
            continue
        k = _cell_kind(r[j])
        kinds[k] = kinds.get(k, 0) + 1
        if k == "money":
            v, _ = parse_cell(r[j])
            if v:
                mags.append(len(str(int(abs(v)))))
    n = sum(kinds.values()) or 1
    kind = max(kinds, key=kinds.get) if kinds else "blank"
    if kinds.get("blank", 0) == n:
        kind = "blank"
    return {"kind": kind,
            "mag": sorted(mags)[len(mags) // 2] if mags else 0}


def signature(frag: dict) -> list:
    return [col_signature(frag["rows"], j)
            for j in range(len(frag["headers"]) or
                           max((len(r) for r in frag["rows"]), default=0))]


def sig_compatible(a: list, b: list) -> bool:
    if len(a) != len(b) or not a:
        return False
    agree = 0
    for sa, sb in zip(a, b):
        if "blank" in (sa["kind"], sb["kind"]) or sa["kind"] == sb["kind"]:
            if sa["kind"] == "money" == sb["kind"] and \
                    abs(sa["mag"] - sb["mag"]) > 2:
                continue
            agree += 1
    return agree / len(a) >= 0.8


# ---------------------------------------------------------------------------
# Vertical stitching
# ---------------------------------------------------------------------------

def _is_header_repeat(row: list, headers: list) -> bool:
    if not headers:
        return False
    hits = sum(1 for c, h in zip(row, headers)
               if str(c).strip() and str(c).strip().lower() ==
               str(h).strip().lower())
    return hits >= max(3, 0.6 * sum(1 for h in headers if str(h).strip()))


def _label_col(rows: list) -> int | None:
    if not rows:
        return None
    for j in range(len(rows[0])):
        if sum(1 for r in rows if j < len(r) and
               _cell_kind(r[j]) == "text") >= 0.6 * len(rows):
            return j
    return None


def _dedup_overlap(acc_rows, acc_prov, new_rows, new_prov, issues):
    """Image-strip overlap: longest suffix of acc matching a prefix of new,
    keyed on the label cell. Verbatim cell agreement inside the overlap is
    the free extraction witness; disagreement is recorded, not smoothed."""
    lc = _label_col(acc_rows) if acc_rows else None
    max_k = min(len(acc_rows), len(new_rows), 12)
    for k in range(max_k, 0, -1):
        tail, head = acc_rows[-k:], new_rows[:k]
        if lc is None:
            if tail == head:
                return new_rows[k:], new_prov[k:]
            continue
        if all(lc < len(t) and lc < len(h) and t[lc].strip() and
               t[lc] == h[lc] for t, h in zip(tail, head)):
            for t, h, pv in zip(tail, head, new_prov[:k]):
                if t != h:
                    diff = [j for j, (x, y) in enumerate(zip(t, h)) if x != y]
                    issues.append({
                        "kind": "overlap_mismatch", "chunk_id": pv[0][0],
                        "page": pv[0][1], "row_label": t[lc], "columns": diff,
                        "note": "same physical row extracted twice with "
                                "different values -- extraction unreliable "
                                "on this strip"})
            return new_rows[k:], new_prov[k:]
    return new_rows, new_prov


def _vertical_groups(fragments: list) -> list:
    frags = sorted(fragments, key=lambda f: (f["pages"][0], f["position"]))
    groups = []
    for f in frags:
        sig = signature(f)
        rows = [list(r) for r in f["rows"]]
        prov = f.get("prov") or [[(f["chunk_id"], f["pages"][0], i)]
                                 for i in range(len(rows))]
        fchunks = set(f.get("chunks", {f["chunk_id"]}))
        fissues = list(f.get("issues", []))
        placed = False
        for g in groups:
            if f["pages"][0] - g["last_page"] <= 2 and \
                    sig_compatible(g["sig"], sig):
                issues: list = []
                keep_rows, keep_prov = [], []
                for r, p in zip(rows, prov):
                    if _is_header_repeat(r, g["headers"]):
                        continue
                    keep_rows.append(r)
                    keep_prov.append(p)
                if f.get("overlaps_prev"):
                    keep_rows, keep_prov = _dedup_overlap(
                        g["rows"], g["prov"], keep_rows, keep_prov, issues)
                # Vertical continuation means NEW rows. A fragment whose
                # remaining labels heavily duplicate the group's is a
                # columnar continuation (repeated name column) wearing a
                # similar signature -- leave it for the horizontal pass.
                lc = _label_col(g["rows"])
                if lc is not None and keep_rows:
                    have = {r[lc].strip() for r in g["rows"]
                            if lc < len(r) and r[lc].strip()}
                    dup = sum(1 for r in keep_rows if lc < len(r)
                              and r[lc].strip() in have)
                    if dup / len(keep_rows) > 0.4:
                        continue
                g["issues"] += fissues + issues
                g["rows"] += keep_rows
                g["prov"] += keep_prov
                g["last_page"] = f["pages"][-1]
                g["chunks"] |= fchunks
                placed = True
                break
        if not placed:
            headers = f["headers"] if any(str(h).strip()
                                          for h in f["headers"]) else []
            groups.append({"headers": headers or f["headers"], "sig": sig,
                           "rows": rows, "prov": prov,
                           "last_page": f["pages"][-1],
                           "first_page": f["pages"][0],
                           "chunks": fchunks, "issues": fissues})
    return groups


# ---------------------------------------------------------------------------
# Horizontal pairing (fragment level, BEFORE vertical stitching)
# ---------------------------------------------------------------------------
# A columnar continuation pairs with its base page the way the paper does:
# facing pages first, then the full-width pages stitch vertically. Pairing
# at the group level runs after over-merges have already deranged the row
# counts; pairing at the fragment level sees the document as printed.

def _pair_fragments(fragments: list) -> list:
    frags = sorted(fragments, key=lambda f: (f["pages"][0], f["position"]))
    for f in frags:
        f.setdefault("issues", [])
        f.setdefault("chunks", {f["chunk_id"]})
        if not isinstance(f["rows"], list):
            f["rows"] = list(f["rows"])
        f.setdefault("prov", [[(f["chunk_id"], f["pages"][0], i)]
                              for i in range(len(f["rows"]))])
    out, used = [], set()
    for i, L in enumerate(frags):
        if i in used:
            continue
        llc = _label_col(L["rows"])
        for j in range(i + 1, len(frags)):
            if j in used:
                continue
            R = frags[j]
            if R["pages"][0] - L["pages"][0] > 1 or llc is None:
                break
            if abs(len(L["rows"]) - len(R["rows"])) > 2:
                continue
            rlc = _label_col(R["rows"])
            if rlc is None and sig_compatible(signature(L), signature(R)):
                continue      # same-shaped, label-less: not a continuation
            merged = _join_pair(L, R, llc, rlc)
            if merged is not None:
                out.append(merged)
                used.update({i, j})
                break
        if i not in used:
            out.append(L)
            used.add(i)
    return out


def _join_pair(L: dict, R: dict, llc: int, rlc):
    issues = list(L["issues"]) + list(R["issues"])
    if rlc is not None:
        lnames = [r[llc].strip() for r in L["rows"]]
        rmap = {}
        for k, r in enumerate(R["rows"]):
            rmap.setdefault(r[rlc].strip(), k)
        named = [n for n in lnames if n]
        if not named or sum(1 for n in named if n in rmap) < 0.8 * len(named):
            return None
        rows, prov = [], []
        for k, (lr, name) in enumerate(zip(L["rows"], lnames)):
            if name in rmap:
                rk = rmap[name]
                rr = [c for q, c in enumerate(R["rows"][rk]) if q != rlc]
                rows.append(lr + rr)
                prov.append(L["prov"][k] + R["prov"][rk])
            else:
                rows.append(lr + [""] * (len(R["headers"]) - 1))
                prov.append(list(L["prov"][k]))
                issues.append({"kind": "hjoin_missing_row", "row_label": name,
                               "page": R["pages"][0],
                               "note": "row present on the base page but "
                                       "absent from the continuation page"})
        rheaders = [h for q, h in enumerate(R["headers"]) if q != rlc]
    else:
        if len(L["rows"]) != len(R["rows"]):
            issues.append({"kind": "hjoin_rowcount_mismatch",
                           "note": f"{len(L['rows'])} vs {len(R['rows'])} rows; "
                                   "positional zip truncated -- expect seam-"
                                   "shaped identity failures from the "
                                   "misalignment row onward"})
        n = min(len(L["rows"]), len(R["rows"]))
        rows = [L["rows"][k] + R["rows"][k] for k in range(n)]
        prov = [L["prov"][k] + R["prov"][k] for k in range(n)]
        rheaders = R["headers"]
    return {"chunk_id": L["chunk_id"], "pages": L["pages"] + R["pages"],
            "headers": list(L["headers"]) + list(rheaders), "rows": rows,
            "prov": prov, "position": L["position"], "issues": issues,
            "chunks": L["chunks"] | R["chunks"], "joined": True}


def stitch(fragments: list) -> list:
    """fragments -> logical tables: {headers, rows, row_prov, issues,
    chunks, pages}."""
    groups = _vertical_groups(_pair_fragments(
        [dict(f) for f in fragments]))
    tables = []
    for g in groups:
        tables.append({
            "headers": g["headers"],
            "rows": g["rows"],
            "row_prov": g["prov"],          # per row: [(chunk, page, local)+]
            "issues": g["issues"],
            "chunks": sorted(g["chunks"]),
            "pages": [g["first_page"], g["last_page"]],
            "joined_columns": bool(g.get("joined")) or
                any(len(p) > 1 for p in g["prov"]),
        })
    return tables
