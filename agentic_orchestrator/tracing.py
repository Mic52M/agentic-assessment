"""Observability layer.

The Tracer captures, for each run, a structured record of every node:
state before/after, duration, output, errors and guardrail events. It is
the primary source of evidence for offline analysis of non-functional
properties (robustness, availability, privacy).

Two outputs per run:
  * runs/<run_id>.json   — the full run record (easy to inspect by hand)
  * runs/events.jsonl    — one line per node, append-only (easy to stream/aggregate)

OpenTelemetry is optional: if the SDK is installed and OTEL_ENABLED is set,
each node also becomes a span. Absence of the SDK is a silent no-op so the
prototype never hard-depends on it.
"""

from __future__ import annotations

import copy
import json
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

from .state import OrchestratorState

# --- Optional OpenTelemetry ------------------------------------------------

_OTEL_TRACER = None


def _maybe_init_otel(enabled: bool) -> None:
    global _OTEL_TRACER
    if not enabled or _OTEL_TRACER is not None:
        return
    try:
        from opentelemetry import trace
        from opentelemetry.sdk.trace import TracerProvider

        trace.set_tracer_provider(TracerProvider())
        _OTEL_TRACER = trace.get_tracer("agentic_orchestrator")
    except ImportError:
        _OTEL_TRACER = None


# --- Serialization helpers -------------------------------------------------

# State keys that are large or redundant to snapshot on every node.
_SNAPSHOT_SKIP = {"tool_calls", "llm_calls", "guardrail_events"}


def _snapshot(state: OrchestratorState) -> dict[str, Any]:
    """A JSON-safe shallow copy of state for before/after diffing."""
    return {k: copy.deepcopy(v) for k, v in state.items() if k not in _SNAPSHOT_SKIP}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class Tracer:
    def __init__(self, run_id: str, runs_dir: Path, otel_enabled: bool = False) -> None:
        self.run_id = run_id
        self.runs_dir = runs_dir
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        self.started_at = _now_iso()
        self.node_traces: list[dict[str, Any]] = []
        _maybe_init_otel(otel_enabled)

    @contextmanager
    def node(self, name: str, state: OrchestratorState) -> Iterator[dict[str, Any]]:
        """Time a node, capturing before/after state and any error.

        Yields a mutable record the caller may enrich (e.g. attach output).
        """
        record: dict[str, Any] = {
            "run_id": self.run_id,
            "node": name,
            "started_at": _now_iso(),
            "state_before": _snapshot(state),
            "status": "ok",
            "error": None,
        }
        t0 = time.perf_counter()
        span_cm = (
            _OTEL_TRACER.start_as_current_span(name)
            if _OTEL_TRACER is not None
            else _nullcontext()
        )
        try:
            with span_cm:
                yield record
        except Exception as exc:  # node failures are evidence, not crashes here
            record["status"] = "error"
            record["error"] = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            record["duration_ms"] = round((time.perf_counter() - t0) * 1000, 3)
            record["ended_at"] = _now_iso()
            self.node_traces.append(record)
            self._append_event(record)

    def _append_event(self, record: dict[str, Any]) -> None:
        path = self.runs_dir / "events.jsonl"
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    def finalize(self, state: OrchestratorState) -> Path:
        """Write the complete run record and return its path."""
        llm_calls = state.get("llm_calls", [])
        input_tokens = sum(c["input_tokens"] for c in llm_calls)
        cache_read = sum(c.get("cache_read_input_tokens", 0) for c in llm_calls)
        run_record = {
            "run_id": self.run_id,
            "started_at": self.started_at,
            "ended_at": _now_iso(),
            "input_text": state.get("input_text"),
            "total_duration_ms": round(
                sum(t["duration_ms"] for t in self.node_traces), 3
            ),
            "nodes": self.node_traces,
            "tool_calls": state.get("tool_calls", []),
            "llm_calls": llm_calls,
            "llm_tokens_total": sum(
                c["input_tokens"] + c["output_tokens"] for c in llm_calls
            ),
            "llm_cache_read_tokens_total": cache_read,
            "llm_cache_hit_ratio": (
                round(cache_read / input_tokens, 4) if input_tokens else 0.0
            ),
            "usd_cost_total": round(
                sum(c.get("usd_cost", 0.0) for c in llm_calls), 6
            ),
            "guardrail_events": state.get("guardrail_events", []),
            "errors": state.get("errors", []),
            "decision": state.get("decision"),
        }
        path = self.runs_dir / f"{self.run_id}.json"
        path.write_text(
            json.dumps(run_record, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return path


@contextmanager
def _nullcontext() -> Iterator[None]:
    yield None


def traced_node(
    name: str, tracer: Tracer, fn: Callable[[OrchestratorState], dict[str, Any]]
) -> Callable[[OrchestratorState], dict[str, Any]]:
    """Wrap a node function so every invocation is traced.

    The wrapped function records the node's returned partial update as its
    `output`, then returns it unchanged to LangGraph.
    """

    def wrapped(state: OrchestratorState) -> dict[str, Any]:
        with tracer.node(name, state) as record:
            update = fn(state)
            record["output"] = _snapshot(update) if isinstance(update, dict) else update
            return update

    return wrapped
