"""Monte Carlo: the metric that matters is WRONG ASSERTIONS (a certified
match that is false). Ambiguity is honest; wrongness is fatal."""
import numpy as np
from job_match import match

def run(seed):
    rng = np.random.default_rng(seed)
    n = int(rng.integers(8, 26))
    prev = []
    base = rng.uniform(300_000, 1_100_000)
    for k in range(n):
        V = round(base * rng.uniform(0.6, 1.6) / 1000) * 1000   # clustered band
        m = rng.uniform(0.06, 0.18)
        C = round(V * (1 - m))
        P = rng.uniform(0.05, 0.92)
        D = round(C * P)
        prev.append({"V": float(V), "C": float(C), "D": float(D),
                     "E": round(V * D / C),
                     "B": round(V * P * rng.uniform(0.85, 1.12))})
    # identical-twin pathology: duplicate one job's (V, C) at a different stage
    if n >= 10:
        tw = dict(prev[0]); P2 = rng.uniform(0.05, 0.92)
        tw["D"] = round(tw["C"] * P2); tw["E"] = round(tw["V"] * tw["D"] / tw["C"])
        tw["B"] = round(tw["V"] * P2 * rng.uniform(0.85, 1.12))
        prev.append(tw); n += 1

    curr, truth = [], {}
    for i, r in enumerate(prev):
        u = rng.random()
        if u < 0.15:      # completes / rolls off
            continue
        dP = rng.uniform(0.03, 0.25)
        dV = rng.uniform(0.04, 0.20) if rng.random() < 0.25 else 0.0   # CO
        dC = rng.uniform(0.03, 0.15) if rng.random() < 0.25 else 0.0   # re-est
        V = round(r["V"] * (1 + dV)); C = round(r["C"] * (1 + dC))
        P1 = min(r["D"] / r["C"] + dP, 1.0)
        D = max(round(C * P1), r["D"]); E = round(V * D / C)
        B = max(round(V * P1 * rng.uniform(0.9, 1.12)), r["B"])
        truth[len(curr)] = i
        curr.append({"V": float(V), "C": float(C), "D": float(D),
                     "E": float(E), "B": float(B)})
    for _ in range(int(rng.integers(0, 4))):   # new starts
        V = round(base * rng.uniform(0.6, 1.6) / 1000) * 1000
        C = round(V * (1 - rng.uniform(0.06, 0.18)))
        P = rng.uniform(0.03, 0.2); D = round(C * P)
        curr.append({"V": float(V), "C": float(C), "D": float(D),
                     "E": round(V * D / C), "B": round(V * P)})
    if rng.random() < 0.5:                     # interim: rounded to $1k
        for s in curr:
            for k in ("V", "C", "D", "E", "B"):
                s[k] = round(s[k] / 1000) * 1000
    order = rng.permutation(len(curr))
    curr = [curr[o] for o in order]
    truth = {int(np.where(order == k)[0][0]): v for k, v in truth.items()}

    matches, ambiguous, new_jobs, closed = match(prev, curr)
    wrong = sum(1 for i, (j, _) in matches.items() if truth.get(j, -1) != i)
    right = sum(1 for i, (j, _) in matches.items() if truth.get(j) == i)
    return right, wrong, len(ambiguous), len(truth)

R = W = A = T = 0
for seed in range(300):
    r, w, a, t = run(seed)
    R += r; W += w; A += a; T += t
print(f"300 books | true continuations: {T}")
print(f"asserted correct: {R} ({R/T:.1%})  WRONG ASSERTIONS: {W} ({W/T:.2%})")
print(f"declared ambiguous (honest punt to name-tiebreak): {A} ({A/T:.1%})")
