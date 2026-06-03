# Project state and change log

This document describes **what concretely exists in the repo** and **what each
phase of work changed**. It is the canonical entry point for understanding the
current state of the code; the research motivation lives in `architecture.md`
and `README.md`.

---

## 1. What is in the repo (concrete inventory)

The repository contains two cooperating Python packages plus a small CLI and a
test suite. There is no service, no daemon, no web UI — every artifact is a
file you can read.

### 1.1 `agentic_orchestrator/` — the subject under test

A LangGraph-based pipeline that triages a single support ticket. The graph is
**fixed and linear** (`planner -> executor -> verifier -> finalizer -> END`)
and every transition leaves a structured trace on disk.

| File | What it contains |
| --- | --- |
| `config.py` | `Settings` dataclass loaded from environment / `.env`. Exposes `runs_dir`, `llm_enabled`, `llm_model`, `otel_enabled`, `tool_failure_rate`. |
| `state.py` | `OrchestratorState` (TypedDict that flows through the graph) and the per-record TypedDicts: `Plan`, `Classification`, `AuditResult`, `Decision`, `ToolCall`, `LlmCall`, `GuardrailEvent`, `PiiFinding`. |
| `llm.py` | The single seam to Anthropic. `current_model()` reads `LLM_MODEL`; `structured(system, user, schema)` does one `messages.parse` call with structured outputs; `estimate_cost_usd()` uses a versioned price table; `is_available()` gates the offline fallback. |
| `tracing.py` | `Tracer` writes `runs/<run_id>.json` (full record) and appends one line per node to `runs/events.jsonl`. Optional OTel spans gated by `OTEL_ENABLED`. `traced_node` wraps each LangGraph node. |
| `privacy.py` | `scan_and_redact(text) -> Detection`. Regex-based, conservative; used by the planner to redact PII *before* any LLM call. Also re-used by the verifier as a soundness check. |
| `perturbation.py` | `perturb(text, seed, intensity)` — typo-injection only (one perturbation family today). |
| `graph.py` | `build_graph(tracer, settings, rng)` wires the four nodes; `run_once(input, settings, seed, run_id)` is the single entry point that builds graph + tracer per run and returns `(final_state, trace_path)`. |
| `nodes/planner.py` | Runs PII redaction first; then asks the LLM for a triage rationale + focus points (or returns a fixed plan when offline). Records a `ToolCall` for the redaction step. |
| `nodes/executor.py` | Classifies the sanitized ticket via the LLM (or `tools/classifier.py` offline). Output is a validated `Classification` (category, priority, reason, confidence, source). |
| `nodes/verifier.py` | Deterministic checks (residual PII, well-formedness) plus an optional LLM "is this classification sound?" judgement. Output is an `AuditResult`. |
| `nodes/finalizer.py` | Deterministic accept/reject rule over the audit. Never an LLM call. |
| `tools/classifier.py` | Keyword-scored rule-based classifier used as the offline fallback for the executor. |
| `tools/failure.py` | `maybe_fail(tool, rate, rng)` — probabilistic fault injection raising `ToolFailure`. |

### 1.2 `assessment/` — the verification layer

Consumes the orchestrator as a black-box subject. Runs campaigns, computes
metrics, evaluates oracles, renders a report.

| File | What it contains |
| --- | --- |
| `tickets.jsonl` | Labelled dataset (`id`, `text`, `expected_category`, `expected_priority`, `contains_pii`). |
| `harness.py` | `Condition` (named experimental knob: perturbation on/off, intensity, failure rate). `Ticket`, `ExperimentResult` dataclasses. `run_campaign(...)` iterates ticket × seed × condition and persists `results.json`. |
| `metrics.py` | Pure functions over `list[ExperimentResult]`. Produces a flat namespaced dict: `robustness.*`, `availability.*`, `privacy.*`. |
| `oracles.py` | `PropertySpec(property, name, metric, op, threshold)` and `evaluate(metrics, specs) -> list[Verdict]`. `DEFAULT_SPECS` is the current set of pass/fail criteria. |
| `report.py` | Hand-rolled HTML (no plotting dependency). Verdicts first, then per-metric tables. |
| `run.py` | CLI entry point: `python -m assessment.run [--offline] [--limit N] [--seeds N]`. |

