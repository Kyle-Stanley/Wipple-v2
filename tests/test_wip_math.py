import json
import subprocess
from pathlib import Path


MODULE = Path(__file__).parents[1] / "static" / "wip_math.js"


def run_js(expression, *args):
    script = f"""
const fs = require("fs");
const source = fs.readFileSync(process.argv[1], "utf8");
const loaded = {{exports: {{}}}};
new Function("module", "exports", source)(loaded, loaded.exports);
const args = process.argv.slice(2).map(JSON.parse);
process.stdout.write(JSON.stringify({expression}));
"""
    result = subprocess.run(
        ["node", "-e", script, str(MODULE), *[json.dumps(arg) for arg in args]],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def derive(values):
    return run_js("loaded.exports.deriveCanonicalVars(args[0])", values)


def readiness(variables):
    return run_js("loaded.exports.mappingReadiness(args[0])", variables)


def corroboration(rows, mapping, ignored=None):
    return run_js(
        "loaded.exports.inferCorroboratingColumns(args[0], args[1], args[2])",
        rows,
        mapping,
        ignored or [],
    )


def fixed_audit(rows, mapping, labels=None):
    return run_js(
        "loaded.exports.auditFixedMapping(args[0], args[1], args[2])",
        rows,
        mapping,
        labels or [],
    )


def test_full_wip_fields_are_derived_from_validated_baselines():
    values = derive({"V": 1_000_000, "C": 800_000, "D": 400_000,
                     "E": 500_000, "B": 450_000})

    assert values == {
        "V": 1_000_000,
        "C": 800_000,
        "D": 400_000,
        "E": 500_000,
        "B": 450_000,
        "G": 200_000,
        "P": 0.5,
        "Q": 400_000,
        "R": 500_000,
        "RB": 550_000,
        "M": 0.2,
        "PB": 0.45,
        "H": 100_000,
        "U": 50_000,
        "O": 0,
        "N": -50_000,
    }


def test_printed_values_are_not_overwritten():
    values = derive({"V": 100, "C": 80, "D": 40, "E": 50, "B": 45,
                     "G": 25, "P": 0.75})

    assert values["G"] == 25
    assert values["P"] == 0.75


def test_alternative_dollar_anchors_close_the_full_wip():
    values = derive({"V": 1_000_000, "G": 200_000, "Q": 400_000,
                     "U": 50_000, "O": 0})

    assert values["C"] == 800_000
    assert values["D"] == 400_000
    assert values["E"] == 500_000
    assert values["B"] == 450_000
    assert values["P"] == 0.5


def test_earned_profit_can_reconstruct_cost_to_date_without_percent_math():
    values = derive({"E": 500_000, "H": 100_000})

    assert values["D"] == 400_000


def test_readiness_requires_profit_progress_and_billing():
    assert readiness(["V", "C"])["score"] == 1
    assert readiness(["V", "C", "D"])["score"] == 2
    ready = readiness(["V", "C", "D", "B"])
    assert ready["complete"] is True
    assert ready["score"] == 3


def test_percent_complete_does_not_satisfy_progress_requirement():
    result = readiness(["V", "C", "P", "B"])

    assert result["score"] == 2
    assert result["complete"] is False


def test_complete_mapping_corroborates_a_unique_untouched_money_column():
    rows = [
        [1000, 800, 400, 500, 200],
        [2000, 1500, 600, 800, 500],
        [3000, 2400, 1200, 1500, 600],
    ]

    inferred = corroboration(rows, {"0": "V", "1": "C", "2": "D", "3": "B"})

    assert inferred["4"]["variable"] == "G"
    assert "contract value" in inferred["4"]["reason"].lower()
    assert inferred["4"]["matchedRows"] == 3
    assert inferred["4"]["mismatches"] == 0


def test_corroboration_accepts_small_rounding_residue_from_ratio_math():
    rows = [
        [1_000_000, 800_000, 500_000, 450_000, 400_006],
        [2_000_000, 1_500_000, 800_000, 760_000, 600_011],
        [3_000_000, 2_400_000, 1_500_000, 1_440_000, 1_200_019],
        [4_000_000, 3_200_000, 2_000_000, 1_900_000, 1_600_028],
    ]

    inferred = corroboration(rows, {"0": "V", "1": "C", "2": "E", "3": "B"})

    assert inferred["4"]["variable"] == "D"
    assert inferred["4"]["matchedRows"] == 4


def test_corroboration_tolerates_up_to_two_corrupted_cells_on_a_long_schedule():
    rows = []
    for index in range(12):
        value = (index + 1) * 1000
        estimated_cost = value * 0.8
        earned = value * 0.3
        billings = earned * 0.9
        cost_to_date = earned * estimated_cost / value
        if index in {3, 9}:
            cost_to_date += 91_000
        rows.append([value, estimated_cost, earned, billings, cost_to_date])

    inferred = corroboration(rows, {"0": "V", "1": "C", "2": "E", "3": "B"})

    assert inferred["4"]["variable"] == "D"
    assert inferred["4"]["matchedRows"] == 10
    assert inferred["4"]["comparedRows"] == 12
    assert inferred["4"]["mismatches"] == 2
    assert "10 of 12 rows" in inferred["4"]["reason"]


def test_large_schedule_corroboration_survives_three_poisoned_predictions():
    rows = []
    for index in range(28):
        value = (index + 10) * 100_000
        estimated_cost = value * 0.8
        earned = value * 0.45
        billings = earned * 0.93
        cost_to_date = earned * estimated_cost / value
        printed_value = value + (90_000 if index == 3 else 0)
        printed_cost = estimated_cost + (70_000 if index == 11 else 0)
        printed_earned = earned + (80_000 if index == 21 else 0)
        rows.append([printed_value, printed_cost, printed_earned, billings, cost_to_date])

    inferred = corroboration(rows, {"0": "V", "1": "C", "2": "E", "3": "B"})

    assert inferred["4"]["variable"] == "D"
    assert inferred["4"]["matchedRows"] == 25
    assert inferred["4"]["mismatches"] == 3


def test_corroboration_rejects_more_than_two_corrupted_cells_in_a_small_schedule():
    rows = []
    for index in range(12):
        value = (index + 1) * 1000
        estimated_cost = value * 0.8
        earned = value * 0.3
        billings = earned * 0.9
        cost_to_date = earned * estimated_cost / value
        if index in {2, 6, 10}:
            cost_to_date += 91_000
        rows.append([value, estimated_cost, earned, billings, cost_to_date])

    inferred = corroboration(rows, {"0": "V", "1": "C", "2": "E", "3": "B"})

    assert "4" not in inferred


def test_mapped_cost_to_date_is_confirmed_without_self_witness():
    rows = [
        [1_000_000, 800_000, 500_000, 400_000, 100_000, 450_000],
        [2_000_000, 1_600_000, 1_000_000, 800_000, 200_000, 900_000],
        [3_000_000, 2_400_000, 1_500_000, 1_200_000, 300_000, 1_350_000],
        [4_000_000, 3_200_000, 2_000_000, 1_600_000, 400_000, 1_800_000],
    ]
    mapping = {"0": "V", "1": "C", "2": "E", "3": "D", "4": "H", "5": "B"}

    inferred = corroboration(rows, mapping)

    assert inferred["3"]["variable"] == "D"
    assert inferred["3"]["confirmed"] is True
    assert inferred["3"]["matchedRows"] == 4


def test_corroboration_never_changes_a_user_touched_column():
    rows = [
        [1000, 800, 400, 500, 200],
        [2000, 1500, 600, 800, 500],
        [3000, 2400, 1200, 1500, 600],
    ]

    inferred = corroboration(
        rows, {"0": "V", "1": "C", "2": "D", "3": "B"}, ignored=[4],
    )

    assert inferred == {}


def test_ambiguous_corroboration_stays_unmapped():
    rows = [
        [1000, 800, 400, 500, 200, 200],
        [2000, 1500, 600, 800, 500, 500],
        [3000, 2400, 1200, 1500, 600, 600],
    ]

    inferred = corroboration(rows, {"0": "V", "1": "C", "2": "D", "3": "B"})

    assert "4" not in inferred
    assert "5" not in inferred


def test_fixed_mapping_audit_flags_inconsistent_rows_without_naming_a_culprit():
    rows = [
        [1_000_000, 800_000, 500_000, 400_000, 450_000],
        [2_000_000, 1_600_000, 1_000_000, 800_000, 900_000],
        [3_000_000, 2_400_000, 1_500_000, 1_200_000, 1_350_000],
        [4_000_000, 3_200_000, 20_000_000, 1_600_000, 1_800_000],
    ]
    mapping = {"0": "V", "1": "C", "2": "E", "3": "D", "4": "B"}

    result = fixed_audit(rows, mapping, ["A", "B", "C", "Broken"])

    assert len(result["relations"]) == 1
    assert result["relations"][0]["id"] == "earned-revenue"
    assert result["checkedRows"] == 4
    assert [row["rowLabel"] for row in result["failedRows"]] == ["Broken"]
    assert result["failedRows"][0]["variables"] == ["V", "C", "D", "E"]
    assert "culprit" not in result["failedRows"][0]
    assert result["limited"] is True


def test_fixed_mapping_audit_allows_small_ratio_rounding_residue():
    rows = [
        [1_000_000, 800_000, 500_008, 400_000],
        [2_000_000, 1_600_000, 1_000_014, 800_000],
        [3_000_000, 2_400_000, 1_500_019, 1_200_000],
    ]
    mapping = {"0": "V", "1": "C", "2": "E", "3": "D"}

    result = fixed_audit(rows, mapping)

    assert len(result["relations"]) == 1
    assert result["failedRows"] == []
    assert result["passed"] is True


def test_fixed_mapping_audit_does_not_claim_a_check_without_redundancy():
    rows = [
        [1_000_000, 800_000, 450_000],
        [2_000_000, 1_600_000, 900_000],
        [3_000_000, 2_400_000, 1_350_000],
    ]
    mapping = {"0": "V", "1": "C", "2": "B"}

    result = fixed_audit(rows, mapping)

    assert result["relations"] == []
    assert result["failedRows"] == []
    assert result["passed"] is False
