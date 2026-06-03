"""Local, rule-based ticket classifier.

Deliberately not LLM-backed in v1: keeps runs deterministic, reproducible
and free of any cloud dependency, which is what offline analysis of
non-functional properties needs. The interface (`classify`) is the seam
where an LLM-backed implementation can be dropped in later.
"""

from __future__ import annotations

from ..state import Classification

# category -> indicative keywords (lowercased)
_CATEGORY_KEYWORDS: dict[str, list[str]] = {
    "billing": ["invoice", "payment", "charge", "refund", "subscription", "price"],
    "technical": ["error", "bug", "crash", "broken", "not working", "fail", "exception"],
    "account": ["login", "password", "account", "sign in", "access", "locked"],
    "abuse": ["spam", "fraud", "abuse", "phishing", "scam"],
}

_HIGH_PRIORITY = ["urgent", "asap", "immediately", "critical", "down", "cannot access"]
_LOW_PRIORITY = ["whenever", "no rush", "minor", "question", "wondering"]


def classify(text: str) -> Classification:
    lowered = text.lower()

    scores: dict[str, float] = {}
    for category, keywords in _CATEGORY_KEYWORDS.items():
        hits = sum(1 for kw in keywords if kw in lowered)
        scores[category] = hits / len(keywords)

    best = max(scores, key=scores.get)
    if scores[best] == 0.0:
        best = "other"
    scores["other"] = 0.0 if any(v > 0 for v in scores.values()) else 1.0

    priority = "medium"
    if any(kw in lowered for kw in _HIGH_PRIORITY):
        priority = "high"
    elif any(kw in lowered for kw in _LOW_PRIORITY):
        priority = "low"

    return Classification(
        category=best,
        priority=priority,
        scores={k: round(v, 3) for k, v in scores.items()},
    )
