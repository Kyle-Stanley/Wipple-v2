"""CC math and the narrow text fallback for schema-identical triangles."""

import numpy as np

from wipple.accounting.cc import validate_cc
from wipple.accounting.concordance import match_header
from wipple.accounting.schemas import CC_VAR_NAMES, WIP_VAR_NAMES
from wipple.accounting.validation import run_schema_race


def _triangle(n=12):
    revenue = np.arange(1, n + 1, dtype=float) * 100_000
    cost = np.round(revenue * 0.82)
    profit = revenue - cost
    return revenue, cost, profit


def test_sparse_header_matching_accepts_exact_and_clear_typo():
    exact = match_header("Costs to Date", WIP_VAR_NAMES)
    close = match_header("Contract Amunt", WIP_VAR_NAMES)

    assert exact == {
        "variable": "D", "match": "exact",
        "synonym": "costs to date", "score": 1.0}
    assert close["variable"] == "V"
    assert close["match"] == "close"


def test_sparse_header_matching_is_scoped_and_conservative():
    assert match_header("Gross Profit", WIP_VAR_NAMES)["variable"] == "G"
    assert match_header("Gross Profit", CC_VAR_NAMES)["variable"] == "GT"
    assert match_header("Cost", WIP_VAR_NAMES) is None
    assert match_header("Completely Unknown", WIP_VAR_NAMES) is None


def test_retainage_is_not_treated_as_unbilled_revenue():
    revenue, cost, profit = _triangle()
    retainage = np.round(revenue * 0.05)
    billed_less_retainage = revenue - retainage
    matrix = np.column_stack([
        revenue, cost, profit, billed_less_retainage, retainage])

    result = validate_cc(matrix)

    assert set(result.mapping.values()) == {"RT", "KT", "GT"}
    assert "BC" not in result.mapping.values()
    assert "RR" not in result.mapping.values()
    assert all("RR" not in witness.relation for witness in result.witnesses)


def test_completed_billings_equal_total_revenue_and_are_certified():
    revenue, cost, profit = _triangle()
    matrix = np.column_stack([revenue, cost, profit, revenue])

    result = validate_cc(matrix)

    assert result.status == "success"
    assert result.mapping[3] == "BC"
    assert {w.relation for w in result.witnesses} >= {
        "RT = KT + GT", "BC = RT"}


def test_bad_completed_billing_is_isolated_by_proven_total_revenue():
    revenue, cost, profit = _triangle()
    billed = revenue.copy()
    billed[4] -= 12_345
    matrix = np.column_stack([revenue, cost, profit, billed])

    result = validate_cc(matrix)

    assert result.status == "validation_failed"
    assert len(result.findings) == 1
    finding = result.findings[0]
    assert finding.culprit_variable == "BC"
    assert finding.proposed_correction == revenue[4]
    assert finding.proof_kind == "inherited"


def test_wip_title_defeats_contract_component_fake_cc_triangle():
    revenue, cost, profit = _triangle()
    original = np.round(revenue * 0.95)
    changes = revenue - original
    matrix = np.column_stack([original, changes, revenue, cost, profit])
    headers = [
        "ORIGINAL CONTRACT", "T&M CHANGE ORDERS", "TOTAL CONTRACT",
        "PROJECTED COSTS", "GROSS PROFIT",
    ]

    _, race = run_schema_race(
        matrix, None, headers=headers, title_texts=["WORK-IN-PROGRESS"])

    assert race["chosen"] == "wip"
    assert race["resolution"] == "text_title"
    assert race["cc"]["explained"] == 3


def test_sparse_cc_title_resolves_math_identical_triangle():
    revenue, cost, profit = _triangle()
    matrix = np.column_stack([revenue, cost, profit])

    _, race = run_schema_race(
        matrix, None,
        headers=["Revenue", "Cost", "Gross Profit"],
        title_texts=["Schedule of Completed Contracts"])

    assert race["chosen"] == "cc"
    assert race["resolution"] == "text_title"


def test_sparse_cc_specific_headers_can_resolve_without_title():
    revenue, cost, profit = _triangle()
    matrix = np.column_stack([revenue, cost, profit])

    _, race = run_schema_race(
        matrix, None,
        headers=["Total Contract Revenue", "Total Costs",
                 "Total Gross Profit"])

    assert race["chosen"] == "cc"
    assert race["resolution"] == "text_headers"


def test_full_cc_lattice_remains_a_math_decision_without_title():
    revenue, cost, profit = _triangle()
    prior_fraction = np.linspace(0.1, 0.8, len(revenue))
    revenue_prior = np.round(revenue * prior_fraction)
    cost_prior = np.round(cost * prior_fraction)
    matrix = np.column_stack([
        revenue_prior, revenue - revenue_prior, revenue,
        cost_prior, cost - cost_prior, cost,
        revenue_prior - cost_prior,
        (revenue - revenue_prior) - (cost - cost_prior),
        profit,
    ])

    _, race = run_schema_race(matrix, None)

    assert race["chosen"] == "cc"
    assert race["resolution"] == "math"
