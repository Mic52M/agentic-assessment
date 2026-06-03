from assessment.harness import ExperimentResult
from assessment.metrics import compute_metrics
from assessment.oracles import all_passed, evaluate


_FAMILY_FROM_CONDITION = {
    "baseline": ("none", "none"),
    "perturbation": ("typo", "none"),
    "fault": ("none", "crash"),
}


def _r(**kw) -> ExperimentResult:
    base = dict(
        ticket_id="t", condition="baseline", seed=0,
        expected_category="billing", expected_priority="high", contains_pii=False,
        predicted_category="billing", predicted_priority="high", source="rules",
        decision="accepted", completed=True, errors=0, total_ms=10.0, llm_tokens=0,
        pii_redacted=False, residual_pii=False,
    )
    base.update(kw)
    # Back-compat: derive family fields from the legacy condition name when
    # the test didn't set them explicitly.
    cond = base.get("condition", "baseline")
    if "perturbation_family" not in base and "fault_family" not in base:
        fam = _FAMILY_FROM_CONDITION.get(cond, ("none", "none"))
        base["perturbation_family"], base["fault_family"] = fam
    return ExperimentResult(**base)


def test_prediction_stability_detects_flip():
    results = [
        _r(ticket_id="a", condition="baseline", seed=0, predicted_category="billing"),
        _r(ticket_id="a", condition="perturbation", seed=0, predicted_category="technical"),
        _r(ticket_id="b", condition="baseline", seed=0, predicted_category="account"),
        _r(ticket_id="b", condition="perturbation", seed=0, predicted_category="account"),
    ]
    m = compute_metrics(results)
    assert m["robustness.prediction_stability"] == 0.5  # one flip of two


def test_accuracy_and_privacy_metrics():
    results = [
        _r(ticket_id="a", expected_category="billing", predicted_category="billing"),
        _r(ticket_id="b", expected_category="technical", predicted_category="billing"),
        # PII rows: predicted_category=None so they don't affect accuracy.
        _r(ticket_id="c", predicted_category=None, contains_pii=True,
           pii_redacted=True, residual_pii=False),
        _r(ticket_id="d", predicted_category=None, contains_pii=True,
           pii_redacted=False, residual_pii=True),
    ]
    m = compute_metrics(results)
    assert m["robustness.accuracy_baseline"] == 0.5
    assert m["privacy.redaction_coverage"] == 0.5
    assert m["privacy.pii_leakage_rate"] == 0.5


def test_oracles_pass_on_clean_run():
    results = [_r() for _ in range(5)] + [
        _r(condition="perturbation"), _r(condition="fault")
    ]
    verdicts = evaluate(compute_metrics(results))
    # leakage 0, coverage 1 (no pii tickets), completion 1, accuracy 1, stable 1
    assert all_passed(verdicts)


def test_oracle_fails_on_pii_leak():
    results = [_r(contains_pii=True, pii_redacted=False, residual_pii=True)]
    verdicts = evaluate(compute_metrics(results))
    leak = next(v for v in verdicts if v.spec.metric == "privacy.pii_leakage_rate")
    assert not leak.passed
