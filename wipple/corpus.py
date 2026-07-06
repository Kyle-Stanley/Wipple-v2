"""
Synthetic test corpus: ground-truth-first books, a deterministic LAYOUT
engine, and renderers.

The layout engine is the load-bearing piece: it turns a book into per-page
FRAGMENTS -- exactly what a perfect extractor would return per chunk -- and
both consumers draw from it:

  * tests inject the fragments directly (zero model calls, exact-to-the-
    dollar assertions, because ground truth is known);
  * render_pdf / render_png draw the same layout for live vision runs.

Adversarial knobs, each one conversation-derived:
  repeat_headers        header band re-printed on every page
  page_subtotals        blank-label subtotal row per page (parse must strip)
  cc_placement          'own_page' | 'same_page' | 'consolidated'
  vsplit                (m, names_repeated): columns split across facing
                        pages -- the horizontal-stitch case
  shift_chunk           (chunk_id, col): that chunk's cells displaced left by
                        one column, width preserved -- the silent block-
                        misalignment case
  drop_row              (chunk_id, local_row): row missing from one chunk --
                        the seam/dedup case

Books regenerate fresh per call (same philosophy as demo.py): no fixtures to
rot. Pass seed for reproducibility inside one test run.
"""

from __future__ import annotations

import io
import random

_FIRST = ["Riverbend", "Granite", "Harbor", "Cedar", "Maple", "Elm",
          "Prairie", "Lakeshore", "Hillside", "Willow", "Summit", "Fairview",
          "Oakdale", "Stonebridge", "North Fork", "Juniper"]
_SECOND = ["Treatment Plant", "Parkway Bridge", "District Garage",
           "Ridge Clinic", "Yard Logistics Hub", "Street Fire Station",
           "Substation", "Pavilion", "Library Phase II", "Creek Culverts",
           "Courthouse Annex", "Elementary Retrofit", "Water Tower",
           "Transit Center", "Pedestrian Mall"]

WIP_HEADERS = ["Job", "Contract Price", "Est. Total Cost", "Est. Gross Profit",
               "Costs to Date", "Cost to Complete", "% Complete",
               "Revenues Earned", "Billed to Date", "Under Billings",
               "Over Billings"]
CC9_HEADERS = ["Job", "Revenues Earned Prior Years",
               "Revenues Earned Current Year", "Total Revenues Earned",
               "Costs Prior Years", "Costs Current Year", "Total Costs",
               "Gross Profit Prior Years", "Gross Profit Current Year",
               "Total Gross Profit"]
CC3_HEADERS = ["Job", "Contract Price", "Total Cost", "Gross Profit"]


def _F(x):
    return f"{int(round(x)):,}"


def _names(rng, n, used=None):
    used = used if used is not None else set()
    out = []
    while len(out) < n:
        nm = f"{rng.choice(_FIRST)} {rng.choice(_SECOND)}"
        if nm not in used:
            used.add(nm)
            out.append(nm)
    return out


def _plant(rng, rows, true_rows, cols, n_err, section, registry):
    """Plant n_err transcription errors on distinct rows/columns; the stated
    totals (computed from TRUE values by the layout engine) corroborate."""
    err_cells = [(r, c) for r in range(len(rows)) for c in cols]
    rng.shuffle(err_cells)
    seen_rows = set()
    for (r, c) in err_cells:
        if len(registry) >= n_err or r in seen_rows:
            continue
        true_val = int(round(true_rows[r][c - 1]))
        kind = rng.randrange(3)
        if kind == 0:
            bad = true_val * 10
        elif kind == 1:
            d = str(abs(true_val))
            bad = int(d[1] + d[0] + d[2:]) if len(d) > 1 and d[0] != d[1] \
                else true_val * 10
        else:
            bad = true_val + int(10 ** (len(str(abs(true_val))) - 1))
        rows[r][c] = _F(bad)
        registry.append({"section": section, "row": r, "col": c,
                         "true": true_val, "printed": bad})
        seen_rows.add(r)


