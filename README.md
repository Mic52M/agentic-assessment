# agentic-assessment

**A research prototype for measuring non-functional properties of LLM-driven
agentic systems** — robustness, availability/cost, privacy/compliance — with
quantified uncertainty and reproducible evidence.

This is the codebase behind ongoing work on how to *verify* (not just demo)
properties of systems where one or more steps are decided by a language
model. It is deliberately not a chatbot, not a product, and not a benchmark
leaderboard. The contribution is the **assessment methodology** and the
infrastructure that makes it executable; the triage agent shipped in the
repo is a vehicle for that methodology, swappable for any other system that
fits a small interface.

---

## 1. The problem

Standard testing answers "does this function return the right value?".
That question is too narrow for an LLM-in-the-loop system, because most of
what we worry about is not correctness but *non-functional behaviour*:
does the system stay robust under noisy or adversarial input? does it
degrade gracefully when a tool fails? does it leak sensitive data on the
way through the model? what does each of these cost in tokens, latency,
dollars?

These questions resist anecdotal answers. A run that looks fine tells you
nothing; a run that looks broken tells you only that *one* run was broken.
What is needed is:

1. A *subject under test* that is observable by construction — every
   decision, every tool call, every model call leaves structured evidence
   on disk.
2. A *protocol* that exercises the system under controlled families of
   stress, repeated enough times that point estimates carry confidence
   intervals.
3. A *verdict layer* that compares measurements to thresholds in a way
   that is statistically honest, so a PASS means more than "the point
   estimate happened to land on the right side".

This repository implements all three.

---

## 2. The approach

Three principles run through everything in the codebase. They are
independent of the specific task the agent performs.

**Transparency is a design choice, not an add-on.** The orchestrator uses
an explicit, fixed workflow rather than an autonomous agent loop. State
is shared and typed. All model calls flow through a single seam that
forces structured outputs. Every node is wrapped by a tracer that records
its before/after state, duration, errors, and outputs. None of this is
"logging" bolted onto a black box; it is the architecture. A system
designed to be opaque cannot be made transparent by external tools.

**Measurement must be independent of what it measures.** The detector
that checks whether sensitive data leaked from the redactor cannot be
the redactor itself — that would always score perfectly, by construction.
The assessment layer therefore carries its own family of PII oracles
(a strong regex detector, optional Presidio, an optional LLM judge);
they are independent from the orchestrator's own redactor, and their
agreement is itself reportable evidence. The same principle generalises:
when evaluating any property of a system, the evaluator must be built on
distinct evidence.

**Verdicts must reflect uncertainty, not hide it.** Every metric is
computed with a percentile bootstrap, clustered on the independent unit
of the experiment (the ticket), so the confidence interval respects the
repeated-measures structure. Pass/fail oracles compare the threshold to
the *conservative end* of the CI — the lower bound for "≥" thresholds,
the upper bound for "≤". PASS means "the property holds even at the
unfavourable end of our sampling uncertainty". This is what separates an
assessment from a press release.

---

## 3. What is in the repo

Two cooperating Python packages.

**The orchestrator (`agentic_orchestrator/`)** is the subject under
test. It implements a support-ticket triage pipeline as a fixed
`planner → executor → verifier → finalizer` workflow on LangGraph. The
planner redacts personally identifying information *before* any model
call (privacy by ordering, not by post-hoc check), then asks the LLM for
a brief triage rationale. The executor classifies the sanitized ticket.
The verifier checks structural soundness and — when the model is
available — asks for an LLM soundness judgement. The finalizer is a
transparent rule on the audit. Three of the four nodes use the LLM; the
finalizer is deterministic on purpose.

Every model call is recorded with tokens, cache reads, and a versioned
dollar estimate. Every node is recorded with state before/after, output,
duration, and errors. Three families of tool faults can be injected
(crash, slow, semantic corruption). Three families of input
perturbations can be applied upstream (typo, paraphrase, prompt
injection). When the model is disabled, every node falls back to a
deterministic rule-based stand-in, so the entire system runs offline
and reproducibly — useful for baselines and for unit testing.

**The assessment layer (`assessment/`)** is the verifier. It runs
*campaigns* — every ticket in the dataset under every condition,
repeated across several seeds — and turns the resulting flat table of
results into:

- *metrics*: 15 functions, each a pure mapping from results to a
  scalar, grouped under three properties (robustness, availability,
  privacy);
- *confidence intervals* via clustered percentile bootstrap;
- *paired permutation tests* between conditions, for any comparison
  the campaign permits (e.g. accuracy under perturbation minus
  accuracy at baseline, paired by ticket and seed);
- *verdicts* via property oracles that compare thresholds to the
  conservative CI bound, marked PASS or FAIL;
- a *self-contained HTML report* with verdicts first, then
  comparisons, then per-condition breakdowns.

