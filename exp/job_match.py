"""
Cross-period job matching by invariants, not names. v2: unified certification.

Everything -- including exact (V, C) fingerprint pairs -- goes through one
globally-optimal assignment with a per-edge uniqueness test. Fingerprints
are evidence (near-zero cost), never a bypass: two jobs sharing a
fingerprint produce a near-tie and surface as AMBIGUOUS, not a false anchor.

Hard feasibility gates (any failure kills the edge):
  - D, B cumulative monotone
  - bounded burn (D growth limited by prior cost-to-complete)
  - CO / re-estimate bands on V, C
  - when V and C are unchanged, the bilinear identity E*C = V*D must keep
    holding at t+1, with tolerance scaled to the document's rounding grid
Names are never consulted.
"""
import numpy as np
from scipy.optimize import linear_sum_assignment

REL, ABS = 0.005, 1.0
OPEN = 0.30     # cost of an open slot (new start / completion)
MARGIN = 0.40   # certification margin: alternative must be this much worse

def _grid(jobs):
    vals = [v for j in jobs for v in j.values() if v]
    for g in (1000.0, 100.0):
        if vals and all(abs(v / g - round(v / g)) < 1e-9 for v in vals):
            return g
    return 1.0

def _close(a, b, tol):
    return abs(a - b) <= max(tol, REL * max(abs(a), abs(b)))

def feasible(r, s, g):
    tol = max(ABS, 1.5 * g)
    if s["D"] < r["D"] - tol or s["B"] < r["B"] - tol:
        return False
    Q = max(r["C"] - r["D"], 0.0)
    if s["D"] > r["D"] + 1.75 * Q + 0.15 * r["C"] + tol:
        return False
    if s["B"] > 1.35 * max(r["V"], s["V"]):
        return False
    if not (0.55 * r["V"] <= s["V"] <= 2.2 * r["V"]):
        return False
    if not (0.55 * r["C"] <= s["C"] <= 2.2 * r["C"]):
        return False
    if _close(s["V"], r["V"], tol) and _close(s["C"], r["C"], tol):
        # same job, same estimates => earned identity must still hold
        e_implied = s["V"] * s["D"] / s["C"]
        if abs(s["E"] - e_implied) > max(3.0 * g, 0.01 * max(e_implied, 1)):
            return False
    return True

def pair_cost(r, s, g):
    tol = max(ABS, 1.5 * g)
    c = abs(s["V"] - r["V"]) / max(r["V"], 1) \
      + abs(s["C"] - r["C"]) / max(r["C"], 1)
    dP = s["D"] / s["C"] - r["D"] / r["C"]
    if dP < -0.02:
        c += 6 * (-dP)
    if _close(s["V"], r["V"], tol) and _close(s["C"], r["C"], tol):
        c -= 0.02          # fingerprint bonus: evidence, not bypass
    return max(c, 0.0)

def match(prev, curr):
    g = max(_grid(prev), _grid(curr))
    nR, nS = len(prev), len(curr)
    n = nR + nS
    BIG = 1e6
    Cm = np.full((n, n), BIG)
    Cm[:nR, nS:] = OPEN            # prev job -> open (rolled off)
    Cm[nR:, :nS] = OPEN            # open -> curr job (new start)
    Cm[nR:, nS:] = 0.0
    for i, r in enumerate(prev):
        for j, s in enumerate(curr):
            if feasible(r, s, g):
                Cm[i, j] = pair_cost(r, s, g)
    rows, cols = linear_sum_assignment(Cm)
    base = Cm[rows, cols].sum()
    matches, ambiguous = {}, []
    for a, b in zip(rows, cols):
        if a < nR and b < nS and Cm[a, b] < BIG:
            Cm2 = Cm.copy(); Cm2[a, b] = BIG
            r2, c2 = linear_sum_assignment(Cm2)
            gap = Cm2[r2, c2].sum() - base
            if gap < MARGIN:
                ambiguous.append((a, b, round(float(gap), 4)))
            else:
                tol = max(ABS, 1.5 * g)
                how = ("fingerprint"
                       if _close(prev[a]["V"], curr[b]["V"], tol)
                       and _close(prev[a]["C"], curr[b]["C"], tol)
                       else "assignment")
                matches[a] = (b, how)
    new_jobs = [j for j in range(nS)
                if j not in {m[0] for m in matches.values()}
                and j not in {x[1] for x in ambiguous}]
    closed = [i for i in range(nR) if i not in matches
              and i not in {x[0] for x in ambiguous}]
    return matches, ambiguous, new_jobs, closed