### 1.3 CLI and tests

- `main.py` — single-ticket CLI. `--input` or `--input-file`, `--failure-rate`,
  `--perturb`, `--seed`, `--runs-dir`. Exit code 0 if accepted, 1 otherwise.
- `tests/` — pytest, forced offline via an autouse fixture (`LLM_ENABLED=false`).
  Currently: `test_classifier.py`, `test_privacy.py`, `test_graph.py`,
  `test_assessment.py`, `test_determinism.py`.

### 1.4 Generated artifacts (gitignored)

- `runs/<run_id>.json` — full run record for one orchestrator invocation.
- `runs/events.jsonl` — one JSON line per node, append-only across all runs.
- `assessment_runs/` — per-campaign output: `runs/`, `results.json`,
  `metrics.json`, `report.html`.

### 1.5 Documentation

- `README.md` — install, run, layout, reproducibility notes.
- `architecture.md` — design rationale (why explicit workflow, why a single LLM
  seam, why deterministic finalizer, etc.).
- `CLAUDE.md` — contributor and agent conventions.
- `docs/PROGRESS.md` — this file.

---

## 2. Phase log

Each phase has a fixed goal, a list of concrete changes, and a verification
step. Phases are described in the order they were executed.

### Fase 0 — Reproducibility foundations

**Goal.** Make every future experiment bit-reproducible (modulo wall clock)
and make the choice of LLM a first-class experimental axis. Add cost
accounting now, before any campaign is run at scale.

**Changes.**

- `requirements.txt`: every dependency pinned to an exact version
  (`langgraph==1.2.2`, `anthropic==0.105.2`, `pydantic==2.13.4`,
  `python-dotenv==1.2.2`). Optional extras (OpenTelemetry, Presidio, pytest)
  listed with exact pins but commented out.
- `agentic_orchestrator/llm.py`: removed the `MODEL` module constant. Added
  `current_model()` that reads `LLM_MODEL` (default `claude-opus-4-7`), a
  versioned `_PRICING_PER_MTOK` table (Opus 4.7/4.8, Sonnet 4.6, Haiku 4.5),
  and `estimate_cost_usd(model, in, out, cache_read)`. Each `LlmCall` record
  now carries a `usd_cost` field.
- `agentic_orchestrator/config.py`: `Settings` gained `llm_model`.
- `agentic_orchestrator/state.py`: `LlmCall` gained `usd_cost`.
- `agentic_orchestrator/tracing.py`: `Tracer.finalize` aggregates
  `llm_cache_read_tokens_total`, `llm_cache_hit_ratio`, and `usd_cost_total`
  in the per-run record.
- `main.py`: summary line shows the active model id.
- `tests/test_determinism.py` (new): three tests guarding the reproducibility
  claim — same input + seed (offline) ⇒ trace identical modulo timestamps;
  same seed under fault injection ⇒ identical; different seed under fault ⇒
  diverges (sanity check that the seed actually drives the RNG).
- `README.md`: new **Reproducibility** section documenting the offline mode,
  the model-as-condition contract, and the cost/cache fields.

**Verification.** `pytest -q` → 17 passed. `LLM_ENABLED=false python main.py
--input "..."` produces a trace containing the new cost/cache fields (all
zero offline, as expected).

**Why this had to come first.** Without determinism, no campaign produces
defensible numbers. Without `llm_model` as a setting, cross-model studies
require code changes. Without cost in the trace, availability/cost analysis
is impossible after the fact.

### Fase 1 — Independent privacy oracle and adversarial dataset

**Goal.** Eliminate the circularity in the privacy leakage metric: until now,
the same `privacy.scan_and_redact` was used both to redact the input *and* to
measure residual PII, so the metric was effectively constant at zero. Replace
the measurement detector with one (or more) *independent* from the redactor,
and extend the dataset with adversarial cases that actually exercise it.

**Changes.**

