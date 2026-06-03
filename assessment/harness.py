"""Experiment harness: run reproducible assessment campaigns.

A campaign runs every ticket in the dataset under a set of conditions
(baseline, perturbation, fault injection), repeated over several seeds. Each
(ticket, condition, seed) triple is one orchestrator run; the harness records a
flat ExperimentResult per run for the metrics layer to consume.

Everything is seed-driven and condition-driven, so a campaign is reproducible.
It works in LLM mode or the offline rule-based fallback (free, deterministic) —
the same machinery, so you can dry-run a campaign before spending tokens.
"""

from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass, field
from pathlib import Path

from agentic_orchestrator.config import Settings
from agentic_orchestrator.graph import run_once
from agentic_orchestrator.perturbation import PerturbationFamily, perturb

from . import detectors

_DATASET = Path(__file__).parent / "tickets.jsonl"

_PERTURBATION_NAMES = {f.value for f in PerturbationFamily} - {"none"}
_FAULT_NAMES = {"crash", "slow", "corrupt"}


@dataclass(frozen=True)
class Condition:
    """One experimental cell.

    ``perturbation_family`` and ``fault_family`` carry the family identity
    so the metrics layer can filter on it without parsing condition names.
    The legacy ``perturb`` / ``failure_rate`` fields are kept so old
    Condition literals continue to work.
    """

    name: str
    perturb: bool = False  # legacy; superseded by perturbation_family != "none"
    intensity: float = 0.15
    failure_rate: float = 0.0
    perturbation_family: str = "none"
    fault_family: str = "none"
    slow_rate: float = 0.0
    slow_ms: float = 0.0
    corrupt_rate: float = 0.0


DEFAULT_CONDITIONS = [
    Condition("baseline"),
    Condition(
        "perturbation_typo",
        perturb=True, intensity=0.15,
        perturbation_family=PerturbationFamily.TYPO.value,
    ),
    Condition(
        "perturbation_paraphrase",
        perturb=True, intensity=0.30,
        perturbation_family=PerturbationFamily.PARAPHRASE.value,
    ),
    Condition(
        "perturbation_injection",
        perturb=True,
        perturbation_family=PerturbationFamily.PROMPT_INJECTION.value,
    ),
    Condition(
        "fault_crash",
        failure_rate=0.5,
        fault_family="crash",
    ),
    Condition(
        "fault_slow",
        slow_rate=1.0, slow_ms=20.0,
        fault_family="slow",
    ),
    Condition(
        "fault_corrupt",
        corrupt_rate=1.0,
        fault_family="corrupt",
    ),
]


@dataclass
class Ticket:
    id: str
    text: str
    expected_category: str
    expected_priority: str
    contains_pii: bool
    pii_subcategory: str | None = None  # e.g. "plain" | "adversarial" | None


@dataclass
class ExperimentResult:
    ticket_id: str
    condition: str
    seed: int
    expected_category: str
    expected_priority: str
    contains_pii: bool
    # outcomes
    predicted_category: str | None
    predicted_priority: str | None
    source: str | None
    decision: str | None
    completed: bool  # produced a decision without crashing
    errors: int
    total_ms: float
    llm_tokens: int
    # privacy evidence
    pii_redacted: bool  # a redaction guardrail event fired
    residual_pii: bool  # detectable PII survived into the classifier input
    # condition tagging (optional, defaults preserve back-compat for tests)
    perturbation_family: str = "none"
    fault_family: str = "none"


def load_dataset(limit: int | None = None, path: Path = _DATASET) -> list[Ticket]:
    tickets: list[Ticket] = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                tickets.append(Ticket(**json.loads(line)))
    return tickets[:limit] if limit else tickets


def _result_from_state(
    ticket: Ticket,
    cond: Condition,
    seed: int,
    state,
    trace_path: Path,
    privacy_oracle: str = detectors.DEFAULT_ORACLE,
) -> ExperimentResult:
    clf = state.get("classification") or {}
    decision = state.get("decision") or {}
    sanitized = state.get("sanitized_text", "")
    redaction_fired = any(
        e["kind"] == "pii_redaction" for e in state.get("guardrail_events", [])
    )
    # Leakage measured with an *independent* detector, not the redactor under
    # test — otherwise the metric would be circular by construction.
    residual = detectors.detect_residual(sanitized, oracle=privacy_oracle)
    llm_tokens = sum(
        c["input_tokens"] + c["output_tokens"] for c in state.get("llm_calls", [])
    )
    total_ms = json.loads(trace_path.read_text())["total_duration_ms"]
    return ExperimentResult(
        ticket_id=ticket.id,
        condition=cond.name,
        seed=seed,
        expected_category=ticket.expected_category,
        expected_priority=ticket.expected_priority,
        contains_pii=ticket.contains_pii,
        predicted_category=clf.get("category"),
        predicted_priority=clf.get("priority"),
        source=clf.get("source"),
        decision=decision.get("outcome"),
        completed=decision.get("outcome") in {"accepted", "rejected"},
        errors=len(state.get("errors", [])),
        total_ms=total_ms,
        llm_tokens=llm_tokens,
        pii_redacted=redaction_fired,
        residual_pii=residual,
        perturbation_family=cond.perturbation_family,
        fault_family=cond.fault_family,
    )


def run_campaign(
    *,
    conditions: list[Condition] = DEFAULT_CONDITIONS,
    seeds: int = 3,
    limit: int | None = None,
    out_dir: Path = Path("assessment_runs"),
    verbose: bool = True,
    privacy_oracle: str = detectors.DEFAULT_ORACLE,
) -> tuple[list[ExperimentResult], Path]:
    """Execute a campaign and persist the raw results. Returns (results, dir).

    Prints per-run progress (verbose) — a live campaign runs many sequential
    LLM calls and would otherwise look frozen.
    """
    tickets = load_dataset(limit)
    base = Settings.from_env()
    campaign_dir = out_dir
    runs_dir = campaign_dir / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)

    total = len(tickets) * seeds * len(conditions)
    if verbose:
        print(f"campaign: {total} runs ({len(tickets)} tickets x {seeds} seeds "
              f"x {len(conditions)} conditions)", flush=True)

    results: list[ExperimentResult] = []
    done = 0
    for ticket in tickets:
        for seed in range(seeds):
            for cond in conditions:
                if cond.perturb and cond.perturbation_family != "none":
                    text = perturb(
                        ticket.text,
                        family=cond.perturbation_family,
                        seed=seed,
                        intensity=cond.intensity,
                    )
                else:
                    text = ticket.text
                settings = dataclasses.replace(
                    base,
                    runs_dir=runs_dir,
                    tool_failure_rate=cond.failure_rate,
                    tool_slow_rate=cond.slow_rate,
                    tool_slow_ms=cond.slow_ms,
                    tool_corrupt_rate=cond.corrupt_rate,
                )
                run_id = f"{cond.name}-{ticket.id}-s{seed}"
                state, trace_path = run_once(text, settings, seed=seed, run_id=run_id)
                result = _result_from_state(
                    ticket, cond, seed, state, trace_path,
                    privacy_oracle=privacy_oracle,
                )
                results.append(result)
                done += 1
                if verbose:
                    print(
                        f"  [{done:>3}/{total}] {run_id:<22} "
                        f"-> {result.predicted_category or '—':<10} "
                        f"({result.decision or '—'})",
                        flush=True,
                    )

    results_path = campaign_dir / "results.json"
    results_path.write_text(
        json.dumps([dataclasses.asdict(r) for r in results], indent=2),
        encoding="utf-8",
    )
    return results, campaign_dir
