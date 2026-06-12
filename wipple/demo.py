"""Bundled demo book: a realistic 12-job schedule with planted stories --
two late-stage underbillings, a job borrow, a loss job, a margin outlier,
an early-stage cluster, and one decimal slip whose stated total row was
computed from the TRUE value (so the totals check corroborates the math).
All other identities are exact to the dollar."""

# (name, V, margin, P, B_offset_from_E)
_JOBS = [
    ("Cedar Ridge Clinic",        520_000, 0.12, 0.30, +4_000),
    ("Hillside Library Phase II", 780_000, 0.11, 0.45, -6_000),
    ("Maple Yard Logistics Hub",1_240_000, 0.13, 0.62, +9_000),
    ("Courthouse Annex Repaint",  430_000,-0.03, 0.55, +2_000),   # loss job
    ("Riverbend Treatment Plant", 950_000, 0.10, 0.85, -130_000), # trapped cash
    ("Elm Street Fire Station",   610_000, 0.12, 0.90, -48_000),  # trapped cash
    ("Granite Parkway Bridge",    720_000, 0.11, 0.25, +165_000), # job borrow
    ("Lakeshore Pavilion",        880_000, 0.28, 0.40, +3_000),   # margin outlier
    ("North Campus Dorms",      1_100_000, 0.12, 0.15, -2_000),
    ("Prairie Substation",        690_000, 0.13, 0.08, 0),        # early
    ("Willow Creek Culverts",     540_000, 0.11, 0.05, 0),        # early
    ("Harbor District Garage",    830_000, 0.12, 0.40, +5_000),   # decimal slip on D
]

HEADERS = ["Job", "Contract Price", "Est. Total Cost", "Est. Gross Profit",
           "Costs to Date", "Cost to Complete", "% Complete",
           "Revenues Earned", "Billed to Date", "Under Billings",
           "Over Billings"]

def _m(x): return f"{int(round(x)):,}"

def demo_raw_table_12() -> dict:
    rows, true_cols = [], []
    for name, V, m, P, off in _JOBS:
        C = round(V * (1 - m)); D = round(C * P); E = round(V * P)
        G = V - C; Q = C - D; B = E + off
        U = max(E - B, 0); O = max(B - E, 0)
        true_cols.append((V, C, G, D, Q, E, B, U, O))
        D_print = D * 10 if name == "Harbor District Garage" else D
        rows.append([name, _m(V), _m(C), f"({_m(-G)})" if G < 0 else _m(G),
                     _m(D_print), _m(Q), f"{P*100:.1f}%", _m(E), _m(B),
                     _m(U) if U else "-", _m(O) if O else "-"])
    sums = [sum(t[k] for t in true_cols) for k in range(9)]
    rows.append(["TOTAL", _m(sums[0]), _m(sums[1]), _m(sums[2]), _m(sums[3]),
                 _m(sums[4]), "", _m(sums[5]), _m(sums[6]), _m(sums[7]),
                 _m(sums[8])])
    return {"headers": HEADERS, "rows": rows, "page_count": 1,
            "notes": ["bundled demo schedule"]}


import random as _random


def demo_raw_table() -> dict:
    """Fresh 60-job, 15-column WIP with five planted transcription errors,
    regenerated on every call. Stated totals are computed from the TRUE
    values, so the totals row independently corroborates each correction.
    Errors land only on validator-known columns so each can be isolated."""
    rng = _random.Random()
    F = lambda x: f"{int(round(x)):,}"
    first = ["Riverbend", "Granite", "Harbor", "Cedar", "Maple", "Elm",
             "Prairie", "Lakeshore", "Hillside", "Willow", "Summit",
             "Fairview", "Oakdale", "Stonebridge", "North Fork", "Juniper"]
    second = ["Treatment Plant", "Parkway Bridge", "District Garage",
              "Ridge Clinic", "Yard Logistics Hub", "Street Fire Station",
              "Substation", "Pavilion", "Library Phase II", "Creek Culverts",
              "Courthouse Annex", "Elementary Retrofit", "Water Tower",
              "Transit Center", "Pedestrian Mall"]
    headers = ["Job", "Contract Price", "Est. Total Cost", "Est. Gross Profit",
               "Costs to Date", "Cost to Complete", "% Complete",
               "Revenues Earned", "Earned Gross Profit", "Billed to Date",
               "Net Billing", "Under Billings", "Over Billings",
               "Remaining Revenue", "Remaining Billings", "% Billed"]
    names, used = [], set()
    while len(names) < 60:
        nm = f"{rng.choice(first)} {rng.choice(second)}"
        if nm not in used:
            used.add(nm); names.append(nm)

    rows_true, rows = [], []
    for k in range(60):
        V = rng.randrange(15, 420) * 10_000
        m = rng.randrange(6, 17) / 100
        P = rng.randrange(4, 197) / 2 / 100          # 2.0% .. 98.0% in .5 steps
        # planted underwriting stories on fixed slots
        if k == 7:  m = -0.04                         # loss job
        if k == 19: m = 0.28                          # margin outlier
        if k == 31: P = 0.90                          # trapped cash (late, underbilled)
        if k == 44: P = 0.20                          # job borrow (early, overbilled)
        C = round(V * (1 - m)); D = round(C * P); E = round(V * P)
        jit = rng.randrange(-35, 36) * 1000
        if k == 31: jit = -max(120_000, round(0.12 * V))
        if k == 44: jit = max(150_000, round(0.18 * V))
        B = E + jit
        N = B - E; U = max(-N, 0); O = max(N, 0)
        vals = [V, C, V - C, D, C - D, P, E, E - D, B, N, U, O,
                V - E, V - B, B / V]
        rows_true.append(vals)
        rows.append([names[k], F(V), F(C), F(V - C), F(D), F(C - D),
                     f"{P*100:.1f}%", F(E), F(E - D), F(B), F(N),
                     F(U) if U else "-", F(O) if O else "-",
                     F(V - E), F(V - B), f"{B/V*100:.1f}%"])

    # five errors, one per core column, five distinct rows
    err_cols = [(1, "V"), (2, "C"), (4, "D"), (7, "E"), (9, "B")]
    rng.shuffle(err_cols)
    err_rows = rng.sample(range(60), 5)
    for (col, _), r in zip(err_cols, err_rows):
        true_val = int(rows[r][col].replace(",", ""))
        kind = rng.randrange(3)
        if kind == 0:
            bad = true_val * 10
        elif kind == 1:
            d = str(abs(true_val))
            bad = int(d[1] + d[0] + d[2:]) if len(d) > 1 and d[0] != d[1]                 else true_val * 10
        else:
            bad = int(rows[(r + 11) % 60][col].replace(",", ""))
            if abs(bad - true_val) < 2:
                bad = true_val * 10
        rows[r][col] = F(bad)

    totals = ["TOTAL"]
    for c in range(1, 16):
        if c in (6, 15):
            totals.append("")
        else:
            totals.append(F(sum(rt[c - 1] for rt in rows_true)))
    rows.append(totals)
    return {"headers": headers, "rows": rows, "page_count": 1,
            "notes": ["bundled randomized sample"]}
