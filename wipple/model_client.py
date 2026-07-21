"""
Wipple model client.

Important routing rules:
- Auto keeps the proven Gemini-only path:
    primary   -> Gemini 3.1 Flash-Lite
    escalated -> Gemini 3.1 Pro Preview
    fallback  -> Gemini 3.1 Flash-Lite
- An explicit UI selection stays pinned for every call that shares the same
  Metrics object, including validator-triggered retries.
- Invalid explicit model IDs raise instead of silently becoming another model.
- Metrics report configured and provider-returned model IDs per call.
"""

from __future__ import annotations

import base64
import json
import os
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass(frozen=True)
class TierConfig:
    model_id: str
    provider: str               # "google" | "anthropic"
    input_per_m: float
    output_per_m: float
    display_name: str = ""
    thinking_level: Optional[str] = None
    adaptive_thinking: bool = False
    effort: Optional[str] = None
    thinking_budget_tokens: Optional[int] = None
    document_system_prompt: Optional[str] = None


HAIKU_DOCUMENT_SYSTEM_PROMPT = """You are a high-recall visual document
transcription engine. Before answering, inspect the entire attached page,
including small text and landscape-oriented content, and inventory every
region made of aligned rows and columns.

A contractor schedule may be titled Work in Progress, Contracts in Progress,
Contract Status, or may have no explicit title. Dense financial rows with
headings such as contract amount, estimated cost, cost to date, billings,
earned revenue, backlog, or gross profit count as a table even when borders
are faint or absent. Do not require the phrase \"WIP\" to recognize one.

When the requested JSON has a tables array, return tables: [] only after you
have inspected the whole page and found no tabular or column-aligned schedule
at all. Ambiguous headings are not a reason to omit a visible table: preserve
them verbatim and mention the ambiguity in notes."""


# Current selectable models. Pricing is standard synchronous API pricing
# per million tokens. Claude remains manually selectable but is not used by Auto.
MODEL_REGISTRY: dict[str, TierConfig] = {
    "gemini-3.1-flash-lite": TierConfig(
        "gemini-3.1-flash-lite", "google", 0.25, 1.50,
        "Gemini 3.1 Flash-Lite", thinking_level="minimal"),
    "gemini-3-flash-preview": TierConfig(
        "gemini-3-flash-preview", "google", 0.50, 3.00,
        "Gemini 3 Flash Preview", thinking_level="minimal"),
    "gemini-3.5-flash": TierConfig(
        "gemini-3.5-flash", "google", 1.50, 9.00,
        "Gemini 3.5 Flash", thinking_level="minimal"),
    "gemini-3.1-pro-preview": TierConfig(
        "gemini-3.1-pro-preview", "google", 2.00, 12.00,
        "Gemini 3.1 Pro Preview", thinking_level="low"),
    "claude-haiku-4-5": TierConfig(
        "claude-haiku-4-5", "anthropic", 1.00, 5.00,
        "Claude Haiku 4.5", thinking_budget_tokens=16_384,
        document_system_prompt=HAIKU_DOCUMENT_SYSTEM_PROMPT),
    "claude-haiku-4-5-20251001": TierConfig(
        "claude-haiku-4-5-20251001", "anthropic", 1.00, 5.00,
        "Claude Haiku 4.5", thinking_budget_tokens=16_384,
        document_system_prompt=HAIKU_DOCUMENT_SYSTEM_PROMPT),
    "claude-sonnet-4-6": TierConfig(
        "claude-sonnet-4-6", "anthropic", 3.00, 15.00,
        "Claude Sonnet 4.6", adaptive_thinking=True, effort="low"),
    "claude-sonnet-5": TierConfig(
        "claude-sonnet-5", "anthropic", 3.00, 15.00,
        "Claude Sonnet 5", effort="low"),
    "claude-opus-4-6": TierConfig(
        "claude-opus-4-6", "anthropic", 5.00, 25.00,
        "Claude Opus 4.6", adaptive_thinking=True, effort="low"),
    "claude-opus-4-8": TierConfig(
        "claude-opus-4-8", "anthropic", 5.00, 25.00,
        "Claude Opus 4.8", adaptive_thinking=True, effort="low"),
}

# Only genuinely retired IDs are redirected. Distinct working models are never
# silently aliased to Gemini 3.5 Flash or a newer Claude family.
MODEL_ALIASES: dict[str, str] = {
    "gemini-3.1-flash-lite-preview": "gemini-3.1-flash-lite",
}


def canonical_model_id(model_id: str) -> str:
    raw = (model_id or "").strip()
    canonical = MODEL_ALIASES.get(raw, raw)
    if canonical not in MODEL_REGISTRY:
        supported = ", ".join(sorted(MODEL_REGISTRY))
        raise ValueError(
            f"Unsupported model selection {raw!r}. Supported models: {supported}")
    return canonical


def model_config(model_id: str) -> TierConfig:
    return MODEL_REGISTRY[canonical_model_id(model_id)]


def _tier_from_env(env_name: str, default_model: str) -> TierConfig:
    # Environment overrides inherit the selected model's real provider/pricing.
    return model_config(os.environ.get(env_name, default_model))


