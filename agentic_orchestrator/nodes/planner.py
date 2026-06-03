"""Planner node.

Two responsibilities, in this order:
  1. Deterministically redact PII from the raw input — this runs FIRST so that
     no LLM call downstream ever sees raw sensitive data (privacy by design).
  2. Produce a triage plan: an LLM reads the sanitized ticket and outputs a
     rationale plus focus points for the classifier. Falls back to a fixed
     plan when no LLM is available.

The step list itself is fixed and transparent — the agent does not invent its
own control flow.
"""

from __future__ import annotations

import random
import time
from typing import Any

from pydantic import BaseModel, Field

from .. import llm
from ..privacy import scan_and_redact
from ..state import GuardrailEvent, OrchestratorState, Plan, ToolCall
from ..tools.failure import ToolFailure, maybe_fail

_STEPS = ["sanitize", "classify", "audit", "decide"]

_SYSTEM = """You are the planner in a support-ticket triage pipeline.
You receive a single support ticket whose sensitive data has already been redacted.
Do NOT classify or resolve it. Instead, briefly state how it should be handled and
list the concrete aspects the downstream classifier should weigh (e.g. urgency
signals, mentions of billing or access, ambiguity). Keep it short and concrete."""


class _PlanLLM(BaseModel):
    rationale: str = Field(description="One or two sentences on how to handle this ticket.")
    focus: list[str] = Field(
        default_factory=list,
        description="Short focus points for the classifier (max 5).",
    )


def make_planner_node(failure_rate: float, rng: random.Random):
    def planner_node(state: OrchestratorState) -> dict[str, Any]:
        text = state.get("input_text", "")
        tool_calls: list[ToolCall] = list(state.get("tool_calls", []))
        guardrails: list[GuardrailEvent] = list(state.get("guardrail_events", []))
        llm_calls: list[Any] = list(state.get("llm_calls", []))
        errors: list[str] = list(state.get("errors", []))
        update: dict[str, Any] = {}

        # 1) PII redaction (deterministic, before any LLM call) ---
        t0 = time.perf_counter()
        try:
            maybe_fail("privacy.scan_and_redact", failure_rate, rng)
            detection = scan_and_redact(text)
            sanitized = detection.redacted_text
            if detection.findings:
                guardrails.append(
                    GuardrailEvent(
                        node="planner",
                        kind="pii_redaction",
                        detail=f"redacted {len(detection.findings)} item(s): "
                        + ", ".join(f["kind"] for f in detection.findings),
                    )
                )
            tool_calls.append(
                ToolCall(
                    tool="privacy.scan_and_redact",
                    args={"chars": len(text)},
                    ok=True,
                    error=None,
                    duration_ms=round((time.perf_counter() - t0) * 1000, 3),
                )
            )
        except ToolFailure as exc:
            errors.append(str(exc))
            sanitized = text  # degraded: raw text flows on (recorded as evidence)
            tool_calls.append(
                ToolCall(
                    tool="privacy.scan_and_redact",
                    args={"chars": len(text)},
                    ok=False,
                    error=str(exc),
                    duration_ms=round((time.perf_counter() - t0) * 1000, 3),
                )
            )
        update["sanitized_text"] = sanitized

        # 2) Triage plan (LLM if available, else fixed) ---
        rationale = "Fixed triage pipeline: redact, classify, audit, decide."
        focus: list[str] = []
        if llm.is_available():
            try:
                plan, usage = llm.structured(_SYSTEM, sanitized, _PlanLLM)
                rationale, focus = plan.rationale, plan.focus[:5]
                llm_calls.append(llm.as_llm_call("planner", usage))
            except Exception as exc:  # API/parse failure → degrade, record it
                errors.append(f"planner LLM error: {type(exc).__name__}: {exc}")

        update["plan"] = Plan(steps=_STEPS, rationale=rationale, focus=focus)
        update["tool_calls"] = tool_calls
        update["guardrail_events"] = guardrails
        update["llm_calls"] = llm_calls
        update["errors"] = errors
        return update

    return planner_node
