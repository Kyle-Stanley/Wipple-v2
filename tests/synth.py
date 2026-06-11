"""Synthetic WIP generator: exact-integer identities, printed as raw strings."""

HEADERS = ["Job #", "Contract Price", "Est. Total Cost", "Est. Gross Profit",
           "Costs to Date", "Cost to Complete", "% Complete",
           "Revenues Earned", "Billed to Date", "Underbillings",
           "Overbillings"]

# (V, C, P, B) chosen so D=C*P, E=V*P are exact integers.
JOBS = [
    ("J-101", 500_000, 400_000, 0.10, 60_000),
    ("J-102", 800_000, 600_000, 0.20, 150_000),
    ("J-103", 1_200_000, 1_000_000, 0.25, 300_000),
    ("J-104", 400_000, 300_000, 0.40, 170_000),
    ("J-105", 2_000_000, 1_600_000, 0.50, 1_000_000),
    ("J-106", 900_000, 700_000, 0.60, 500_000),
    ("J-107", 600_000, 480_000, 0.75, 450_000),
    ("J-108", 1_500_000, 1_200_000, 0.80, 1_250_000),
]


def money(x: float) -> str:
    return f"{int(round(x)):,}"


def rows_numeric():
    out = []
    for name, V, C, P, B in JOBS:
        D = C * P
        E = V * P
        G = V - C
        Q = C - D
        U = max(E - B, 0)
        O = max(B - E, 0)
        out.append((name, V, C, G, D, Q, P, E, B, U, O))
    return out


def raw_table(corrupt: dict | None = None, with_totals: bool = True,
              columns: list | None = None) -> dict:
    """corrupt: {(row, header_name): raw_string_override}"""
    numeric = rows_numeric()
    keep = columns or HEADERS
    idx = [HEADERS.index(h) for h in keep]
    rows = []
    for i, tup in enumerate(numeric):
        cells = []
        for h, j in zip(keep, idx):
            v = tup[j]
            if h == "Job #":
                cells.append(str(v))
            elif h == "% Complete":
                cells.append(f"{v * 100:.1f}%")
            elif h in ("Underbillings", "Overbillings") and v == 0:
                cells.append("-")
            else:
                cells.append(money(v))
        if corrupt:
            for (ri, hname), override in corrupt.items():
                if ri == i and hname in keep:
                    cells[keep.index(hname)] = override
        rows.append(cells)
    if with_totals:
        tot = []
        for h, j in zip(keep, idx):
            if h == "Job #":
                tot.append("TOTAL")
            elif h == "% Complete":
                tot.append("")
            else:
                tot.append(money(sum(t[j] for t in numeric)))
        rows.append(tot)
    return {"headers": keep, "rows": rows, "page_count": 1, "notes": []}
