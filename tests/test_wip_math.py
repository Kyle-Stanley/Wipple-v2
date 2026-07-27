import json
import subprocess
from pathlib import Path


MODULE = Path(__file__).parents[1] / "static" / "wip_math.js"


def derive(values):
    script = """
const fs = require("fs");
const source = fs.readFileSync(process.argv[1], "utf8");
const loaded = {exports: {}};
new Function("module", "exports", source)(loaded, loaded.exports);
process.stdout.write(JSON.stringify(
  loaded.exports.deriveCanonicalVars(JSON.parse(process.argv[2]))
));
"""
    result = subprocess.run(
        ["node", "-e", script, str(MODULE), json.dumps(values)],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def readiness(variables):
    script = """
const fs = require("fs");
const source = fs.readFileSync(process.argv[1], "utf8");
const loaded = {exports: {}};
new Function("module", "exports", source)(loaded, loaded.exports);
process.stdout.write(JSON.stringify(
  loaded.exports.mappingReadiness(JSON.parse(process.argv[2]))
));
"""
    result = subprocess.run(
        ["node", "-e", script, str(MODULE), json.dumps(variables)],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def corroboration(rows, mapping, ignored=None):
    script = """
const fs = require("fs");
const source = fs.readFileSync(process.argv[1], "utf8");
const loaded = {exports: {}};
new Function("module", "exports", source)(loaded, loaded.exports);
process.stdout.write(JSON.stringify(
  loaded.exports.inferCorroboratingColumns(
    JSON.parse(process.argv[2]),
    JSON.parse(process.argv[3]),
    JSON.parse(process.argv[4])
  )
));
"""
    result = subprocess.run(
        [
            "node", "-e", script, str(MODULE), json.dumps(rows),
            json.dumps(mapping), json.dumps(ignored or []),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


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