The dataset (60 tickets) carries an explicit annotation schema and
includes adversarial PII cases designed to escape the orchestrator's
own redactor — they are what makes the privacy metric discriminate
between good and bad systems.

The two packages communicate through one contract: the assessment
harness calls a single entry point on the orchestrator, gets back a
final state and a trace path. Swap the orchestrator for any other system
that respects this contract and the entire assessment machinery applies
unchanged.

---

## 4. Install

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt          # langgraph, anthropic, pydantic, dotenv
pip install pytest                       # for the test suite
cp .env.example .env
# set ANTHROPIC_API_KEY in .env to enable the LLM-driven agent
```

Dependencies are pinned to exact versions for reproducibility. Optional
extras (OpenTelemetry, Presidio for the privacy cross-check, pytest) are
documented in `requirements.txt` but not installed by default.

**LLM vs offline.** With `ANTHROPIC_API_KEY` set, the planner / executor
/ verifier run as real Claude calls (model controlled by `LLM_MODEL`,
default `claude-opus-4-7`). Without it (or with `LLM_ENABLED=false`),
the system automatically falls back to deterministic rule-based
stand-ins. The same code path serves both — see *Reproducibility* below.

---

## 5. Run a single ticket

```bash
# nominal
python main.py --input "Urgent: charged twice, refund me at john@acme.com"

# robustness — perturbed input (typo)
python main.py --input "payment error, urgent" --perturb --seed 3

# availability — inject tool failures
python main.py --input "login broken" --failure-rate 1.0 --seed 1

# fully offline (deterministic baseline)
LLM_ENABLED=false python main.py --input "billing refund"
```

Every run writes `runs/<run_id>.json` (the full per-run record) and
appends one line per node to `runs/events.jsonl`. Exit code is 0 if the
final decision is *accepted*, 1 otherwise.

---

## 6. Run an assessment campaign

```bash
# full campaign, current dataset, default conditions
python -m assessment.run

# offline — free, deterministic baseline
python -m assessment.run --offline

# quick smoke
python -m assessment.run --offline --limit 5 --seeds 1

# live on a specific model (Haiku is fast and cheap; default is Opus)
LLM_MODEL=claude-haiku-4-5 python -m assessment.run --limit 10 --seeds 1

# tag the output dir (recommended for campaigns you'll keep)
python -m assessment.run --label haiku-4-5-smoke

# pick a different privacy oracle
python -m assessment.run --privacy-oracle presidio  # if installed

