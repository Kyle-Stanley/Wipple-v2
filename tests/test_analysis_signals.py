import numpy as np

from wipple.analysis import compute_signals


def signal_ids(core):
    return {signal["id"] for signal in compute_signals(core, ["Test job"])}


def test_completed_billing_gap_is_not_an_underbilling_signal():
    core = {
        "V": np.array([1_000_000.0]),
        "C": np.array([800_000.0]),
        "D": np.array([800_000.0]),
        "E": np.array([1_000_000.0]),
        "B": np.array([900_000.0]),
        "U": np.array([100_000.0]),
        "O": np.array([0.0]),
    }

    ids = signal_ids(core)

    assert "trapped_cash" not in ids
    assert "completed_underbilling" not in ids


def test_completed_job_loss_remains_a_loss_signal():
    core = {
        "V": np.array([1_000_000.0]),
        "C": np.array([1_100_000.0]),
        "D": np.array([1_100_000.0]),
        "E": np.array([1_000_000.0]),
        "B": np.array([900_000.0]),
        "U": np.array([100_000.0]),
        "O": np.array([0.0]),
    }

    ids = signal_ids(core)

    assert "loss_jobs" in ids
    assert "trapped_cash" not in ids


def test_late_stage_underbilling_still_uses_remaining_revenue():
    core = {
        "V": np.array([1_000_000.0]),
        "C": np.array([800_000.0]),
        "D": np.array([760_000.0]),
        "E": np.array([950_000.0]),
        "B": np.array([850_000.0]),
        "U": np.array([100_000.0]),
        "O": np.array([0.0]),
    }

    ids = signal_ids(core)

    assert "trapped_cash" in ids
    assert "completed_underbilling" not in ids
