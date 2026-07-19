from pathlib import Path

from wipple.docgraph import run_document
from wipple.periods import extract_period_end


def test_period_end_is_exact_or_missing():
    assert extract_period_end(
        ["Contracts in progress for the year ended December 31, 2025"]
    ) == {"reporting_date": "2025-12-31",
          "reporting_date_error": None}
    assert extract_period_end(["FYE 2024"])["reporting_date"] == "2024-12-31"
    assert extract_period_end([], "contractor_2025_wip.pdf") == {
        "reporting_date": None,
        "reporting_date_error": "reporting_date_not_found",
    }


def test_conflicting_periods_fail_instead_of_guessing():
    out = extract_period_end([
        "Year ended December 31, 2024",
        "Year ended December 31, 2025",
    ])
    assert out["reporting_date"] is None
    assert out["reporting_date_error"] == "multiple_reporting_dates"


def test_spreadsheet_document_preserves_job_id_and_name():
    path = Path(__file__).parent / "fixtures" / "wip_2025.csv"
    report, metrics = run_document(path.read_bytes(), path.name)
    section = report["tables"][0]["sections"][0]
    table = section["report"]["table"]
    assert report["document"]["reporting_date"] == "2025-12-31"
    assert report["document"]["reporting_date_error"] is None
    assert section["type"] == "wip"
    assert table["job_ids"][:2] == ["24-001", "24-002"]
    assert table["job_names"][:2] == ["North Fork Bridge", "Cedar Library"]
    assert metrics["api_calls"] == 0


def test_same_period_completed_contracts_remain_a_separate_schedule():
    path = Path(__file__).parent / "fixtures" / "cc_2025.csv"
    report, _ = run_document(path.read_bytes(), path.name)
    section = report["tables"][0]["sections"][0]
    assert report["document"]["reporting_date"] == "2025-12-31"
    assert report["document"]["schedule_types"] == ["cc"]
    assert section["type"] == "cc"
    assert section["report"]["table"]["job_ids"][:2] == ["24-004", "24-007"]
