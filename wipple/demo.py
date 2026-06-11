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

def demo_raw_table() -> dict:
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
