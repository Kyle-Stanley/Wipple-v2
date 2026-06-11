import numpy as np
from mc_test import run
import mc_test, job_match

# instrument: re-run and capture wrong-match details
wrongs = []
orig_match = job_match.match
import importlib

def run_diag(seed):
    rng = np.random.default_rng(seed)
    # reuse mc_test.run but capture internals by re-calling match ourselves:
    return None

# simpler: patch mc_test.run's wrong counter by re-running with detail
total_fp = total_as = 0
wrong_fp = wrong_as = 0
wrong_to_new = 0
for seed in range(300):
    rng_state = seed
    import mc_test as M
    # monkey: copy of run() with detail (duplicating logic is fine here)
    r, w, a, t = M.run(seed)
# faster: modify approach -- inline a detailed rerun
