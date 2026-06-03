"""Tests for the statistical engine (Fase 2).

We don't try to test the statistical machinery exhaustively (that's what
peer-reviewed libraries are for). We pin the *contracts* that the rest of the
assessment layer relies on:

  * bootstrap_ci on a constant metric → CI collapses to a point.
  * bootstrap_ci is seeded → identical results on repeat.
  * paired_permutation_test on identical samples → p = 1.
  * paired_permutation_test on a strong, consistent effect → p is small.
  * Oracle uses the CI's conservative bound, not the point estimate.
"""

from __future__ import annotations

from assessment.harness import ExperimentResult
from assessment.metrics import compute_metric_stats
from assessment.oracles import PropertySpec, evaluate
from assessment.stats import (
    MetricStat,
    bootstrap_ci,
    mean_difference,
    paired_permutation_test,
)


def _r(**kw) -> ExperimentResult:
    base = dict(
        ticket_id="t", condition="baseline", seed=0,
        expected_category="billing", expected_priority="high", contains_pii=False,
        predicted_category="billing", predicted_priority="high", source="rules",
        decision="accepted", completed=True, errors=0, total_ms=10.0, llm_tokens=0,
        pii_redacted=False, residual_pii=False,
    )
    base.update(kw)
    return ExperimentResult(**base)


# --- bootstrap -------------------------------------------------------------

def test_bootstrap_constant_metric_has_zero_width_ci():
    results = [_r(ticket_id=f"t{i}", completed=True) for i in range(20)]
    stat = bootstrap_ci(lambda rs: 1.0, results, n_iter=200, seed=0)
    assert stat.value == 1.0
    assert stat.ci_low == 1.0 and stat.ci_high == 1.0
    assert stat.n == 20


def test_bootstrap_is_seeded():
    results = [
        _r(ticket_id=f"t{i}", completed=(i % 2 == 0)) for i in range(30)
    ]
    fn = lambda rs: sum(r.completed for r in rs) / len(rs)
    a = bootstrap_ci(fn, results, n_iter=200, seed=42)
    b = bootstrap_ci(fn, results, n_iter=200, seed=42)
    assert a == b


def test_bootstrap_ci_contains_point():
    results = [_r(ticket_id=f"t{i}", completed=(i < 15)) for i in range(30)]
    fn = lambda rs: sum(r.completed for r in rs) / len(rs)
    stat = bootstrap_ci(fn, results, n_iter=500, seed=0)
    assert stat.ci_low <= stat.value <= stat.ci_high


# --- permutation -----------------------------------------------------------

def test_permutation_identical_pairs_pvalue_one():
    pairs = [(0.5, 0.5)] * 20
    delta, p = paired_permutation_test(mean_difference, pairs, n_iter=500, seed=0)
    assert delta == 0.0
    assert p > 0.9  # essentially 1; +1 smoothing keeps it just below 1


def test_permutation_strong_effect_low_pvalue():
    pairs = [(0.0, 1.0)] * 30
    delta, p = paired_permutation_test(mean_difference, pairs, n_iter=500, seed=0)
    assert delta == 1.0
    # Every permutation either matches or flips the sign; the observed extreme
    # is matched by the all-flipped permutation only — p should be small.
    assert p < 0.05


# --- CI-aware oracle -------------------------------------------------------

def test_oracle_uses_lower_ci_bound_for_ge():
    spec = PropertySpec("robustness", "x", "m", ">=", 0.70)
    # Point estimate passes, but the CI lower bound does NOT — must FAIL.
    stat = MetricStat(value=0.75, ci_low=0.60, ci_high=0.85, n=10)
    (v,) = evaluate({"m": stat}, [spec])
    assert v.passed is False
    assert v.value == 0.75
    assert v.ci == (0.60, 0.85)


def test_oracle_uses_upper_ci_bound_for_le():
    spec = PropertySpec("availability", "x", "m", "<=", 0.05)
    # Point estimate passes, but the CI upper bound does NOT — must FAIL.
    stat = MetricStat(value=0.03, ci_low=0.01, ci_high=0.09, n=10)
    (v,) = evaluate({"m": stat}, [spec])
    assert v.passed is False


def test_oracle_backward_compatible_with_floats():
    spec = PropertySpec("robustness", "x", "m", ">=", 0.70)
    (v,) = evaluate({"m": 0.85}, [spec])
    assert v.passed is True
    assert v.ci is None


# --- compute_metric_stats round-trip --------------------------------------

def test_compute_metric_stats_keys_match_compute_metrics():
    from assessment.metrics import METRIC_FNS
    results = [_r(ticket_id=f"t{i}") for i in range(5)]
    stats = compute_metric_stats(results, n_bootstrap=50, seed=0)
    assert set(stats) == set(METRIC_FNS)
    for name, s in stats.items():
        assert isinstance(s, MetricStat)
