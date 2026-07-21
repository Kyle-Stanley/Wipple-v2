"""Regression tests for UI model pinning and provider output contracts."""

from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

import wipple.extraction as extraction
from wipple.model_client import MODEL_REGISTRY, TIERS, ModelClient


def test_ui_model_values_are_registered():
    html = Path("static/index.html").read_text(encoding="utf-8")
    values = set(re.findall(r'<option value="([^"]+)">', html))
    assert values <= set(MODEL_REGISTRY)
    assert {"gemini-3.1-flash-lite", "gemini-3.5-flash-lite",
            "gemini-3.6-flash", "claude-sonnet-5", "claude-opus-4-8",
            "gpt-5.6-luna", "gpt-5.6-terra", "gpt-5.6-sol"} <= values
    assert "gemini-3.1-flash-lite-preview" not in MODEL_REGISTRY


def test_auto_defaults_remain_on_the_proven_gemini_path():
    assert TIERS["primary"].model_id == "gemini-3.1-flash-lite"
    assert TIERS["escalated"].model_id == "gemini-3.1-pro-preview"
    assert TIERS["fallback"].model_id == "gemini-3.1-flash-lite"


def test_chunk_extraction_forwards_pinned_model_and_schema(monkeypatch):
    class FakeClient:
        def __init__(self):
            self.kwargs = None

        def generate(self, *_args, **kwargs):
            self.kwargs = kwargs
            return json.dumps({"reporting_period_text": None, "tables": []})

    fake = FakeClient()
    monkeypatch.setattr(extraction, "get_client", lambda: fake)
    extraction.extract_chunks_node({
        "chunks": [{"chunk_id": 0, "bytes": b"pdf", "pages": [1],
                    "media_type": "application/pdf"}],
        "fragments": [],
        "model_override": "claude-sonnet-4-6",
        "extraction_tier": "primary",
        "extraction_attempts": [],
        "_metrics": None,
    })

    assert fake.kwargs["model_override"] == "claude-sonnet-4-6"
    assert fake.kwargs["output_schema"] == extraction.CHUNK_OUTPUT_SCHEMA


def test_chunk_prompt_is_strictly_one_page():
    assert "continue the same rows array across pages" not in extraction.CHUNK_PROMPT
    assert "never infer, repeat, or carry over rows" in extraction.CHUNK_PROMPT


def test_claude_json_request_uses_structured_output_config():
    captured = {}

    def create(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            content=[SimpleNamespace(text='{"ok":true}')],
            usage=SimpleNamespace(input_tokens=7, output_tokens=3),
        )

    client = ModelClient()
    client._anthropic = SimpleNamespace(
        messages=SimpleNamespace(create=create))
    schema = {
        "type": "object",
        "properties": {"ok": {"type": "boolean"}},
        "required": ["ok"],
        "additionalProperties": False,
    }

    result = client.generate(
        "return JSON", model_override="claude-sonnet-4-6",
        pdf_bytes=b"%PDF-fake", json_only=True, output_schema=schema)

    assert result == '{"ok":true}'
    assert captured["model"] == "claude-sonnet-4-6"
    assert captured["max_tokens"] == 65_536
    assert captured["output_config"] == {
        "format": {"type": "json_schema", "schema": schema},
        "effort": "low",
    }
    assert captured["thinking"] == {"type": "adaptive"}
    assert "system" not in captured


def test_gemini_request_path_remains_unchanged():
    captured = {}

    def generate_content(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            text='{"ok":true}', usage_metadata=None,
            model_version="gemini-3.1-flash-lite")

    client = ModelClient()
    client._google = SimpleNamespace(
        models=SimpleNamespace(generate_content=generate_content))
    schema = {
        "type": "object",
        "properties": {"ok": {"type": "boolean"}},
        "required": ["ok"],
        "additionalProperties": False,
    }

    client.generate("return JSON", model_override="gemini-3.1-flash-lite",
                    json_only=True, output_schema=schema)

    assert captured["config"].max_output_tokens == 65_536
    assert captured["config"].response_json_schema is None


def test_claude_json_request_requires_a_schema():
    client = ModelClient()
    client._anthropic = SimpleNamespace(messages=SimpleNamespace())

    with pytest.raises(ValueError, match="explicit output_schema"):
        client.generate("return JSON", model_override="claude-sonnet-4-6",
                        json_only=True)


def test_haiku_gets_bounded_manual_thinking():
    captured = {}

    def create(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            content=[SimpleNamespace(text='{"ok":true}')],
            usage=SimpleNamespace(input_tokens=7, output_tokens=3),
        )

    client = ModelClient()
    client._anthropic = SimpleNamespace(
        messages=SimpleNamespace(create=create))
    schema = {
        "type": "object",
        "properties": {"ok": {"type": "boolean"}},
        "required": ["ok"],
        "additionalProperties": False,
    }

    client.generate("return JSON", model_override="claude-haiku-4-5",
                    pdf_bytes=b"%PDF-fake", json_only=True,
                    output_schema=schema)

    assert captured["thinking"] == {
        "type": "enabled", "budget_tokens": 16_384}
    assert captured["max_tokens"] == 65_536
    assert "inspect the entire attached page" in captured["system"]
    assert "Do not require the phrase \"WIP\"" in captured["system"]


def test_openai_pdf_request_uses_responses_api_and_structured_output():
    captured = {}

    def create(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            output_text='{"ok":true}',
            model="gpt-5.6-terra",
            usage=SimpleNamespace(input_tokens=11, output_tokens=4),
        )

    client = ModelClient()
    client._openai = SimpleNamespace(
        responses=SimpleNamespace(create=create))
    schema = {
        "type": "object",
        "properties": {"ok": {"type": "boolean"}},
        "required": ["ok"],
        "additionalProperties": False,
    }

    result = client.generate(
        "return JSON", model_override="gpt-5.6-terra",
        pdf_bytes=b"%PDF-fake", json_only=True, output_schema=schema)

    assert result == '{"ok":true}'
    assert captured["model"] == "gpt-5.6-terra"
    assert captured["max_output_tokens"] == 65_536
    assert captured["reasoning"] == {"effort": "medium"}
    file_part = captured["input"][0]["content"][0]
    assert file_part["type"] == "input_file"
    assert file_part["filename"] == "document.pdf"
    assert file_part["file_data"].startswith(
        "data:application/pdf;base64,")
    assert captured["text"] == {"format": {
        "type": "json_schema",
        "name": "wipple_extraction",
        "schema": schema,
        "strict": True,
    }}


def test_openai_image_request_preserves_original_detail():
    captured = {}

    def create(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(output_text="done", usage=None)

    client = ModelClient()
    client._openai = SimpleNamespace(
        responses=SimpleNamespace(create=create))

    client.generate(
        "read this", model_override="gpt-5.6-luna",
        pdf_bytes=b"image", media_type="image/png", json_only=False)

    image_part = captured["input"][0]["content"][0]
    assert image_part["type"] == "input_image"
    assert image_part["image_url"].startswith("data:image/png;base64,")
    assert image_part["detail"] == "original"


def test_openai_json_request_requires_a_schema():
    client = ModelClient()
    client._openai = SimpleNamespace(responses=SimpleNamespace())

    with pytest.raises(ValueError, match="explicit output_schema"):
        client.generate("return JSON", model_override="gpt-5.6-sol",
                        json_only=True)
