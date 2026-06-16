"""
Slim model client: two providers, two tiers, one method.

Tiering is the whole point -- the validator is the escalation trigger, so
the client just needs "primary" (cheap) and "escalated" (strong) and a way
to record what each call cost.
"""

from __future__ import annotations

import base64
import json
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class TierConfig:
    model_id: str
    provider: str               # "google" | "anthropic"
    input_per_m: float
    output_per_m: float


# Pinnable models for the UI selector. Pricing per million tokens.
MODEL_REGISTRY: dict[str, TierConfig] = {
    "gemini-3.1-flash-lite": TierConfig(
        "gemini-3.1-flash-lite", "google", 0.25, 1.50
    ),
    "gemini-3-flash-preview": TierConfig(
        "gemini-3-flash-preview", "google", 0.50, 3.00
    ),
    "gemini-3.5-flash": TierConfig(
        "gemini-3.5-flash", "google", 1.50, 9.00
    ),
    "gemini-3.1-pro-preview": TierConfig(
        "gemini-3.1-pro-preview", "google", 2.00, 12.00
    ),
    "claude-haiku-4-5-20251001": TierConfig(
        "claude-haiku-4-5-20251001", "anthropic", 1.00, 5.00
    ),
    "claude-sonnet-4-6": TierConfig(
        "claude-sonnet-4-6", "anthropic", 3.00, 15.00
    ),
    "claude-opus-4-8": TierConfig(
        "claude-opus-4-8", "anthropic", 5.00, 25.00
    ),
}


def _model_config(model_id: str, source: str) -> TierConfig:
    try:
        return MODEL_REGISTRY[model_id]
    except KeyError as e:
        known = ", ".join(sorted(MODEL_REGISTRY))
        raise RuntimeError(
            f"{source}={model_id!r} is not in MODEL_REGISTRY. "
            f"Known models: {known}"
        ) from e


def _tier_from_env(env_name: str, default_model_id: str) -> TierConfig:
    return _model_config(os.environ.get(env_name, default_model_id), env_name)


TIERS: dict[str, TierConfig] = {
    "primary": _tier_from_env(
        "WIPPLE_PRIMARY_MODEL",
        "gemini-3.1-flash-lite",
    ),
    "escalated": _tier_from_env(
        "WIPPLE_ESCALATED_MODEL",
        "claude-sonnet-4-6",
    ),
    # Small, cheap text-only tier for the header fallback / disambiguator.
    "fallback": _tier_from_env(
        "WIPPLE_FALLBACK_MODEL",
        "gemini-3.1-flash-lite",
    ),
}


@dataclass
class CallRecord:
    tier: str
    model_id: str
    input_tokens: int
    output_tokens: int
    seconds: float
    purpose: str
    input_per_m: float = 0.0
    output_per_m: float = 0.0

    @property
    def cost_usd(self) -> float:
        return (self.input_tokens * self.input_per_m
                + self.output_tokens * self.output_per_m) / 1e6


@dataclass
class Metrics:
    calls: list[CallRecord] = field(default_factory=list)

    def record(self, rec: CallRecord) -> None:
        self.calls.append(rec)

    def summary(self) -> dict:
        return {
            "api_calls": len(self.calls),
            "input_tokens": sum(c.input_tokens for c in self.calls),
            "output_tokens": sum(c.output_tokens for c in self.calls),
            "cost_usd": round(sum(c.cost_usd for c in self.calls), 6),
            "by_call": [
                {"purpose": c.purpose, "tier": c.tier, "model": c.model_id,
                 "in": c.input_tokens, "out": c.output_tokens,
                 "cost_usd": round(c.cost_usd, 6), "seconds": round(c.seconds, 2)}
                for c in self.calls
            ],
        }


def extract_json(text: str) -> Any:
    """Robust JSON extraction: decoder-scan past fences and commentary."""
    s = (text or "").strip()
    s = re.sub(r"^```(?:json)?\s*", "", s)
    s = re.sub(r"\s*```$", "", s)
    dec = json.JSONDecoder()
    for i, ch in enumerate(s):
        if ch in "{[":
            try:
                obj, _ = dec.raw_decode(s[i:])
                return obj
            except json.JSONDecodeError:
                continue
    raise ValueError(f"no JSON found in model output: {s[:300]!r}")


