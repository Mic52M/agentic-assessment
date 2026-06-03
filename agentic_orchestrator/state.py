"""Shared, typed state for the orchestration graph.

`OrchestratorState` is the single object that flows through every node.
LangGraph merges the partial dict each node returns into this state, so
nodes stay pure: they read state and return only the keys they change.
"""

from __future__ import annotations

from typing import Any, TypedDict

# --- Domain payloads -------------------------------------------------------


class Plan(TypedDict, total=False):
    """Produced by the planner: which steps the executor must perform."""

    steps: list[str]
    rationale: str
    focus: list[str]  # aspects the downstream classifier should weigh


class Classification(TypedDict, total=False):
    category: str
    priority: str
    reason: str  # model's (or rule's) justification
    confidence: float  # 0.0-1.0 when available
    scores: dict[str, float]  # rule-based keyword scores, when applicable
    source: str  # "llm" | "rules"


class PiiFinding(TypedDict):
    kind: str
    value_preview: str  # never the raw value in clear — already masked
    span: tuple[int, int]


class AuditResult(TypedDict):
    passed: bool
    issues: list[str]
    pii_findings: list[PiiFinding]


class Decision(TypedDict):
    outcome: str  # "accepted" | "rejected"
    category: str
    priority: str
    reason: str


# --- Trace records ---------------------------------------------------------


class ToolCall(TypedDict):
    tool: str
    args: dict[str, Any]
    ok: bool
    error: str | None
    duration_ms: float


class LlmCall(TypedDict):
    node: str
    model: str
    input_tokens: int
    output_tokens: int
    cache_read_input_tokens: int
    duration_ms: float
    usd_cost: float


class GuardrailEvent(TypedDict):
    node: str
    kind: str  # e.g. "pii_redaction"
    detail: str


# --- The graph state -------------------------------------------------------


class OrchestratorState(TypedDict, total=False):
    # Inputs / identity
    run_id: str
    input_text: str

    # Per-node outputs
    plan: Plan
    sanitized_text: str
    classification: Classification
    audit: AuditResult
    decision: Decision

    # Cross-cutting evidence collected during the run
    tool_calls: list[ToolCall]
    llm_calls: list[LlmCall]
    guardrail_events: list[GuardrailEvent]
    errors: list[str]
