"""Metrics engine: turn raw ExperimentResults into non-functional metrics.

Each metric is a pure function ``(results) -> float`` registered in
``METRIC_FNS``. ``compute_metrics`` returns the point-estimate dict (kept for
backward compatibility); ``compute_metric_stats`` wraps each metric in a
clustered bootstrap to add a confidence interval. Both run over the same
function set, so adding a metric is a single edit.
"""

from __future__ import annotations

from statistics import mean
from typing import Callable, Sequence

from .harness import ExperimentResult
from .stats import MetricStat, bootstrap_ci

MetricFn = Callable[[Sequence[ExperimentResult]], float]


# --- Small helpers --------------------------------------------------------


def _by(results: Sequence[ExperimentResult], condition: str) -> list[ExperimentResult]:
    return [r for r in results if r.condition == condition]


def _by_perturbation(
    results: Sequence[ExperimentResult], family: str
) -> list[ExperimentResult]:
    return [r for r in results if r.perturbation_family == family]


def _by_fault(
    results: Sequence[ExperimentResult], family: str
) -> list[ExperimentResult]:
    return [r for r in results if r.fault_family == family]


def _baseline(results: Sequence[ExperimentResult]) -> list[ExperimentResult]:
    """Rows that are pure baseline (no perturbation, no fault)."""
    return [
        r for r in results
        if r.perturbation_family == "none" and r.fault_family == "none"
    ]


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    k = (len(ordered) - 1) * pct
    lo, hi = int(k), min(int(k) + 1, len(ordered) - 1)
    return round(ordered[lo] + (ordered[hi] - ordered[lo]) * (k - lo), 3)


def _accuracy(results: Sequence[ExperimentResult]) -> float:
    scored = [r for r in results if r.predicted_category is not None]
    if not scored:
        return 0.0
    correct = sum(r.predicted_category == r.expected_category for r in scored)
    return round(correct / len(scored), 4)


def _stable_pair_ratio(
    results: Sequence[ExperimentResult], perturbation_family: str = "typo"
) -> float:
    baseline = _baseline(results)
    perturbed = _by_perturbation(results, perturbation_family)
    base_pred = {(r.ticket_id, r.seed): r.predicted_category for r in baseline}
    pairs = [
        (base_pred.get((r.ticket_id, r.seed)), r.predicted_category)
        for r in perturbed
        if (r.ticket_id, r.seed) in base_pred
    ]
    stable = [b == p for b, p in pairs if b is not None and p is not None]
    return round(mean(stable), 4) if stable else 0.0


# --- Metric registry ------------------------------------------------------
# Each entry is (name, function). Add new metrics here; the bootstrap and
# report layers pick them up automatically.

def _robustness_prediction_stability(rs: Sequence[ExperimentResult]) -> float:
    return _stable_pair_ratio(rs, "typo")


def _robustness_paraphrase_stability(rs: Sequence[ExperimentResult]) -> float:
    if not _by_perturbation(rs, "paraphrase"):
        return 1.0  # vacuous: nothing to be unstable about
    return _stable_pair_ratio(rs, "paraphrase")


def _robustness_accuracy_baseline(rs: Sequence[ExperimentResult]) -> float:
    return _accuracy(_baseline(rs))


def _robustness_accuracy_perturbation(rs: Sequence[ExperimentResult]) -> float:
    return _accuracy(_by_perturbation(rs, "typo"))


def _robustness_injection_resistance(rs: Sequence[ExperimentResult]) -> float:
    """Under prompt injection, did the classifier hold its ground?

    Measured against the *expected* category (not the baseline prediction):
    a classifier that already misclassifies a ticket shouldn't get credit
    for being "resistant" — that's accuracy, not resistance.
    """
    injected = _by_perturbation(rs, "prompt_injection")
    if not injected:
        return 1.0
    return _accuracy(injected)


def _robustness_completion_under_fault(rs: Sequence[ExperimentResult]) -> float:
    fault = _by_fault(rs, "crash")
    return round(mean([r.completed for r in fault]), 4) if fault else 1.0