def build_book(seed=None, n_wip=48, n_cc=12, cc_cols=9,
               wip_errors=2, cc_errors=0) -> dict:
    rng = random.Random(seed)
    used: set = set()

    # ---- WIP section (same generative model as demo.py, parameterized) ----
    wip_rows, wip_true = [], []
    for k, name in enumerate(_names(rng, n_wip, used)):
        V = round(10 ** rng.uniform(5.18, 6.6) / 1000) * 1000
        m = rng.randrange(6, 20) / 100
        P = rng.randrange(4, 195) / 2 / 100
        if k == 5:
            m = -0.04                        # loss job
        C = round(V * (1 - m)); D = round(C * P); E = round(V * P)
        jit = round(V * (0.01 + 0.04 * rng.random()) * (1 - 0.7 * P)
                    * (1 if rng.random() < 0.7 else -1))
        B = E + jit
        U, O = max(E - B, 0), max(B - E, 0)
        wip_true.append([V, C, V - C, D, C - D, P, E, B, U, O])
        wip_rows.append([name, _F(V), _F(C),
                         f"({_F(C - V)})" if V - C < 0 else _F(V - C),
                         _F(D), _F(C - D), f"{P*100:.1f}%", _F(E), _F(B),
                         _F(U) if U else "-", _F(O) if O else "-"])

    # ---- CC section --------------------------------------------------------
    cc_rows, cc_true = [], []
    for name in _names(rng, n_cc, used):
        RT = round(10 ** rng.uniform(5.1, 6.4) / 1000) * 1000
        mgn = rng.randrange(5, 19) / 100
        KT = round(RT * (1 - mgn)); GT = RT - KT
        fr = rng.random()
        RP = round(RT * fr); RC = RT - RP
        KP = round(KT * fr); KC = KT - KP
        GPp, GCc = RP - KP, RC - KC
        if cc_cols == 9:
            cc_true.append([RP, RC, RT, KP, KC, KT, GPp, GCc, GT])
            cc_rows.append([name] + [_F(x) for x in cc_true[-1]])
        else:
            cc_true.append([RT, KT, GT])
            cc_rows.append([name, _F(RT), _F(KT), _F(GT)])

    registry: list = []
    _plant(rng, wip_rows, wip_true, [1, 2, 4, 7, 8], wip_errors, "wip",
           registry)
    if cc_errors:
        cols = list(range(1, (10 if cc_cols == 9 else 4)))
        _plant(rng, cc_rows, cc_true, cols, cc_errors, "cc", registry)

    return {"wip": {"headers": WIP_HEADERS, "rows": wip_rows,
                    "true": wip_true},
            "cc": {"headers": CC9_HEADERS if cc_cols == 9 else CC3_HEADERS,
                   "rows": cc_rows, "true": cc_true, "cols": cc_cols},
            "errors": registry, "seed": seed}


# ---------------------------------------------------------------------------
# Layout engine: book -> per-page fragments (+ per-page ground truth)
# ---------------------------------------------------------------------------

def _total_row(label, true_rows, pct_col=None, width=None):
    sums = [sum(r[k] for r in true_rows) for k in range(len(true_rows[0]))]
    cells = [label] + [_F(s) for s in sums]
    if pct_col is not None:
        cells[pct_col] = ""
    return cells


def _cc_as_wip_row(name, t):
    """A completed contract printed in WIP columns: the degenerate signature
    E=V, D=C, Q=0, P=100%, U=O=0 -- exact to the dollar by construction."""
    RT, KT, GT = (t[2], t[5], t[8]) if len(t) == 9 else (t[0], t[1], t[2])
    return ([name, _F(RT), _F(KT), _F(GT), _F(KT), "0", "100.0%",
             _F(RT), _F(RT), "-", "-"],
            [RT, KT, GT, KT, 0, 1.0, RT, RT, 0, 0])


