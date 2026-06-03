# CLAUDE.md

Guidance for Claude Code (and human contributors) working in this repository.

## Purpose

Research prototype of a **stateful, observable agentic orchestrator** (LangGraph)
with **Claude-driven nodes** (Anthropic API, `claude-opus-4-7`), for studying
non-functional properties — robustness, availability/performance,
privacy/compliance — of agentic LLM+tool systems. It is **not** a chatbot or a
product. Prioritise transparency, reproducibility and analyzable evidence over
features or polish.

## Architectural principles (do not violate without discussion)

1. **Explicit workflow, no autonomous agent loop.** The graph is the fixed
   pipeline `planner -> executor -> verifier -> finalizer -> END`. Nodes may use
   the LLM for their *judgment*, but must not choose their own control flow.
2. **Shared typed state.** Everything flows through `OrchestratorState`
   (`state.py`). Nodes are pure: read state, return only the changed keys.
3. **Every step must be observable.** Any new node goes through `traced_node`;
   any tool boundary records a `ToolCall`; any LLM call records an `LlmCall`
   (token usage). No work should happen that leaves no trace.
4. **One LLM seam.** All model calls go through `llm.py` (`structured()`), using
   the Messages API with structured outputs (Pydantic schemas). Don't call the
   SDK directly from a node. Default model `claude-opus-4-7`.
5. **Privacy by design.** PII redaction (`privacy.scan_and_redact`) runs in the
   planner *before any LLM call*, so the model never sees raw PII. Keep it that
   way — do not add an LLM call upstream of redaction.
6. **Graceful degradation, no mandatory cloud dependency.** Every LLM node must
   fall back to a deterministic stand-in when `llm.is_available()` is false
   (missing key/SDK or `LLM_ENABLED=false`), and must record LLM/API failures as
   errors rather than crashing. This keeps offline runs and failure experiments
   working.
7. **The finalizer stays deterministic.** The accept/reject gate is a transparent
   rule over the audit — do not turn it into an opaque LLM verdict.
8. **Minimal dependencies.** Do not add a library unless strictly necessary.

## Code structure

```
agentic_orchestrator/
  config.py        # Settings.from_env() — dataclass, dotenv-backed
  state.py         # OrchestratorState + trace record TypedDicts
  llm.py           # Anthropic client + structured() helper (the only LLM seam)
  tracing.py       # Tracer, traced_node, optional OTel
  privacy.py       # scan_and_redact (PII guardrail)
  perturbation.py  # perturb (robustness input edits)
  graph.py         # build_graph, run_once
  tools/           # classifier.py (rule-based fallback), failure.py (fault injection)
  nodes/           # planner / executor / verifier / finalizer
assessment/        # verification layer (consumes the orchestrator)
  tickets.jsonl    # labelled dataset
  harness.py       # campaigns: ticket × condition × seed
  metrics.py       # robustness / availability / privacy metrics
  oracles.py       # thresholds → PASS/FAIL verdicts
  report.py        # HTML report (no plotting deps)
  run.py           # CLI: python -m assessment.run
main.py            # orchestrator CLI
tests/             # pytest (run offline via LLM_ENABLED=false fixture)
runs/              # generated traces (gitignored)
assessment_runs/   # generated campaign outputs (gitignored)
```

## Assessment layer rules

- The orchestrator is the **subject under test** — keep it clean and observable.
  All judgement about *whether properties hold* lives in `assessment/`, never in
  the orchestrator nodes.
- Metrics are **pure functions** over `ExperimentResult` with explicit, documented
  definitions. New metric → add to `metrics.py` with a one-line definition.
- New property check → add a `PropertySpec` in `oracles.py` (property, metric, op,
  threshold). Keep thresholds conservative and tunable per study.
- Measure the privacy property on the **baseline** condition; PII loss under
  injected faults is a robustness signal, not a privacy defect.
- Campaigns must run **offline** (`--offline`) for a free deterministic baseline,
  and **live** for the real agent — same machinery.

## Conventions

- Python, `from __future__ import annotations`, type hints where they help.
- Small functions, clear names, **few comments** — only explain *why*.
- Nodes return partial `dict` updates; accumulate list-valued evidence by
  copying the existing list and appending.
- New tools: wrap the call with timing + a `ToolCall` record, and gate failure
  via `tools/failure.maybe_fail` so robustness experiments keep working.
- Keep traces JSON-serializable.

## Modification rules

- Touching the graph shape? Update `architecture.md` and the workflow strings
  in `main.py` / README.
- Adding a node? Register it in `graph.py` via `traced_node` and add a graph
  test asserting it appears in the trace.
- Adding state fields? Extend `state.py` and make sure they serialize.
- Don't break the `runs/<run_id>.json` + `events.jsonl` schema without updating
  tests and docs — offline analysis depends on it.

## Run & test

```bash
# setup
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt pytest
export ANTHROPIC_API_KEY=sk-ant-...   # for the LLM agent; omit for offline mode

# run scenarios
python main.py --input "Urgent: charged twice, refund to john@acme.com"   # nominal
python main.py --input "login broken" --failure-rate 1.0 --seed 1          # failure
python main.py --input "payment error, urgent" --perturb --seed 3          # perturbed
LLM_ENABLED=false python main.py --input "billing refund"                  # offline baseline

# tests (hermetic — forced offline, never hit the API)
pytest -q
```

Always verify the project still runs (`python main.py --input "..."`) and
`pytest -q` passes before considering a change done.
