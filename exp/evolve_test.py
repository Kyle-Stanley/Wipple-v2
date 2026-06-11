"""Synthetic two-period book with every nasty case Kyle named:
renamed jobs, interims with different naming conventions (names are
withheld entirely from the matcher), simultaneous CO + re-estimate,
completions, new starts, and near-twin jobs."""
import numpy as np
from job_match import match

rng = np.random.default_rng(7)

def mk(V, P, margin=0.12):
    C = round(V * (1 - margin))
    D = round(C * P)
    return {"V": float(V), "C": float(C), "D": float(D),
            "E": round(V * D / C), "B": round(V * P * rng.uniform(0.85, 1.1))}

# Period t: 14 jobs, clustered contract band (smalltown contractor style)
names_t, prev = [], []
specs = [(480_000,.15),(520_000,.30),(610_000,.45),(495_000,.60),(700_000,.72),
         (655_000,.80),(540_000,.88),(820_000,.10),(760_000,.25),(580_000,.50),
         (505_000,.55),(670_000,.35),(905_000,.65),(450_000,.93)]
for k,(V,P) in enumerate(specs):
    prev.append(mk(V,P)); names_t.append(f"Job-{k:02d}")

# --- evolve to t+1 ----------------------------------------------------------
curr, truth = [], {}   # truth: curr idx -> prev idx
def advance(r, dP, dV=0.0, dC=0.0):
    V = round(r["V"] * (1 + dV)); C = round(r["C"] * (1 + dC))
    P0 = r["D"] / r["C"]; P1 = min(P0 + dP, 1.0)
    D = max(round(C * P1), r["D"])           # cumulative
    E = round(V * D / C)
    B = max(round(V * P1 * rng.uniform(0.9, 1.12)), r["B"])
    return {"V": float(V), "C": float(C), "D": float(D), "E": float(E), "B": float(B)}

plan = {
    0: dict(dP=.18), 1: dict(dP=.15), 2: dict(dP=.12),
    3: dict(dP=.10, dV=.09, dC=.08),     # simultaneous CO + re-estimate
    4: dict(dP=.11),
    5: dict(dP=.08, dC=.06),             # re-estimate only (fade!)
    7: dict(dP=.22), 8: dict(dP=.16, dV=.12),  # change order only
    9: dict(dP=.14), 10: dict(dP=.13),         # 9 & 10 are near-twins in V
    11: dict(dP=.15), 12: dict(dP=.09),
}                                             # 6 & 13 complete -> roll off
for i, kw in plan.items():
    truth[len(curr)] = i
    curr.append(advance(prev[i], **kw))
for V, P in [(530_000, .07), (615_000, .12)]:  # new starts
    curr.append(mk(V, P))

order = rng.permutation(len(curr))             # shuffle row order too
curr = [curr[o] for o in order]
truth = {int(np.where(order == k)[0][0]): v for k, v in truth.items()}

matches, ambiguous, new_jobs, closed = match(prev, curr)

ok = sum(1 for i,(j,how) in matches.items() if truth.get(j) == i)
bad = sum(1 for i,(j,how) in matches.items() if truth.get(j, -1) != i)
print(f"asserted: {len(matches)}  correct: {ok}  WRONG: {bad}")
for i,(j,how) in sorted(matches.items()):
    tag = "OK " if truth.get(j)==i else "BAD"
    print(f"  {tag} t[{i:2d}] -> t+1[{j:2d}]  via {how}")
print("ambiguous (left to name-tiebreak):", ambiguous,
      "-> true pairs there:", [(truth.get(j), j) for _,j,_ in ambiguous] if ambiguous else "n/a")
print("new starts detected:", sorted(new_jobs),
      "| truth:", sorted(set(range(len(curr))) - set(truth)))
print("closed/rolled-off detected:", sorted(closed), "| truth: [6, 13]")
