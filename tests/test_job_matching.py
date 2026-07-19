import json
import subprocess
from pathlib import Path


MODULE = Path(__file__).parents[1] / "static" / "job_matching.js"


def run_matching_case(left, right):
    script = """
const fs = require("fs");
const source = fs.readFileSync(process.argv[1], "utf8");
const loaded = {exports: {}};
new Function("module", "exports", source)(loaded, loaded.exports);
const matching = loaded.exports;
const [left, right] = JSON.parse(process.argv[2]);
process.stdout.write(JSON.stringify({
  score: matching.identityScore(left, right),
  plausible: matching.isPlausibleIdentityMatch(left, right)
}));
"""
    result = subprocess.run(
        ["node", "-e", script, str(MODULE), json.dumps([left, right])],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def test_same_job_with_changed_name_and_id_is_offered_for_review():
    result = run_matching_case(
        {"jobId": "24-003", "jobName": "Willow Transit Center"},
        {"jobId": "25-088", "jobName": "Willow Transit Center - Phase II"},
    )

    assert result["plausible"] is True
    assert result["score"] >= 0.60
    assert result["score"] < 0.90  # review it; do not silently auto-link it


def test_unrelated_jobs_are_not_offered_as_candidates():
    result = run_matching_case(
        {"jobId": "23-011", "jobName": "Maple Street Fire Station"},
        {"jobId": "24-006", "jobName": "Granite Substation"},
    )

    assert result["plausible"] is False


def test_same_job_id_survives_a_loose_name():
    result = run_matching_case(
        {"jobId": "24-128", "jobName": "Riverfront Hospital Addition"},
        {"jobId": "24 128", "jobName": "Riverfront Hosp. Addn"},
    )

    assert result["plausible"] is True
    assert result["score"] == 1


def test_similar_looking_ids_without_names_are_different_jobs():
    result = run_matching_case(
        {"jobId": "Job 123", "jobName": "", "label": "Job 123"},
        {"jobId": "Job 124", "jobName": "", "label": "Job 124"},
    )

    assert result["plausible"] is False
    assert result["score"] == 0
