"""The page vision call is a table reader, not an accounting agent."""

from wipple.extraction import CHUNK_OUTPUT_SCHEMA, CHUNK_PROMPT


def test_chunk_prompt_is_schema_blind():
    lower = CHUNK_PROMPT.lower()
    assert "work-in-progress" not in lower
    assert "completed contract" not in lower
    assert "classify" in lower and "do not" in lower
    assert "table detector and table reader" in lower


def test_chunk_prompt_does_not_request_derived_metadata():
    lower = CHUNK_PROMPT.lower()
    assert "row_count" not in lower
    assert "column_count" not in lower
    assert "bounding box" not in lower
    assert "position" not in lower
    assert "reporting_period_text" not in lower


def test_chunk_schema_contains_only_grids():
    assert set(CHUNK_OUTPUT_SCHEMA["properties"]) == {"tables"}
    table = CHUNK_OUTPUT_SCHEMA["properties"]["tables"]["items"]
    assert set(table["properties"]) == {"headers", "rows"}
    assert set(table["required"]) == {"headers", "rows"}
