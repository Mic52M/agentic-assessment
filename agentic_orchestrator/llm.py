"""Anthropic LLM access for the agentic nodes.

Single seam through which every node talks to Claude. Uses the Messages API
with structured outputs (`messages.parse`) so each node returns a validated
Pydantic object rather than free-form text — keeping the workflow analyzable.

The module degrades gracefully: if the SDK is missing or no API key is set,
`is_available()` returns False and nodes fall back to their deterministic
stand-ins. This keeps the prototype runnable offline and keeps failure-mode
experiments reproducible.
"""

from __future__ import annotations

import os
import time
from functools import lru_cache
from typing import Any, TypeVar

from pydantic import BaseModel

# Default model. Overridable via LLM_MODEL env var so the model itself can be
# treated as an experimental condition in the assessment layer.
DEFAULT_MODEL = "claude-opus-4-7"

# USD per million tokens, per model. Tariffs are an explicit, versioned table
# rather than a live lookup — keeps cost numbers reproducible across runs.
# (input, output, cache_read). Cache writes are billed at input rate.
_PRICING_PER_MTOK: dict[str, tuple[float, float, float]] = {
    "claude-opus-4-7": (15.0, 75.0, 1.5),
    "claude-opus-4-8": (15.0, 75.0, 1.5),
    "claude-sonnet-4-6": (3.0, 15.0, 0.3),
    "claude-haiku-4-5-20251001": (1.0, 5.0, 0.1),
}


def current_model() -> str:
    return os.getenv("LLM_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL


def estimate_cost_usd(
    model: str, input_tokens: int, output_tokens: int, cache_read_tokens: int = 0
) -> float:
    rates = _PRICING_PER_MTOK.get(model)
    if rates is None:
        return 0.0
    in_rate, out_rate, cache_rate = rates
    billable_input = max(0, input_tokens - cache_read_tokens)
    cost = (
        billable_input * in_rate
        + output_tokens * out_rate
        + cache_read_tokens * cache_rate
    ) / 1_000_000
    return round(cost, 6)

T = TypeVar("T", bound=BaseModel)


class LLMUnavailable(RuntimeError):
    """Raised when the Anthropic SDK or API key is not available."""


@lru_cache(maxsize=1)
def _client() -> Any:
    try:
        import anthropic
    except ImportError as exc:  # pragma: no cover - import guard
        raise LLMUnavailable("anthropic SDK not installed") from exc
    if not os.getenv("ANTHROPIC_API_KEY"):
        raise LLMUnavailable("ANTHROPIC_API_KEY not set")
    return anthropic.Anthropic()


def _enabled() -> bool:
    # LLM_ENABLED defaults to true; set it false to force the offline fallback.
    return os.getenv("LLM_ENABLED", "true").strip().lower() not in {
        "0", "false", "no", "off"
    }


def is_available() -> bool:
    if not _enabled():
        return False
    try:
        _client()
        return True
    except LLMUnavailable:
        return False


def structured(system: str, user: str, schema: type[T]) -> tuple[T, dict[str, Any]]:
    """Run one structured-output call. Returns (parsed_object, usage_record).

    The system prompt carries a cache breakpoint so repeated runs with the same
    role reuse the prefix; volatile per-ticket text lives in the user message.
    """
    client = _client()
    model = current_model()
    t0 = time.perf_counter()
    response = client.messages.parse(
        model=model,
        max_tokens=2048,
        system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": user}],
        output_format=schema,
    )
    usage = {
        "model": model,
        "input_tokens": response.usage.input_tokens,
        "output_tokens": response.usage.output_tokens,
        "cache_read_input_tokens": getattr(
            response.usage, "cache_read_input_tokens", 0
        ),
        "duration_ms": round((time.perf_counter() - t0) * 1000, 3),
    }
    return response.parsed_output, usage


def as_llm_call(node: str, usage: dict[str, Any]) -> dict[str, Any]:
    """Shape a `usage` record into an LlmCall entry for the trace."""
    return {
        "node": node,
        "model": usage["model"],
        "input_tokens": usage["input_tokens"],
        "output_tokens": usage["output_tokens"],
        "cache_read_input_tokens": usage["cache_read_input_tokens"],
        "duration_ms": usage["duration_ms"],
        "usd_cost": estimate_cost_usd(
            usage["model"],
            usage["input_tokens"],
            usage["output_tokens"],
            usage["cache_read_input_tokens"],
        ),
    }