class ModelClient:
    """Lazy provider init; one generate() that both nodes share."""

    def __init__(self) -> None:
        self._google = None
        self._anthropic = None

    def generate(
        self,
        prompt: str,
        tier: str = "primary",
        pdf_bytes: Optional[bytes] = None,
        media_type: str = "application/pdf",
        json_only: bool = True,
        max_tokens: int = 16384,
        metrics: Optional[Metrics] = None,
        purpose: str = "",
        model_override: Optional[str] = None,
    ) -> str:
        if model_override:
            cfg = _model_config(model_override, "model_override")
        else:
            try:
                cfg = TIERS[tier]
            except KeyError as e:
                known = ", ".join(sorted(TIERS))
                raise RuntimeError(
                    f"unknown tier {tier!r}. Known tiers: {known}"
                ) from e

        t0 = time.time()
        if cfg.provider == "google":
            text, ti, to = self._call_google(cfg, prompt, pdf_bytes,
                                             media_type, json_only, max_tokens)
        else:
            text, ti, to = self._call_anthropic(cfg, prompt, pdf_bytes,
                                                media_type, max_tokens)
        if metrics is not None:
            metrics.record(CallRecord(tier, cfg.model_id, ti, to,
                                      time.time() - t0, purpose,
                                      cfg.input_per_m, cfg.output_per_m))
        return text

    # -- providers -----------------------------------------------------------

    def _call_google(self, cfg, prompt, pdf_bytes, media_type, json_only,
                     max_tokens):
        if self._google is None:
            from google import genai
            key = os.environ.get("GOOGLE_API_KEY", "").strip()
            if not key:
                raise RuntimeError("GOOGLE_API_KEY not set")
            try:
                from google.genai import types as gt0
                self._google = genai.Client(
                    api_key=key,
                    http_options=gt0.HttpOptions(timeout=120_000))
            except Exception:
                self._google = genai.Client(api_key=key)
        from google.genai import types as gt
        contents: list[Any] = []
        if pdf_bytes:
            contents.append(gt.Part.from_bytes(data=pdf_bytes,
                                               mime_type=media_type))
        contents.append(prompt)
        kw: dict[str, Any] = {"max_output_tokens": max_tokens * 4}
        if json_only:
            kw["response_mime_type"] = "application/json"
        resp = self._google.models.generate_content(
            model=cfg.model_id, contents=contents,
            config=gt.GenerateContentConfig(**kw))
        um = getattr(resp, "usage_metadata", None)
        ti = int(getattr(um, "prompt_token_count", 0) or 0)
        to = int(getattr(um, "candidates_token_count", 0) or 0)
        try:
            text = resp.text or ""
        except ValueError:
            parts = resp.candidates[0].content.parts if resp.candidates else []
            text = "".join(getattr(p, "text", "") for p in parts)
        return text, ti, to

    def _call_anthropic(self, cfg, prompt, pdf_bytes, media_type, max_tokens):
        if self._anthropic is None:
            import anthropic
            key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
            if not key:
                raise RuntimeError("ANTHROPIC_API_KEY not set")
            self._anthropic = anthropic.Anthropic(api_key=key, timeout=120.0)
        content: list[dict] = []
        if pdf_bytes:
            kind = "image" if media_type.startswith("image/") else "document"
            content.append({"type": kind, "source": {
                "type": "base64", "media_type": media_type,
                "data": base64.standard_b64encode(pdf_bytes).decode()}})
        content.append({"type": "text", "text": prompt})
        resp = self._anthropic.messages.create(
            model=cfg.model_id, max_tokens=max_tokens,
            messages=[{"role": "user", "content": content}])
        text = "".join(getattr(b, "text", "") for b in resp.content)
        return text, int(resp.usage.input_tokens), int(resp.usage.output_tokens)


_client: Optional[ModelClient] = None


def get_client() -> ModelClient:
    global _client
    if _client is None:
        _client = ModelClient()
    return _client
