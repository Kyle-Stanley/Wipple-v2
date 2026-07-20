"""
Slim model client: two providers, three tiers, one method.

The deterministic validator remains the escalation trigger:
- primary: fast, capable extraction
- escalated: stronger second pass
- fallback: cheap text-only disambiguation

Explicit UI selections are strict. An unsupported model raises an error instead
of silently falling back to Gemini.
"""

from __future__ import annotations

import base64
import json
import os
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Optional


@dataclass(frozen=True)
class TierConfig:
    model_id: str
    provider: str               # "google" | "anthropic"
    input_per_m: float
    output_per_m: float
    display_name: str = ""


def _sonnet_5_prices() -> tuple[float, float]:
    """Anthropic launch pricing runs through 2026-08-31."""
    if date.today() <= date(2026, 8, 31):
        return 2.0, 10.0
    return 3.0, 15.0


_sonnet5_input, _sonnet5_output = _sonnet_5_prices()


# Current production-facing models for document extraction.
# Pricing is standard synchronous API pricing per million tokens.
MODEL_REGISTRY: dict[str, TierConfig] = {
    "gemini-3.1-flash-lite": TierConfig(
        "gemini-3.1-flash-lite", "google", 0.25, 1.50,
        "Gemini 3.1 Flash-Lite"),
    "gemini-3.5-flash": TierConfig(
        "gemini-3.5-flash", "google", 1.50, 9.00,
        "Gemini 3.5 Flash"),
    "gemini-3.1-pro-preview": TierConfig(
        "gemini-3.1-pro-preview", "google", 2.00, 12.00,
        "Gemini 3.1 Pro Preview"),
    "claude-haiku-4-5": TierConfig(
        "claude-haiku-4-5", "anthropic", 1.00, 5.00,
        "Claude Haiku 4.5"),
    "claude-sonnet-5": TierConfig(
        "claude-sonnet-5", "anthropic",
        _sonnet5_input, _sonnet5_output,
        "Claude Sonnet 5"),
    "claude-opus-4-8": TierConfig(
        "claude-opus-4-8", "anthropic", 5.00, 25.00,
        "Claude Opus 4.8"),
}


# Gracefully migrate old config values to current canonical model IDs.
# These aliases are accepted, but metrics always report the actual canonical ID.
MODEL_ALIASES: dict[str, str] = {
    "gemini-3.1-flash-lite-preview": "gemini-3.1-flash-lite",
    "gemini-3-flash-preview": "gemini-3.5-flash",
    "claude-haiku-4-5-20251001": "claude-haiku-4-5",
    "claude-sonnet-4-6": "claude-sonnet-5",
    "claude-opus-4-6": "claude-opus-4-8",
}


def canonical_model_id(model_id: str) -> str:
    raw = (model_id or "").strip()
    canonical = MODEL_ALIASES.get(raw, raw)
    if canonical not in MODEL_REGISTRY:
        supported = ", ".join(MODEL_REGISTRY)
        raise ValueError(
            f"Unsupported model selection {raw!r}. Supported models: {supported}")
    return canonical


def model_config(model_id: str) -> TierConfig:
    return MODEL_REGISTRY[canonical_model_id(model_id)]


def _tier_from_env(env_name: str, default_model: str) -> TierConfig:
    configured = os.environ.get(env_name, default_model)
    return model_config(configured)


# Auto mode now matches the UI:
# Gemini 3.5 Flash first, Claude Sonnet 5 when validation triggers escalation.
TIERS: dict[str, TierConfig] = {
    "primary": _tier_from_env(
        "WIPPLE_PRIMARY_MODEL", "gemini-3.5-flash"),
    "escalated": _tier_from_env(
        "WIPPLE_ESCALATED_MODEL", "claude-sonnet-5"),
    # Small, cheap text-only tier for header fallback / disambiguation.
    "fallback": _tier_from_env(
        "WIPPLE_FALLBACK_MODEL", "gemini-3.1-flash-lite"),
}


