# Architecture

A short rationale for the design choices. The guiding principle is
**transparency and observability over cleverness**: the system should be easy
to reason about and produce evidence that supports offline analysis of
non-functional properties.

## 1. Explicit workflow, not an autonomous agent

The orchestration is a fixed LangGraph `StateGraph`:

```
planner -> executor -> verifier -> finalizer -> END
```

There is no agent loop deciding its own control flow. Each responsibility is a
named node with a single job. This makes every run reproducible and makes the
behaviour analyzable as a small, well-defined transition system — the natural
substrate for later building behavioural graphs/hypergraphs of executions.

Nodes are kept as nodes (rather than inlined into one function) precisely so
the *decisions* (planner) and *checks* (verifier) are observable in the trace
and can grow input-dependent later without changing the shape of the system.

## 2. Shared typed state

`OrchestratorState` (a `TypedDict`) is the only thing flowing through the
graph. Nodes are pure: they read state and return **only the keys they
change**; LangGraph merges the partial update. Cross-cutting evidence
(`tool_calls`, `guardrail_events`, `errors`) is accumulated by reading the
current list and returning the extended one — explicit, no hidden reducers.

## 3. Observability as a first-class layer

`tracing.Tracer` wraps every node via `traced_node`. For each node it records
state before/after, duration, output, status and errors, then writes:

- `runs/<run_id>.json` — the complete run record (human-inspectable);
- `runs/events.jsonl` — one line per node (machine-aggregatable).

This dual format means a single run is easy to read by hand, while many runs
are easy to stream and aggregate offline. OpenTelemetry is wired in as an
**optional** span layer, guarded by an import + env flag, so the prototype
never hard-depends on it.

## 4. The LLM nodes and the single LLM seam

Three of the four nodes are **Claude-driven**; each has a distinct, non-redundant
cognitive job, and all reasoning goes through one seam (`llm.py`):

| Node | LLM job | Output (structured) |
| --- | --- | --- |
| planner | triage analysis of the sanitized ticket | rationale + focus points |
| executor | classify the ticket | category, priority, reason, confidence |
| verifier | judge whether the classification is *sound* | sound? + concerns |
| finalizer | — (deterministic governance gate) | accept/reject + reason |

Every LLM call uses the Messages API with **structured outputs**
(`messages.parse` + a Pydantic schema), so a node returns a *validated object*,
never free text — which is what keeps the workflow analyzable. The system
prompt of each call is stable and carries a cache breakpoint; the volatile
per-ticket text is the user message.

**The finalizer stays deterministic on purpose.** The intelligence (extraction,
soundness judgment) is delegated to the model, but the terminal accept/reject
decision is a transparent rule over the audit. An auditable governance gate is
more defensible than an opaque model verdict, and it honors the constraint that
the agent must not decide its own control flow.

**Graceful degradation / offline mode.** `llm.is_available()` is false when the
SDK or API key is missing, or `LLM_ENABLED=false`. In that case every node falls
back to a deterministic stand-in (the rule-based `tools/classifier.py`,
keyword-scored). This keeps the prototype runnable offline, keeps a reproducible
baseline for experiments, and means an LLM/API failure is *recorded as evidence*
(an availability/robustness signal) rather than crashing the run.

MCP is intentionally *not* implemented yet, but the executor's tool-call
boundary (timed, success/failure recorded as `ToolCall`) and the `llm.py` seam
are the integration points: an MCP tool would be invoked there and traced
identically.

## 5. Non-functional properties, by construction

| Property | Mechanism | Evidence in trace |
| --- | --- | --- |
| Robustness | `perturbation.perturb` (input edits), `tools/failure.maybe_fail` (fault injection) | per-node `status`, `errors`, tool `ok` flags |
| Availability / performance | per-node timing | `duration_ms`, `total_duration_ms` |
| Privacy / compliance | `privacy.scan_and_redact` in the planner; verifier re-scans output | `guardrail_events`, residual-PII issues in `audit` |

The planner **redacts before any LLM call**, so neither the model nor the
traces ever see raw PII; the verifier independently re-scans the sanitized text,
turning "did redaction work?" into a measurable audit check. This privacy-by-
design ordering is the reason redaction lives in the planner rather than the
executor.

## 6. Failure handling philosophy

Injected tool failures are **caught and recorded as evidence**, not allowed to
crash the run. The pipeline continues in a degraded state so the verifier and
finalizer can react (e.g. reject with a reason). This is what lets a single run
produce a clean robustness data point instead of a stack trace.

## Assumptions (documented, minimal)

- **Triage** chosen as the task: simplest realistic vehicle for all three
  properties. Easily swappable — only `tools/` and `nodes/` would change.
- PII detection is **regex-based and conservative**: a prototype for measuring
  privacy behaviour, not a production DLP engine.
- State snapshots in traces deep-copy domain fields but skip the large
  accumulating lists (`tool_calls`, `guardrail_events`), which are persisted
  once at run finalization.

## 7. The assessment layer (`assessment/`)

The orchestrator collects evidence; the `assessment/` package turns it into a
*verdict*. It is a thin pipeline on top of the orchestrator:

```
dataset × conditions × seeds → harness → results → metrics → oracles → report
```

- **Harness** runs each labelled ticket under conditions (baseline / perturbation
  / fault) across several seeds — reproducible, and runnable offline for a free
  deterministic baseline.
- **Metrics** are pure functions with explicit definitions (e.g. prediction
  stability = baseline vs perturbed category agreement; privacy measured on the
  baseline condition, since fault-induced PII loss is a robustness concern, not a
  privacy defect).
- **Oracles** bind a property to a metric + comparison + threshold → PASS/FAIL.
  This is the step that makes the system a *verifier*, in language a reviewer
  reads directly.
- **Report** is hand-rolled HTML (no plotting dependency), verdicts first.

The split is deliberate: the orchestrator must stay a clean, observable
subject-under-test; all judgement about *whether properties hold* lives in the
assessment layer, so the two evolve independently.

## Possible next steps (out of scope for this version)

- Input-dependent planning / conditional edges (e.g. route abuse tickets to a
  different path).
- MCP tool integration at the executor's tool boundary / `llm.py` seam.
- Adaptive thinking + `effort` tuning per node, recorded in the trace, to study
  the cost/quality tradeoff.
- Statistical rigor in the assessment layer: confidence intervals / variance
  across many seeds, and significance tests when comparing configurations.
- More conditions: adversarial privacy inputs, semantic paraphrase perturbations,
  latency/availability fault models beyond tool failure.
