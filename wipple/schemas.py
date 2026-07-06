"""
Completed Contracts schema + the degenerate-WIP signature.

A CC schedule is the 3x3 additive lattice {Revenue, Cost, Gross Profit} x
{Prior, Current, Total}: every grid-row is an identity (K. + G. = R. per
period) and every grid-column is an identity (.P + .C = .T per measure).
Six constraints, five independent, every variable on exactly two -- which is
why the 9-column variant certifies HARDER than a sparse WIP despite being
"just addition". The 3-column variant is the degenerate corner {RT, KT, GT}
with the single identity KT + GT = RT.

Var codes are disjoint from the WIP engine's so a mapping is always
unambiguous about which schema produced it.
"""

from __future__ import annotations

from .wip_validator import VAR_NAMES as WIP_VAR_NAMES

CC_VAR_NAMES = {
    "RT": "Total Contract Revenue",
    "RP": "Revenue Earned Prior Years",
    "RC": "Revenue Earned Current Year",
    "KT": "Total Contract Cost",
    "KP": "Cost Incurred Prior Years",
    "KC": "Cost Incurred Current Year",
    "GT": "Total Gross Profit",
    "GP": "Gross Profit Prior Years",
    "GC": "Gross Profit Current Year",
    "BC": "Billed to Date (Completed)",
    "RR": "Retainage Receivable",
}

# The lattice as (addend, addend, sum) triples over var codes. Period splits
# (.P + .C = .T) are ORDER-AMBIGUOUS in the math: prior/current are both just
# summands, so the engine certifies the slice structure and emits a competing
# mapping with the periods swapped -- the header disambiguator answers one
# question, exactly the organ the WIP engine already exercises.
CC_LATTICE = [
    ("KT", "GT", "RT"),   # measure relation, total column
    ("KP", "GP", "RP"),   # measure relation, prior slice
    ("KC", "GC", "RC"),   # measure relation, current slice
    ("RP", "RC", "RT"),   # period split, revenue
    ("KP", "KC", "KT"),   # period split, cost
    ("GP", "GC", "GT"),   # period split, gross profit
]
PERIOD_SWAP = {"RP": "RC", "RC": "RP", "KP": "KC", "KC": "KP",
               "GP": "GC", "GC": "GP"}

# Merged lookup for emit/concordance layers.
ALL_VAR_NAMES = {**WIP_VAR_NAMES, **CC_VAR_NAMES}


def degenerate_wip_rows(core: dict, tol: float = 1.26) -> list:
    """Per-row exact-degeneracy hits against the completed-contract signature
    E=V, D=C, Q=0, P=1, U=0, O=0, evaluated over whichever core arrays exist.

    Returns per-row (n_hits, n_available). The splitter demands ALL available
    conditions hit with n_available >= 3: one fuzzy 'high percent complete'
    never qualifies, k exact identities agreeing do. tol = money_obs_tol +
    cert_slack, the same strictness certification itself uses.
    """
    import numpy as np
    resids, tols = [], []
    g = core.get
    if g("E") is not None and g("V") is not None:
        resids.append(np.asarray(g("E"), float) - np.asarray(g("V"), float))
        tols.append(tol)
    if g("D") is not None and g("C") is not None:
        resids.append(np.asarray(g("D"), float) - np.asarray(g("C"), float))
        tols.append(tol)
    for var, t in (("Q", tol), ("U", tol), ("O", tol)):
        if g(var) is not None:
            resids.append(np.asarray(g(var), float))
            tols.append(t)
    if g("P") is not None:
        resids.append(np.asarray(g("P"), float) - 1.0)
        tols.append(0.005)
    if not resids:
        return []
    R = np.vstack(resids)
    avail = np.isfinite(R)
    hit = avail & (np.abs(np.nan_to_num(R)) <= np.array(tols)[:, None])
    return [(int(hit[:, r].sum()), int(avail[:, r].sum()))
            for r in range(R.shape[1])]