@dataclass
class CallRecord:
    tier: str
    model_id: str
    provider: str
    input_tokens: int
    output_tokens: int
    seconds: float
    purpose: str
    input_per_m: float = 0.0
    output_per_m: float = 0.0
    requested_model_id: Optional[str] = None
    response_model_id: Optional[str] = None

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
            "models_used": sorted({
                c.response_model_id or c.model_id for c in self.calls
            }),
            "by_call": [
                {
                    "purpose": c.purpose,
                    "tier": c.tier,
                    "provider": c.provider,
                    # Keep "model" for backward-compatible frontend display.
                    "model": c.model_id,
                    "requested_model": c.requested_model_id,
                    "response_model": c.response_model_id or c.model_id,
                    "in": c.input_tokens,
                    "out": c.output_tokens,
                    "cost_usd": round(c.cost_usd, 6),
                    "seconds": round(c.seconds, 2),
                }
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
        self._google_lock = threading.Lock()
        self._anthropic_lock = threading.Lock()

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
        if tier not in TIERS:
            raise ValueError(
                f"Unknown model tier {tier!r}; expected one of {tuple(TIERS)}")

        requested_model = (model_override or "").strip() or None
        if requested_model:
            cfg = model_config(requested_model)
        else:
            cfg = TIERS[tier]

        t0 = time.time()
        if cfg.provider == "google":
            text, ti, to, response_model = self._call_google(
                cfg, prompt, pdf_bytes, media_type, json_only, max_tokens)
        elif cfg.provider == "anthropic":
            text, ti, to, response_model = self._call_anthropic(
                cfg, prompt, pdf_bytes, media_type, max_tokens)
        else:
            raise RuntimeError(f"Unknown provider {cfg.provider!r}")

        if metrics is not None:
            metrics.record(CallRecord(
                tier=tier,
                model_id=cfg.model_id,
                provider=cfg.provider,
                input_tokens=ti,
                output_tokens=to,
                seconds=time.time() - t0,
                purpose=purpose,
                input_per_m=cfg.input_per_m,
                output_per_m=cfg.output_per_m,
                requested_model_id=requested_model,
                response_model_id=response_model,
            ))
        return text

    # -- providers -----------------------------------------------------------

    def _get_google_client(self):
        if self._google is None:
            with self._google_lock:
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
        return self._google

    def _call_google(self, cfg, prompt, pdf_bytes, media_type, json_only,
                     max_tokens):
        client = self._get_google_client()
        from google.genai import types as gt

        contents: list[Any] = []
        if pdf_bytes:
            contents.append(gt.Part.from_bytes(
                data=pdf_bytes, mime_type=media_type))
        contents.append(prompt)

        # Gemini 3.5 Flash supports up to 65,536 output tokens.
        kw: dict[str, Any] = {
            "max_output_tokens": min(max_tokens * 4, 65_536)
        }
        if json_only:
            kw["response_mime_type"] = "application/json"

        resp = client.models.generate_content(
            model=cfg.model_id,
            contents=contents,
            config=gt.GenerateContentConfig(**kw),
        )
        um = getattr(resp, "usage_metadata", None)
        ti = int(getattr(um, "prompt_token_count", 0) or 0)
        to = int(getattr(um, "candidates_token_count", 0) or 0)
        response_model = (
            getattr(resp, "model_version", None)
            or getattr(resp, "model", None)
            or cfg.model_id
        )

        try:
            text = resp.text or ""
        except ValueError:
            parts = (
                resp.candidates[0].content.parts
                if getattr(resp, "candidates", None) else []
            )
            text = "".join(getattr(p, "text", "") for p in parts)
        return text, ti, to, str(response_model)

    def _get_anthropic_client(self):
        if self._anthropic is None:
            with self._anthropic_lock:
                if self._anthropic is None:
                    import anthropic
                    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
                    if not key:
                        raise RuntimeError("ANTHROPIC_API_KEY not set")
                    self._anthropic = anthropic.Anthropic(
                        api_key=key, timeout=120.0)
        return self._anthropic

    def _call_anthropic(self, cfg, prompt, pdf_bytes, media_type, max_tokens):
        client = self._get_anthropic_client()
        content: list[dict] = []

        if pdf_bytes:
            kind = "image" if media_type.startswith("image/") else "document"
            content.append({
                "type": kind,
                "source": {
                    "type": "base64",
                    "media_type": media_type,
                    "data": base64.standard_b64encode(pdf_bytes).decode(),
                },
            })
        content.append({"type": "text", "text": prompt})

        resp = client.messages.create(
            model=cfg.model_id,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": content}],
        )
        text = "".join(getattr(block, "text", "") for block in resp.content)
        response_model = getattr(resp, "model", None) or cfg.model_id
        return (
            text,
            int(resp.usage.input_tokens),
            int(resp.usage.output_tokens),
            str(response_model),
        )


_client: Optional[ModelClient] = None
_client_lock = threading.Lock()


def get_client() -> ModelClient:
    global _client
    if _client is None:
        with _client_lock:
            if _client is None:
                _client = ModelClient()
    return _client
