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