# Preserve the fast, accurate architecture that existed before the selector
# cleanup. Claude is never entered automatically.
TIERS: dict[str, TierConfig] = {
    "primary": _tier_from_env(
        "WIPPLE_PRIMARY_MODEL", "gemini-3.1-flash-lite"),
    "escalated": _tier_from_env(
        "WIPPLE_ESCALATED_MODEL", "gemini-3.1-pro-preview"),
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
    thinking_level: Optional[str] = None

    @property
    def cost_usd(self) -> float:
        return (self.input_tokens * self.input_per_m
                + self.output_tokens * self.output_per_m) / 1e6


@dataclass
class Metrics:
    calls: list[CallRecord] = field(default_factory=list)

    # An explicit selection is pinned to this run. The same Metrics instance is
    # already passed through Wipple's model calls, so retries cannot jump tiers.
    pinned_model_id: Optional[str] = None
    originally_requested_model_id: Optional[str] = None

    def pin(self, model_id: str) -> str:
        canonical = canonical_model_id(model_id)
        if self.pinned_model_id and self.pinned_model_id != canonical:
            raise RuntimeError(
                "Conflicting explicit model selections in one Wipple run: "
                f"{self.pinned_model_id!r} and {canonical!r}")
        self.pinned_model_id = canonical
        if self.originally_requested_model_id is None:
            self.originally_requested_model_id = model_id
        return canonical

    def record(self, rec: CallRecord) -> None:
        self.calls.append(rec)

    def summary(self) -> dict:
        by_call = [
            {
                "purpose": c.purpose,
                "tier": c.tier,
                "provider": c.provider,
                # Backward-compatible field used by existing frontends.
                "model": c.model_id,
                "configured_model": c.model_id,
                "requested_model": c.requested_model_id,
                "response_model": c.response_model_id or c.model_id,
                "thinking_level": c.thinking_level,
                "in": c.input_tokens,
                "out": c.output_tokens,
                "cost_usd": round(c.cost_usd, 6),
                "seconds": round(c.seconds, 2),
            }
            for c in self.calls
        ]
        return {
            "selection_mode": "pinned" if self.pinned_model_id else "auto",
            "requested_model": self.originally_requested_model_id,
            "pinned_model": self.pinned_model_id,
            "api_calls": len(self.calls),
            "input_tokens": sum(c.input_tokens for c in self.calls),
            "output_tokens": sum(c.output_tokens for c in self.calls),
            "cost_usd": round(sum(c.cost_usd for c in self.calls), 6),
            "models_used": sorted({
                c.response_model_id or c.model_id for c in self.calls
            }),
            "by_call": by_call,
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
    """Lazy provider init; one generate() shared by extraction nodes."""

    def __init__(self) -> None:
        self._google = None
        self._anthropic = None
        self._google_lock = threading.Lock()
        self._anthropic_lock = threading.Lock()

    def _resolve_config(
        self,
        tier: str,
        model_override: Optional[str],
        metrics: Optional[Metrics],
    ) -> tuple[TierConfig, Optional[str]]:
        if tier not in TIERS:
            raise ValueError(
                f"Unknown model tier {tier!r}; expected one of {tuple(TIERS)}")

        requested = (model_override or "").strip() or None

        # The first explicit selection pins the entire run. A validator retry
        # that omits model_override therefore cannot jump to another provider.
        if requested:
            canonical = metrics.pin(requested) if metrics else canonical_model_id(requested)
            return MODEL_REGISTRY[canonical], requested

        if metrics and metrics.pinned_model_id:
            return MODEL_REGISTRY[metrics.pinned_model_id], (
                metrics.originally_requested_model_id or metrics.pinned_model_id)

        return TIERS[tier], None

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
        output_schema: Optional[dict[str, Any]] = None,
    ) -> str:
        cfg, requested_model = self._resolve_config(
            tier, model_override, metrics)

        t0 = time.time()
        if cfg.provider == "google":
            text, ti, to, response_model = self._call_google(
                cfg, prompt, pdf_bytes, media_type, json_only, max_tokens)
        elif cfg.provider == "anthropic":
            text, ti, to, response_model = self._call_anthropic(
                cfg, prompt, pdf_bytes, media_type, json_only, max_tokens,
                output_schema)
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
                thinking_level=cfg.thinking_level,
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

        kw: dict[str, Any] = {
            "max_output_tokens": min(max_tokens * 4, 65_536)
        }
        if json_only:
            kw["response_mime_type"] = "application/json"

        # Extraction is a literal transcription task, not a reasoning benchmark.
        # Constrain Gemini's dynamic thinking so 3.5 Flash does not unexpectedly
        # become much slower and more expensive than Flash-Lite.
        if cfg.thinking_level:
            try:
                kw["thinking_config"] = gt.ThinkingConfig(
                    thinking_level=cfg.thinking_level)
            except (AttributeError, TypeError):
                # Compatible with an older google-genai SDK; routing still works.
                pass

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

    def _call_anthropic(self, cfg, prompt, pdf_bytes, media_type, json_only,
                        max_tokens, output_schema):
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
        request: dict[str, Any] = {
            "model": cfg.model_id,
            # Match Gemini's existing effective ceiling without changing the
            # known-good Gemini request path.
            "max_tokens": min(max_tokens * 4, 65_536),
            "messages": [{"role": "user", "content": content}],
        }
        if pdf_bytes and cfg.document_system_prompt:
            request["system"] = cfg.document_system_prompt
        if json_only:
            if output_schema is None:
                raise ValueError(
                    "Claude JSON requests require an explicit output_schema")
            request["output_config"] = {"format": {
                "type": "json_schema", "schema": output_schema}}
        if cfg.effort:
            request.setdefault("output_config", {})["effort"] = cfg.effort
        if cfg.adaptive_thinking:
            request["thinking"] = {"type": "adaptive"}
        elif cfg.thinking_budget_tokens:
            request["thinking"] = {
                "type": "enabled",
                "budget_tokens": cfg.thinking_budget_tokens,
            }

        resp = client.messages.create(**request)
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