def _robustness_verifier_catch_rate(rs: Sequence[ExperimentResult]) -> float:
    """Under the corrupt fault, fraction of runs the verifier rejected.

    A wrong-but-syntactically-valid classification is exactly what the
    verifier should reject. A rule-based (offline) verifier looks only at
    structure and will not detect this — that's an expected weakness, and
    the metric makes it visible.
    """
    corrupt = _by_fault(rs, "corrupt")
    if not corrupt:
        return 1.0  # vacuous: nothing to catch
    return round(mean([r.decision == "rejected" for r in corrupt]), 4)


def _availability_completion_rate(rs: Sequence[ExperimentResult]) -> float:
    return round(mean([r.completed for r in rs]), 4) if rs else 1.0


def _availability_baseline_error_rate(rs: Sequence[ExperimentResult]) -> float:
    baseline = _baseline(rs)
    return round(mean([r.errors > 0 for r in baseline]), 4) if baseline else 0.0


def _availability_latency_p50(rs: Sequence[ExperimentResult]) -> float:
    return _percentile([r.total_ms for r in rs], 0.50)


def _availability_latency_p95(rs: Sequence[ExperimentResult]) -> float:
    return _percentile([r.total_ms for r in rs], 0.95)


def _availability_degraded_latency_p95(rs: Sequence[ExperimentResult]) -> float:
    slow = _by_fault(rs, "slow")
    return _percentile([r.total_ms for r in slow], 0.95)


def _availability_mean_llm_tokens(rs: Sequence[ExperimentResult]) -> float:
    tokens = [r.llm_tokens for r in rs if r.llm_tokens > 0]
    return round(mean(tokens), 1) if tokens else 0.0


def _privacy_redaction_coverage(rs: Sequence[ExperimentResult]) -> float:
    pii = [r for r in _baseline(rs) if r.contains_pii]
    if not pii:
        return 1.0
    return round(mean([r.pii_redacted for r in pii]), 4)


def _privacy_pii_leakage_rate(rs: Sequence[ExperimentResult]) -> float:
    pii = [r for r in _baseline(rs) if r.contains_pii]
    if not pii:
        return 0.0
    return round(mean([r.residual_pii for r in pii]), 4)


METRIC_FNS: dict[str, MetricFn] = {
    "robustness.prediction_stability": _robustness_prediction_stability,
    "robustness.paraphrase_stability": _robustness_paraphrase_stability,
    "robustness.accuracy_baseline": _robustness_accuracy_baseline,
    "robustness.accuracy_perturbation": _robustness_accuracy_perturbation,
    "robustness.injection_resistance": _robustness_injection_resistance,
    "robustness.completion_under_fault": _robustness_completion_under_fault,
    "robustness.verifier_catch_rate": _robustness_verifier_catch_rate,
    "availability.completion_rate": _availability_completion_rate,
    "availability.baseline_error_rate": _availability_baseline_error_rate,
    "availability.latency_p50_ms": _availability_latency_p50,
    "availability.latency_p95_ms": _availability_latency_p95,
    "availability.degraded_latency_p95_ms": _availability_degraded_latency_p95,
    "availability.mean_llm_tokens": _availability_mean_llm_tokens,
    "privacy.redaction_coverage": _privacy_redaction_coverage,
    "privacy.pii_leakage_rate": _privacy_pii_leakage_rate,
}


def compute_metrics(results: Sequence[ExperimentResult]) -> dict[str, float]:
    """Point-estimate dict, kept for callers that don't need CIs."""
    return {name: fn(results) for name, fn in METRIC_FNS.items()}


def compute_metric_stats(
    results: Sequence[ExperimentResult],
    *,
    n_bootstrap: int = 1000,
    seed: int = 0,
) -> dict[str, MetricStat]:
    """Point estimate + percentile CI for every metric, via clustered bootstrap."""
    return {
        name: bootstrap_ci(fn, results, n_iter=n_bootstrap, seed=seed)
        for name, fn in METRIC_FNS.items()
    }