- `assessment/detectors.py` (new). A small registry of independent PII
  detectors with a common `Detector` protocol (`name`, `available()`,
  `detect(text) -> list[PiiHit]`):
  - `regex_strong` (default, always available) — stricter than the
    orchestrator's redactor. Folds unicode lookalikes (Cyrillic а/е/о/р/с/у/х,
    fullwidth `＠`/`．`, etc.) and word-spelled digits ("five five five one
    two…"), recognises `[at]`/`[dot]` and `at`/`dot` email obfuscations,
    collapses spaced/dotted digit groups (`4 1 1 1 …`, `4111.1111.…`),
    matches IBAN with internal whitespace, SSNs, and phones with `+` prefix
    or explicit group structure. Plain bare digit runs (order ids, refs,
    SKUs, build hashes) are deliberately *not* flagged.
  - `presidio` — uses Microsoft Presidio if the `presidio-analyzer` package
    is installed; otherwise reports unavailable. No hard dependency added.
  - `llm_judge` — structured LLM call through the existing seam, asking a
    strict auditor "does this text contain PII, possibly obfuscated?".
    Available iff the Anthropic seam is. Intended for cross-checking, not
    routine use.
  - `detect_residual(text, oracle=...)` is the binary-leakage helper the
    harness consumes.
- `assessment/harness.py`:
  - `_result_from_state` now uses `detectors.detect_residual(sanitized_text,
    oracle=...)` instead of re-running the redactor.
  - `run_campaign` accepts `privacy_oracle="regex_strong"` (default).
  - `Ticket` gained an optional `pii_subcategory` field
    (`plain | adversarial | trap | None`).
  - `load_dataset` accepts an explicit `path` so alternate datasets are easy
    to load in experiments.
- `assessment/run.py`: new `--privacy-oracle {regex_strong,presidio,llm_judge}`
  flag. Falls back to `regex_strong` with a warning if the chosen oracle is
  unavailable. Also fixes the hardcoded model id in the printed banner.
- `assessment/tickets.jsonl`: expanded from 12 to 60 tickets:
  - Category coverage: ~18 billing, ~14 technical, ~14 account, ~6 abuse,
    ~8 other.
  - Priority coverage across all three levels for every category.
  - PII subcategories: ~6 `plain`, ~12 `adversarial`, ~5 `trap`. The
    adversarial set is the one that makes the privacy metric discriminate.
- `assessment/tickets.schema.md` (new). Canonical reference for the dataset:
  field semantics, annotation criteria (category/priority/PII), subcategory
  meanings, current composition targets, and how to add new tickets without
  silently rebalancing the slices.
- `tests/test_detectors.py` (new, 19 tests):
  - Per-case checks that `regex_strong` catches every adversarial form
    appearing in the dataset.
  - Per-case checks that it does *not* fire on the trap cases.
  - An integration check pinning the contract that motivated the phase: run
    an obfuscated PII ticket through the orchestrator, observe that the
    sanitized text still contains the email, and observe that the
    independent detector flags it. This is the proof the metric is no
    longer circular.
  - Availability gates: `regex_strong` always available; `llm_judge`
    unavailable under offline tests.

**Verification.**
- `pytest -q` → 36 passed.
- Smoke campaign `python -m assessment.run --offline --limit 8 --seeds 1`
  → all PASS (small subset, no adversarial cases hit).
- Full offline campaign over all 60 tickets, 1 seed:
  - `privacy.pii_leakage_rate = 0.316` (was 0 by construction in Fase 0).
  - `privacy.redaction_coverage = 0.684` (the orchestrator's regex catches
    only the `plain` PII; the `adversarial` slice escapes it).
  - `robustness.accuracy_baseline = 0.433` — the rule-based classifier is
    too narrow for the expanded dataset. This is a SUT observation, not an
    assessment-layer defect: the classifier is the offline fallback, not
    the subject the paper is meant to evaluate. Recorded here so it is not
    re-discovered later.
- All other PASS verdicts unchanged.

**Why this had to come now.** Statistical rigor (Fase 2) is meaningless if
the underlying metrics don't discriminate between systems. Without the
independent oracle, every privacy CI would be `[0.0, 0.0]` and the property
would be trivially "passed" regardless of the SUT. With Fase 1 in place,
both Fase 2 (CI / significance) and Fase 4 (agency scale, where higher
levels may regress on privacy) have a real signal to operate on.

### Fase 2 — Statistical rigor

**Goal.** Replace point estimates with quantified uncertainty. Make oracle
verdicts robust to sampling noise, and surface real (not anecdotal)
differences between conditions. All seeded for reproducibility.

**Changes.**

- `assessment/stats.py` (new). Statistical engine. Two primitives plus a
  small set of standard comparisons:
  - `bootstrap_ci(metric_fn, results, n_iter, alpha, seed) -> MetricStat`.
    Percentile CI obtained by **clustered bootstrap on `ticket_id`**:
    tickets are the independent units; seeds and conditions are repeated
    measures on the same ticket, so resampling individual rows would
    underestimate variance. Failed-resample metrics (zero denominators) are
    skipped, not propagated as zeros.
  - `paired_permutation_test(stat_fn, pairs, n_iter, seed) -> (delta, p)`.
    Two-sided exact-randomisation test for paired observations. +1 smoothing
    on both numerator and denominator so the p-value never collapses to 0.
  - `standard_comparisons(results)` returns the default comparison set:
    accuracy under perturbation vs baseline (paired by ticket+seed) and
    completion under fault vs baseline.
  - `MetricStat(value, ci_low, ci_high, n)` and `Comparison(name, delta,
    p_value, n)` dataclasses, JSON-serialisable.
- `assessment/metrics.py`. Refactored so each metric is a named function in
  `METRIC_FNS: dict[str, MetricFn]`. `compute_metrics` returns point
  estimates (kept for callers that don't need CIs — e.g. existing tests);
  new `compute_metric_stats(results, n_bootstrap, seed)` returns
  `dict[str, MetricStat]` by wrapping each function in `bootstrap_ci`.
  Adding a metric is now a single registry insertion.
- `assessment/oracles.py`. `Verdict` gained a `ci: tuple[float, float] | None`
  field. `evaluate` accepts either `dict[str, float]` (legacy) or
  `dict[str, MetricStat]` (new). With `MetricStat`, the comparison uses the
  **conservative CI bound** — `ci_low` for `>=`/`>` thresholds, `ci_high`
  for `<=`/`<`. A PASS verdict now means "the property holds even at the
  unfavourable end of our uncertainty", not just "the point estimate
  happens to land on the right side".
- `assessment/report.py`. New "All metrics (95% CI)" table with `value`,
  CI, and `n` (tickets). New "Paired comparisons (permutation test)"
  block. Verdict table shows CI inline. Significance highlighting at
  α = 0.05 for the comparison block.
- `assessment/run.py`. Two new flags: `--bootstrap-iters` (default 1000;
  set to 0 to skip CI/comparisons) and `--bootstrap-seed` (default 0).
  `metrics.json` payload is now `{"metrics": {...}, "comparisons": [...]}`
  with `MetricStat` and `Comparison` serialised as dicts. The stdout
  verdict table prints CIs and the comparisons block.
- `assessment/power.py` (new). Sample-size diagnostic — not a formal power
  analysis (we don't yet have effect-size assumptions to commit to), but a
  practical "how precise is each metric now, and how much more data buys
  half the width?" report driven by the asymptotic `CI_width ∝ 1/sqrt(n)`
  scaling. Usage: `python -m assessment.power assessment_runs/results.json`.
- `tests/test_stats.py` (new, 9 tests). Pin contracts the rest of the
  layer relies on: constant metric ⇒ zero-width CI; bootstrap is seeded;
  CI contains the point estimate; identical paired samples ⇒ p ≈ 1; strong
  consistent effect ⇒ small p; oracle uses `ci_low` for `>=`, `ci_high`
  for `<=`; oracle is backward-compatible with raw floats; the registry
  in `compute_metric_stats` matches `METRIC_FNS`.

**Verification.**
- `pytest -q` → 45 passed (was 36).
- Full campaign, 60 tickets × 2 seeds × 3 conditions = 360 runs:
  - `prediction_stability` point estimate `0.775`, CI `[0.69, 0.85]` ⇒
    **FAIL** under the CI-aware oracle even though the point estimate
    clears the 0.70 threshold. This is the desired behaviour: the seed
    budget is too small to confidently claim the property, and the
    verdict says so.
  - Comparison `accuracy: perturbation − baseline`: Δ = -0.175,
    p = 0.001 — a statistically significant degradation under
    perturbation. With Fase 0–1 we could only report the point
    difference; we couldn't say whether it was real.
  - Comparison `completion: fault − baseline`: Δ = 0, p = 1 — no
    detectable completion loss under the current fault model. Useful as
    a *negative* result, motivating richer fault families in Fase 3.
- `assessment.power` correctly reports CI widths and the ×4 ticket budget
  needed to roughly halve them.

**Why this had to come now.** Fase 3 will add new condition families
(semantic perturbations, slow/corrupt faults, prompt injection) and Fase 4
will scale agency from L0 to L4. Both produce many condition×level cells
to compare. Without bootstrap CI and a paired test, every "level L3 does
worse on privacy than L0" claim would be eyeballed from point estimates.
With the engine in place, those claims become statistically defensible by
construction.

### Fase 3 — Perturbation and fault families

**Goal.** Replace the single typo perturbation and single crash failure
with proper families, so the assessment exercises distinct failure modes
rather than one stylised stand-in. Add metrics that name the new failure
modes explicitly so the report makes them legible.

**Changes.**

- `agentic_orchestrator/perturbation.py`. Rewritten as a small dispatcher
  with a `PerturbationFamily` enum:
  - `typo` — character-level edits (the original family, unchanged).
  - `paraphrase` — surface rewrite. LLM-driven via the standard seam when
    available; deterministic synonym substitution when offline. The LLM
    call happens *outside* `run_once` so it doesn't pollute the SUT's
    per-run cost record — it's an experiment-preparation cost.
  - `prompt_injection` — appends a fixed adversarial suffix asking the
    classifier to mislabel the ticket as `other/low`. Suffix string is
    versioned in the module so comparisons across runs are reproducible.
- `agentic_orchestrator/tools/failure.py`. New `FaultModel` dataclass
  carrying four rates: `crash_rate`, `slow_rate`, `slow_ms`,
  `corrupt_rate`. Helpers `maybe_crash`, `maybe_delay`, `should_corrupt`
  take the model and a seeded RNG. The old `maybe_fail(tool, rate, rng)`
  is kept as a back-compat shim so the planner (which only needs crash)
  doesn't need to change.
- `agentic_orchestrator/config.py`. `Settings` gained three new fields
  (`tool_slow_rate`, `tool_slow_ms`, `tool_corrupt_rate`) plus a
  `fault_model()` method that builds a `FaultModel`.
- `agentic_orchestrator/nodes/executor.py`. Now consumes a `FaultModel`
  instead of a bare rate. Around the classification step it calls
  `maybe_crash` (existing behaviour), `maybe_delay` (new: adds wall-clock
  latency without erroring), and `should_corrupt` (new: replaces the
  predicted category with a wrong-but-valid category and tags the
  classification source as `"<original>+corrupted"` so the trace is
  honest about the injection).
- `assessment/harness.py`. `Condition` extended with
  `perturbation_family`, `fault_family`, `slow_rate`, `slow_ms`,
  `corrupt_rate`. `ExperimentResult` carries the family tags forward so
  the metrics layer can filter on family rather than parsing condition
  names. New `DEFAULT_CONDITIONS` = `baseline`, `perturbation_typo`,
  `perturbation_paraphrase`, `perturbation_injection`, `fault_crash`,
  `fault_slow`, `fault_corrupt` (7 conditions).
- `assessment/metrics.py`. Filters rewritten to use families, not
  condition names. Four new metrics:
  - `robustness.paraphrase_stability` — paired stability baseline vs
    paraphrase, like the existing typo metric.
  - `robustness.injection_resistance` — accuracy under prompt injection
    against the *expected* category (not the baseline prediction, so a
    classifier that already misclassifies doesn't get credit for being
    "resistant").
  - `robustness.verifier_catch_rate` — under the corrupt fault, fraction
    of runs the verifier rejected. Designed to expose blind spots: a
    structural verifier scores ~0 here, an LLM verifier should score
    higher. The asymmetry is the paper's point.
  - `availability.degraded_latency_p95_ms` — p95 latency under the slow
    fault.
  All four return vacuously safe defaults (1.0 for `>=` thresholds,
  0.0 for `<=`) when no rows match, so partial campaigns don't
  spuriously FAIL on absent conditions.
- `assessment/oracles.py`. `DEFAULT_SPECS` updated with conservative
  thresholds for every new metric. Existing names tightened
  (`prediction_stability` is now labelled "Stability under typo
  perturbation"; `completion_under_fault` is "under crash injection").
- `tests/test_perturbation_families.py` (new, 9 tests). Pin per-family
  contracts: typo is seeded and length-preserving; paraphrase changes
  known synonyms and is seeded; injection appends a fixed suffix and
  ignores the seed (by design); enum / string dispatch are equivalent;
  unknown families raise.
- `tests/test_fault_model.py` (new, 9 tests). Unit tests for the helpers
  (rate 0 never fires, rate 1 always fires, slow actually sleeps) plus
  integration tests via `run_once`: slow fault increases per-tool latency
  by the configured amount, corrupt fault swaps the category and tags the
  source, the offline (structural) verifier does *not* catch the
  corruption — pinning the expected blind spot.
- `tests/test_assessment.py`. The `_r(**kw)` helper derives
  `perturbation_family` / `fault_family` from the legacy condition name
  when not set explicitly, so old test fixtures keep working without
  rewriting them.

**Verification.**
- `pytest -q` → 63 passed (was 45).
- Smoke campaign with 10 tickets × 2 seeds × 7 conditions = 140 runs
  (offline, `--bootstrap-iters 1000`). Highlights:
  - `paraphrase_stability = 1.0 [1, 1]` — **PASS but be careful**: this
    reflects the *offline* synonym-substitution fallback, which by
    construction preserves keyword cues. With an LLM-driven paraphrase
    this number will move; the metric works, the offline value just
    isn't informative. Documented so it isn't mistaken for a real
    stability result.
  - `injection_resistance = 0.8 [0.5, 1]` — point estimate clears the
    threshold but the conservative CI lower bound 0.5 fails it. The
    rule-based classifier *partially* resists because its keyword-based
    decision is dominated by the original ticket text; the LLM behaviour
    will differ and is what the paper wants to measure.
  - `verifier_catch_rate = 0.0 [0, 0]` — exact-zero FAIL on the corrupt
    fault. This is the **finding the metric was designed to expose**: a
    structural verifier is blind to semantic corruption. With an
    LLM-driven soundness judgement the value should rise; the gap
    between the two is precisely the "what does the LLM verifier buy
    you?" question.
  - `degraded_latency_p95_ms ≈ 30 ms` vs baseline `≈ 10 ms` (slow
    injects 20 ms per call). Confirms slow fault is wired into the
    timing path correctly.
- All Fase 0–2 properties still pass on the small smoke set.

**Why this had to come now.** Fase 4 will scale up the *autonomy* of the
orchestrator (L0 → L4). The interesting story is how each non-functional
property degrades as the system becomes more agentic — and that story is
only legible if the failure modes the system is exposed to are
specifically named and measured. With three perturbation families and
three fault families, the Fase 4 matrix (level × condition) is a real
experimental surface, not a single cell.

**Open caveats carried into Fase 4.**
- The offline `paraphrase` family is too gentle to be a real stress test.
  Either accept it as a sanity-check baseline and rely on the LLM
  paraphrase for the paper, or strengthen the offline fallback (deeper
  rewrites, sentence reordering) in Fase 5.
- The `injection_resistance` metric assumes a single fixed suffix.
  Reviewers will ask "what about other injection patterns?". A small
  injection corpus is a likely Fase 5 deliverable.
- The structural verifier's `verifier_catch_rate = 0` is expected and
  documented; it stops being a defect once the paper frames the result
  honestly.
