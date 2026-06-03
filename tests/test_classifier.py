from agentic_orchestrator.tools.classifier import classify


def test_billing_high_priority():
    clf = classify("Urgent: I was charged twice for my subscription, need a refund")
    assert clf["category"] == "billing"
    assert clf["priority"] == "high"


def test_technical_category():
    clf = classify("the app keeps showing an error and then it crashes")
    assert clf["category"] == "technical"


def test_unknown_falls_back_to_other():
    clf = classify("hello, just saying hi")
    assert clf["category"] == "other"
    assert clf["scores"]["other"] == 1.0
