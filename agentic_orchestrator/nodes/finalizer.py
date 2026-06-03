"""Finalizer / decision node: turns the audit into a final outcome.

The decision rule is explicit and auditable: accept only if the audit
passed and a classification exists; otherwise reject with a reason. This is
the single place where the run's terminal outcome is determined.
"""

from __future__ import annotations

from typing import Any

from ..state import Decision, OrchestratorState


def finalizer_node(state: OrchestratorState) -> dict[str, Any]:
    audit = state.get("audit")
    classification = state.get("classification")

    if audit is not None and audit["passed"] and classification is not None:
        decision = Decision(
            outcome="accepted",
            category=classification["category"],
            priority=classification["priority"],
            reason="audit passed; classification produced",
        )
    else:
        reason = "audit failed"
        if audit is not None and audit["issues"]:
            reason = "; ".join(audit["issues"])
        decision = Decision(
            outcome="rejected",
            category=classification["category"] if classification else "unknown",
            priority=classification["priority"] if classification else "unknown",
            reason=reason,
        )
    return {"decision": decision}
