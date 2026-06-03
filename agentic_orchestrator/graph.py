"""Graph construction and run driver.

Wires the four nodes into an explicit linear LangGraph workflow:

    planner -> executor -> verifier -> finalizer -> END

Every node is wrapped by the Tracer, so each transition leaves a structured
record. `run_once` builds a fresh graph + tracer per run and returns the
final state together with the path to the persisted run trace.
"""

from __future__ import annotations

import random
import uuid
from pathlib import Path
from typing import Any

from langgraph.graph import END, StateGraph

from .config import Settings
from .nodes.executor import make_executor_node
from .nodes.finalizer import finalizer_node
from .nodes.planner import make_planner_node
from .nodes.verifier import verifier_node
from .state import OrchestratorState
from .tracing import Tracer, traced_node


def build_graph(tracer: Tracer, settings: Settings, rng: random.Random):
    fault_model = settings.fault_model()
    planner_node = make_planner_node(settings.tool_failure_rate, rng)
    executor_node = make_executor_node(fault_model, rng)

    builder = StateGraph(OrchestratorState)
    builder.add_node("planner", traced_node("planner", tracer, planner_node))
    builder.add_node("executor", traced_node("executor", tracer, executor_node))
    builder.add_node("verifier", traced_node("verifier", tracer, verifier_node))
    builder.add_node("finalizer", traced_node("finalizer", tracer, finalizer_node))

    builder.set_entry_point("planner")
    builder.add_edge("planner", "executor")
    builder.add_edge("executor", "verifier")
    builder.add_edge("verifier", "finalizer")
    builder.add_edge("finalizer", END)
    return builder.compile()


def run_once(
    input_text: str,
    settings: Settings | None = None,
    *,
    seed: int = 0,
    run_id: str | None = None,
) -> tuple[OrchestratorState, Path]:
    settings = settings or Settings.from_env()
    run_id = run_id or uuid.uuid4().hex[:12]
    rng = random.Random(seed)

    tracer = Tracer(run_id, settings.runs_dir, otel_enabled=settings.otel_enabled)
    graph = build_graph(tracer, settings, rng)

    initial: OrchestratorState = {
        "run_id": run_id,
        "input_text": input_text,
        "tool_calls": [],
        "llm_calls": [],
        "guardrail_events": [],
        "errors": [],
    }
    final_state: dict[str, Any] = graph.invoke(initial)
    trace_path = tracer.finalize(final_state)  # type: ignore[arg-type]
    return final_state, trace_path  # type: ignore[return-value]