def layout_fragments(book, rows_per_page=12, repeat_headers=True,
                     cc_placement="own_page", vsplit=None, shift_chunk=None,
                     drop_row=None, page_subtotals=False):
    """Returns (fragments, meta). meta: page_of[(section, row)] -> page,
    n_pages, and the consolidated ground truth used for totals rows."""
    wip = book["wip"]
    frags, page_of = [], {}
    page = 0

    def new_frag(headers, rows, notes=()):
        nonlocal page
        page += 1
        frags.append({"chunk_id": page - 1, "pages": [page],
                      "headers": list(headers), "rows": [list(r) for r in rows],
                      "position": 0, "notes": list(notes)})
        return frags[-1]

    # ---- WIP pages (+ consolidated CC if requested) ------------------------
    stream = [("wip", i, wip["rows"][i], wip["true"][i])
              for i in range(len(wip["rows"]))]
    all_true = [t for (_, _, _, t) in stream]
    if cc_placement == "consolidated":
        fence = _total_row("Total Contracts in Progress",
                           [t for t in all_true], pct_col=6)
        stream.append(("fence", None, fence, None))
        for i, (name_row, t) in enumerate(zip(book["cc"]["rows"],
                                              book["cc"]["true"])):
            row, tvals = _cc_as_wip_row(name_row[0], t)
            stream.append(("cc", i, row, tvals))
            all_true.append(tvals)

    pages_rows = [stream[i:i + rows_per_page]
                  for i in range(0, len(stream), rows_per_page)]
    for pi, chunk_rows in enumerate(pages_rows):
        rows = []
        page_true = []
        for (sec, i, cells, t) in chunk_rows:
            rows.append(cells)
            if t is not None:
                page_true.append(t)
            if i is not None:
                page_of[(sec, i)] = page + 1
        if page_subtotals and page_true:
            rows.append(_total_row("", page_true, pct_col=6))
        if pi == len(pages_rows) - 1:                 # grand total, last page
            rows.append(_total_row("TOTAL", all_true, pct_col=6))
        new_frag(wip["headers"] if (repeat_headers or pi == 0) else
                 [""] * len(wip["headers"]), rows)

    # ---- CC pages ----------------------------------------------------------
    if cc_placement in ("own_page", "same_page"):
        cc = book["cc"]
        cc_rows = [list(r) for r in cc["rows"]]
        cc_rows.append(_total_row("TOTAL Completed Contracts", cc["true"]))
        for i in range(len(cc["rows"])):
            page_of[("cc", i)] = page + (0 if cc_placement == "same_page"
                                         else 1)
        if cc_placement == "same_page":
            f = frags[-1]
            f["rows"] = f["rows"]                     # WIP table stays intact
            frags.append({"chunk_id": f["chunk_id"], "pages": list(f["pages"]),
                          "headers": cc["headers"], "rows": cc_rows,
                          "position": 1,
                          "notes": ["second table on same page"]})
        else:
            new_frag(cc["headers"], cc_rows, ["completed contracts page"])

    # ---- vertical column split: each page -> left/right chunk pair ---------
    if vsplit is not None:
        m, names_repeated = vsplit
        split = []
        for f in frags:
            left = {**f, "chunk_id": len(split), "pages": [len(split) + 1],
                    "headers": f["headers"][:m],
                    "rows": [r[:m] for r in f["rows"]],
                    "notes": f["notes"] + ["left half"]}
            rh = ([f["headers"][0]] if names_repeated else []) \
                + f["headers"][m:]
            right = {**f, "chunk_id": len(split) + 1,
                     "pages": [len(split) + 2], "headers": rh,
                     "rows": [([r[0]] if names_repeated else []) + r[m:]
                              for r in f["rows"]],
                     "notes": f["notes"] + ["right half (continuation)"]}
            split += [left, right]
        frags = split

    # ---- adversarial knobs --------------------------------------------------
    if drop_row is not None:
        cid, local = drop_row
        for f in frags:
            if f["chunk_id"] == cid and local < len(f["rows"]):
                del f["rows"][local]
                break
    if shift_chunk is not None:
        cid, col = shift_chunk
        for f in frags:
            if f["chunk_id"] == cid:
                for r in f["rows"]:
                    del r[col]
                    r.append("")       # width preserved, values displaced left
                break

    return frags, {"page_of": page_of, "n_pages": page,
                   "n_fragments": len(frags)}


# ---------------------------------------------------------------------------
# Renderers (live-run path; tests inject fragments directly)
# ---------------------------------------------------------------------------

def render_pdf(fragments) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import landscape, letter
    from reportlab.lib.units import inch
    from reportlab.platypus import SimpleDocTemplate, Spacer, Table, TableStyle

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(letter),
                            leftMargin=0.4 * inch, rightMargin=0.4 * inch,
                            topMargin=0.5 * inch, bottomMargin=0.5 * inch)
    style = TableStyle([
        ("FONTSIZE", (0, 0), (-1, -1), 6.5),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
        ("LINEBELOW", (0, 0), (-1, 0), 0.5, colors.black),
        ("TOPPADDING", (0, 0), (-1, -1), 1.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 1.5),
    ])
    flow, by_page = [], {}
    for f in fragments:
        by_page.setdefault(f["pages"][0], []).append(f)
    from reportlab.platypus import PageBreak
    for p in sorted(by_page):
        for f in sorted(by_page[p], key=lambda x: x["position"]):
            data = [f["headers"]] + f["rows"]
            flow += [Table(data, repeatRows=0, style=style),
                     Spacer(1, 0.25 * inch)]
        flow.append(PageBreak())
    doc.build(flow[:-1] if flow else [])
    return buf.getvalue()


def render_png(fragments, width=1400, row_h=22) -> bytes:
    """One tall image (the long-screenshot case): all fragments stacked."""
    from PIL import Image, ImageDraw
    rows = sum(len(f["rows"]) + 2 for f in fragments)
    img = Image.new("RGB", (width, rows * row_h + 40), "white")
    d = ImageDraw.Draw(img)
    y = 10
    for f in fragments:
        ncol = max(len(f["headers"]), 1)
        cw = (width - 20) // ncol
        for cells, bold in [(f["headers"], True)] + \
                [(r, False) for r in f["rows"]]:
            for j, c in enumerate(cells):
                d.text((12 + j * cw, y), str(c)[:18], fill="black")
            y += row_h
        y += row_h
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
