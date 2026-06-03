"""Statistical engine: clustered bootstrap CIs and paired permutation tests.

The assessment layer reports point estimates *with uncertainty*. Two tools
are exposed:

  * ``bootstrap_ci(metric_fn, results, ...)`` — percentile CI obtained by
    resampling tickets with replacement (clustered bootstrap). Tickets are
    the independent units of the experiment; seeds and conditions are
    repeated measures on the same ticket, so resampling individual rows
    would underestimate variance.
  * ``paired_permutation_test(metric_a, metric_b, results, ...)`` — exact
    randomisation test for a paired difference (e.g. baseline accuracy vs
    perturbed accuracy, paired by ticket+seed).

Both helpers are seeded so reports are reproducible.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Callable, Iterable, Sequence

from .harness import ExperimentResult

MetricFn = Callable[[Sequence[ExperimentResult]], float]


@dataclass(frozen=True)
class MetricStat:
    """A point estimate of a metric with a percentile confidence interval."""

    value: float
    ci_low: float
    ci_high: float
    n: int  # number of independent units (tickets) backing the estimate


@dataclass(frozen=True)
class Comparison:
    """Result of a paired permutation test."""

    name: str
    delta: float  # metric_b - metric_a on the observed data
    p_value: float
    n: int  # number of paired observations


def _by_ticket(results: Iterable[ExperimentResult]) -> dict[str, list[ExperimentResult]]:
    clusters: dict[str, list[ExperimentResult]] = {}
    for r in results:
        clusters.setdefault(r.ticket_id, []).append(r)
    return clusters


def bootstrap_ci(
    metric_fn: MetricFn,
    results: Sequence[ExperimentResult],
    *,
    n_iter: int = 1000,
    alpha: float = 0.05,
    seed: int = 0,
) -> MetricStat:
    """Percentile-bootstrap CI with ticket-level clustering.

    The point estimate is ``metric_fn(results)``. Resampling draws tickets
    (with replacement) and feeds the flattened resample to ``metric_fn``.
    If a resample yields an undefined metric (e.g. zero denominator), it is
    skipped — undefined metrics are not noise to model, they are absences.
    """
    clusters = _by_ticket(results)
    ticket_ids = list(clusters.keys())
    n_units = len(ticket_ids)
    point = metric_fn(results)
    if n_units < 2 or n_iter < 1:
        return MetricStat(value=point, ci_low=point, ci_high=point, n=n_units)

    rng = random.Random(seed)
    samples: list[float] = []
    for _ in range(n_iter):
        picked = [ticket_ids[rng.randrange(n_units)] for _ in range(n_units)]
        flat: list[ExperimentResult] = []
        for tid in picked:
            flat.extend(clusters[tid])
        try:
            samples.append(metric_fn(flat))
        except (ZeroDivisionError, ValueError):
            continue

    if not samples:
        return MetricStat(value=point, ci_low=point, ci_high=point, n=n_units)
    samples.sort()
    lo_idx = max(0, int(round((alpha / 2) * (len(samples) - 1))))
    hi_idx = min(len(samples) - 1, int(round((1 - alpha / 2) * (len(samples) - 1))))
    return MetricStat(
        value=round(point, 4),
        ci_low=round(samples[lo_idx], 4),
        ci_high=round(samples[hi_idx], 4),
        n=n_units,
    )


def paired_permutation_test(
    metric_fn: Callable[[Sequence[float], Sequence[float]], float],
    pairs: Sequence[tuple[float, float]],
    *,
    n_iter: int = 1000,
    seed: int = 0,
) -> tuple[float, float]:
    """Two-sided paired permutation test.

    ``pairs`` is a list of (a, b) observations for the same unit; under H0
    the labels a/b are exchangeable. ``metric_fn(a_values, b_values)`` is the
    test statistic — typically the mean difference ``mean(b) - mean(a)``.

    Returns ``(observed_delta, p_value)``. Both endpoints of the empirical
    distribution are considered (two-sided).
    """
    if not pairs:
        return 0.0, 1.0
    a_obs = [a for a, _ in pairs]
    b_obs = [b for _, b in pairs]
    observed = metric_fn(a_obs, b_obs)
    rng = random.Random(seed)
    extreme = 0
    for _ in range(n_iter):
        a_perm = []
        b_perm = []
        for a, b in pairs:
            if rng.random() < 0.5:
                a_perm.append(b)
                b_perm.append(a)
            else:
                a_perm.append(a)
                b_perm.append(b)
        stat = metric_fn(a_perm, b_perm)
        if abs(stat) >= abs(observed):
            extreme += 1
    # +1 smoothing avoids p=0 (the observed permutation is always extreme).
    p_value = (extreme + 1) / (n_iter + 1)
    return round(observed, 4), round(p_value, 4)


def mean_difference(a: Sequence[float], b: Sequence[float]) -> float:
    if not a or not b:
        return 0.0
    return sum(b) / len(b) - sum(a) / len(a)


# --- Standard condition comparisons --------------------------------------
#
# These extract paired observations from the campaign and feed them to the
# permutation test. Pairing is by (ticket_id, seed) so each pair compares the
# same ticket under different conditions.


def _accuracy_pairs(
    results: Sequence[ExperimentResult], cond_a: str, cond_b: str
) -> list[tuple[float, float]]:
    by_key = {(r.ticket_id, r.seed, r.condition): r for r in results}
    pairs: list[tuple[float, float]] = []
    for r in results:
        if r.condition != cond_a:
            continue
        other = by_key.get((r.ticket_id, r.seed, cond_b))
        if other is None or r.predicted_category is None or other.predicted_category is None:
            continue
        a = 1.0 if r.predicted_category == r.expected_category else 0.0
        b = 1.0 if other.predicted_category == other.expected_category else 0.0
        pairs.append((a, b))
    return pairs


def _completion_pairs(
    results: Sequence[ExperimentResult], cond_a: str, cond_b: str
) -> list[tuple[float, float]]:
    by_key = {(r.ticket_id, r.seed, r.condition): r for r in results}
    pairs: list[tuple[float, float]] = []
    for r in results:
        if r.condition != cond_a:
            continue
        other = by_key.get((r.ticket_id, r.seed, cond_b))
        if other is None:
            continue
        pairs.append((float(r.completed), float(other.completed)))
    return pairs


def standard_comparisons(
    results: Sequence[ExperimentResult],
    *,
    n_iter: int = 1000,
    seed: int = 0,
) -> list[Comparison]:
    """Default set of paired comparisons reported by every campaign."""
    out: list[Comparison] = []
    acc_bp = _accuracy_pairs(results, "baseline", "perturbation")
    if acc_bp:
        delta, p = paired_permutation_test(
            mean_difference, acc_bp, n_iter=n_iter, seed=seed
        )
        out.append(Comparison(
            name="accuracy: perturbation − baseline",
            delta=delta, p_value=p, n=len(acc_bp),
        ))
    comp_bf = _completion_pairs(results, "baseline", "fault")
    if comp_bf:
        delta, p = paired_permutation_test(
            mean_difference, comp_bf, n_iter=n_iter, seed=seed
        )
        out.append(Comparison(
            name="completion: fault − baseline",
            delta=delta, p_value=p, n=len(comp_bf),
        ))
    return out