# disable bootstrap (skip CI / comparisons — much faster)
python -m assessment.run --bootstrap-iters 0
```

Each campaign lands in a date-stamped subdirectory of `--out` (default
`assessment_runs/`) so successive runs do not overwrite each other:

```
assessment_runs/2026-06-08T14-30-22/                 # auto timestamp
assessment_runs/2026-06-08-haiku-4-5-smoke/          # with --label
```

The subdirectory contains:

| File | Content |
| --- | --- |
| `campaign.json` | metadata: timestamp, model, seeds, label, command line |
| `results.json` | one row per run (flat, machine-readable) |
| `metrics.json` | metrics (value, CI low/high, n) + comparisons (Δ, p-value, n) |
| `runs/` | the full orchestrator traces |
| `report.html` | verdicts, comparisons, per-condition breakdown |

Campaigns worth preserving (e.g. those backing a published claim) live
under `experiments/` in version control — see
[`experiments/README.md`](experiments/README.md) for the convention.

The default condition set is seven cells:

| Condition | What it stresses |
| --- | --- |
| `baseline` | nominal operation |
| `perturbation_typo` | resilience to lexical noise |
| `perturbation_paraphrase` | semantic stability of the classifier |
| `perturbation_injection` | resistance to prompt-injection attacks |
| `fault_crash` | graceful degradation when a tool raises |
| `fault_slow` | tail latency under a slow tool |
| `fault_corrupt` | does the verifier catch syntactically-valid but semantically-wrong tool output? |

---

## 7. Read the output

The HTML report leads with the property verdicts. Each line shows:

- the measured value;
- the 95% confidence interval (clustered bootstrap, 1000 resamples by
  default, seeded);
- the threshold the property is checked against;
- PASS or FAIL — using the conservative CI bound, not the point
  estimate.

A PASS means the property holds even at the unfavourable end of the CI.
A FAIL can mean either "the property is genuinely violated" or "you
don't have enough data to assert it" — both are valid reasons not to
ship a claim. The paired-comparisons block reports differences between
conditions with permutation-test p-values, highlighting comparisons with
p < 0.05.

Sample-size diagnostic:

```bash
python -m assessment.power assessment_runs/results.json
```

reports CI widths and the ticket budget required to roughly halve them
(`CI_width ∝ 1/√n` asymptotic). Use it when a metric's CI is too wide
to support the verdict you want to make.

---

## 8. Tests

```bash
pytest -q
```

The suite runs offline (forced by an autouse fixture), so it never hits
the live API and is fully deterministic. It currently covers:
the orchestrator graph (nominal, faulted, trace completeness, event
log), the privacy guardrail, the rule-based classifier, the perturbation
families (typo / paraphrase / injection), the fault model (crash / slow
/ corrupt + the expected blind spot of the structural verifier), the
independent PII detectors (including the contract test pinning
non-circularity), the statistical engine (bootstrap, permutation test,
CI-aware oracle, registry consistency), and end-to-end determinism (same
seed ⇒ identical trace modulo timestamps).

---

## 9. Reproducibility

Three pieces work together so any reported number can be re-derived
later:

- **Pinned dependencies** in `requirements.txt`. Bump deliberately.
- **Deterministic offline mode** (`LLM_ENABLED=false`). Same input, same
  seed ⇒ bit-identical trace modulo wall-clock fields. Enforced by
  `tests/test_determinism.py`.
- **Model and pricing versioned in code.** `LLM_MODEL` controls which
  model is used; a static per-model price table converts tokens to USD.
  The trace records the exact model id and the derived cost, so cost
  numbers are reproducible across reruns.
- **Seeded statistics.** The bootstrap and the permutation test use
  their own seeded RNG (`--bootstrap-seed`), so the report is
  bit-identical across reruns at fixed seeds.

---

## 10. Extending: a different agent, the same assessment

The assessment layer treats the orchestrator as a black box behind a
small contract:

```python
state, trace_path = run_once(input_text, settings, seed=…, run_id=…)
```

To assess a different system, implement that contract (or wrap an
existing system in it), reuse the assessment package as-is, and add
PropertySpecs for any task-specific thresholds. Nothing in
`assessment/` is triage-specific except the dataset, which is itself a
swappable JSONL with a documented schema.

---

## 11. Layout

```
agentic_orchestrator/
  config.py          # Settings (from .env), fault model assembler
  state.py           # OrchestratorState + trace record types
  llm.py             # the single Anthropic seam + pricing table
  tracing.py         # Tracer (per-run JSON + events.jsonl + optional OTel)
  privacy.py         # PII detection / redaction guardrail
  perturbation.py    # typo / paraphrase / prompt_injection families
  graph.py           # LangGraph wiring + run driver
  tools/
    classifier.py    # rule-based offline classifier
    failure.py       # fault model: crash / slow / corrupt
  nodes/             # planner / executor / verifier / finalizer

assessment/
  tickets.jsonl      # labelled dataset (60 tickets, with adversarial PII)
  tickets.schema.md  # annotation guide
  harness.py         # Condition × Ticket × seed campaign driver
  detectors.py       # independent PII oracles (regex_strong / presidio / llm_judge)
  metrics.py         # registry of metric functions over ExperimentResult
  stats.py           # clustered bootstrap CI + paired permutation test
  oracles.py         # PropertySpec → Verdict (CI-aware)
  report.py          # self-contained HTML report
  power.py           # sample-size diagnostic
  run.py             # CLI

main.py              # single-ticket CLI
tests/               # pytest suite (offline, deterministic)
experiments/         # archived, date-labelled campaigns (version-controlled)
docs/PROGRESS.md     # phase-by-phase technical change log
architecture.md      # design rationale
```

---

## 12. Status

The current code corresponds to four phases of work, documented in
detail in [`docs/PROGRESS.md`](docs/PROGRESS.md):

- **Phase 0** — reproducibility foundations: pinning, deterministic
  offline mode, model as a configurable condition, cost and cache
  accounting in every trace.
- **Phase 1** — independent privacy oracle and adversarial dataset. The
  privacy leakage metric stops being circular; the dataset grows to 60
  tickets including obfuscated PII (spaced digits, unicode lookalikes,
  word-spelled numbers, `[at]` / `[dot]` patterns, false-positive
  traps).
- **Phase 2** — statistical rigor: clustered percentile bootstrap CI on
  every metric, paired permutation tests between conditions, oracles
  that compare against the conservative CI bound, sample-size
  diagnostic.
- **Phase 3** — perturbation and fault *families*: three perturbation
  modes (typo, paraphrase, prompt injection) and three fault modes
  (crash, slow, corrupt), each with dedicated metrics. The new
  `verifier.catch_rate` metric exposes a structural verifier's blind
  spot to semantic corruption — a deliberate negative result that
  motivates the next phase.

**Phase 4 (planned)** introduces an explicit *scale of agenticity* —
five levels from a fixed graph (the current system) to a multi-agent
delegating planner — and uses the assessment layer to measure how each
non-functional property degrades as the system is granted more
autonomy. That is the central experimental contribution the framework
is being built to support.

See [`architecture.md`](architecture.md) for the design rationale and
[`docs/PROGRESS.md`](docs/PROGRESS.md) for the full change log.
