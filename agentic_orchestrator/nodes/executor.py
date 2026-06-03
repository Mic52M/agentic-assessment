"""Executor node: classify the (already sanitized) ticket.

The classification is the core LLM step. It runs on `sanitized_text` produced
by the planner, so the model never sees raw PII. Falls back to the local
rule-based classifier when no LLM is available. The call is recorded as a
ToolCall (with timing and success/failure) and, when LLM-backed, an LlmCall
(with token usage) — the evidence for availability/cost analysis.
"""

from __future__ import annotations

import random
import time
from typing import Any

from pydantic import BaseModel, Field

from .. import llm
from ..state import Classification, OrchestratorState, ToolCall
from ..tools.classifier import classify as rule_classify
from ..tools.failure import FaultModel, maybe_crash, maybe_delay, should_corrupt

_CATEGORIES = ["billing", "technical", "account", "abuse", "other"]
_PRIORITIES = ["low", "medium", "high"]

_SYSTEM = f"""You triage support tickets. Classify the ticket into exactly one
category and one priority, and give a one-sentence justification.

Categories: {", ".join(_CATEGORIES)}
Priorities: {", ".join(_PRIORITIES)} (high = urgent / blocking, low = no rush)

Sensitive data has already been redacted to placeholders like [EMAIL]; treat
those as normal tokens. Base the decision only on the ticket text."""


class _ClassificationLLM(BaseModel):
    category: str = Field(description=f"One of: {', '.join(_CATEGORIES)}")
    priority: str = Field(description=f"One of: {', '.join(_PRIORITIES)}")
    reason: str = Field(description="One-sentence justification.")
    confidence: float = Field(description="Confidence 0.0-1.0.")


def _classify_llm(text: str, focus: list[str]) -> tuple[Classification, dict[str, Any]]:
    user = text if not focus else f"Focus points: {'; '.join(focus)}\n\nTicket:\n{text}"
    result, usage = llm.structured(_SYSTEM, user, _ClassificationLLM)
    classification = Classification(
        category=result.category,
        priority=result.priority,
        reason=result.reason,
        confidence=round(result.confidence, 3),
        source="llm",
    )
    return classification, usage


def _corrupt_category(original: str, rng: random.Random) -> str:
    """Pick a wrong category — deterministic given the RNG."""
    options = [c for c in _CATEGORIES if c != original]
    return rng.choice(options) if options else original


def make_executor_node(model: FaultModel, rng: random.Random):
    def executor_node(state: OrchestratorState) -> dict[str, Any]:
        text = state.get("sanitized_text", state.get("input_text", ""))
        focus = state.get("plan", {}).get("focus", [])
        tool_calls: list[ToolCall] = list(state.get("tool_calls", []))
        llm_calls: list[Any] = list(state.get("llm_calls", []))
        errors: list[str] = list(state.get("errors", []))
        update: dict[str, Any] = {}

        tool_name = "llm.classify" if llm.is_available() else "rules.classify"
        t0 = time.perf_counter()
        try:
            maybe_crash(tool_name, model, rng)
            maybe_delay(model, rng)  # slow family: extra latency, no error
            if llm.is_available():
                classification, usage = _classify_llm(text, focus)
                llm_calls.append(llm.as_llm_call("executor", usage))
            else:
                rule = rule_classify(text)
                classification = Classification(source="rules", **rule)
            # corrupt family: swap the predicted category for a wrong one,
            # mark the source so the trace is honest about the injection.
            if should_corrupt(model, rng) and classification.get("category"):
                bad = _corrupt_category(classification["category"], rng)
                classification = Classification(
                    **{**classification, "category": bad, "source": classification.get("source", "?") + "+corrupted"}
                )
            update["classification"] = classification
            tool_calls.append(
                ToolCall(
                    tool=tool_name,
                    args={"chars": len(text)},
                    ok=True,
                    error=None,
                    duration_ms=round((time.perf_counter() - t0) * 1000, 3),
                )
            )
        except Exception as exc:  # injected ToolFailure or real API error
            errors.append(f"{type(exc).__name__}: {exc}")
            tool_calls.append(
                ToolCall(
                    tool=tool_name,
                    args={"chars": len(text)},
                    ok=False,
                    error=str(exc),
                    duration_ms=round((time.perf_counter() - t0) * 1000, 3),
                )
            )

        update["tool_calls"] = tool_calls
        update["llm_calls"] = llm_calls
        update["errors"] = errors
        return update

    return executor_node
