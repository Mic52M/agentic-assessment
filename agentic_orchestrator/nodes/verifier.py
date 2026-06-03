"""Verifier / auditor node.

Combines deterministic checks with an LLM judgment, producing an AuditResult
the finalizer consumes. The checks map onto the non-functional properties:
  * privacy   — re-scan sanitized text; any residual PII means redaction failed
  * robustness — required outputs present despite tool failures
  * validity  — classification is well-formed (category/priority in range)
  * soundness — (LLM) does the classification actually fit the ticket?
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from .. import llm
from ..privacy import scan_and_redact
from ..state import AuditResult, OrchestratorState

_VALID_PRIORITIES = {"low", "medium", "high"}

_SYSTEM = """You audit a support-ticket classification. Given the ticket and the
proposed category/priority, decide whether the classification is sound. List any
concrete concerns (wrong category, mis-rated priority, unsupported by the text).
Be strict but fair; an empty concern list means you agree."""


class _AuditLLM(BaseModel):
    sound: bool = Field(description="True if the classification fits the ticket.")
    concerns: list[str] = Field(
        default_factory=list, description="Specific issues, empty if sound."
    )


def verifier_node(state: OrchestratorState) -> dict[str, Any]:
    issues: list[str] = []
    sanitized = state.get("sanitized_text", "")
    classification = state.get("classification")
    errors = state.get("errors", [])
    llm_calls: list[Any] = list(state.get("llm_calls", []))

    # Privacy: residual PII in text we already sanitized = redaction failure.
    residual = scan_and_redact(sanitized)
    if residual.findings:
        issues.append(
            "residual PII after sanitization: "
            + ", ".join(f["kind"] for f in residual.findings)
        )

    # Robustness: did we get a classification at all?
    if classification is None:
        issues.append("missing classification (executor failure)")
    else:
        if not classification.get("category"):
            issues.append("classification has no category")
        if classification.get("priority") not in _VALID_PRIORITIES:
            issues.append("classification has invalid priority")

    if errors:
        issues.append(f"{len(errors)} error(s) occurred upstream")

    # Soundness: LLM judgment on whether the label fits (best-effort).
    if classification is not None and llm.is_available():
        user = (
            f"Ticket:\n{sanitized}\n\n"
            f"Proposed category: {classification.get('category')}\n"
            f"Proposed priority: {classification.get('priority')}"
        )
        try:
            audit, usage = llm.structured(_SYSTEM, user, _AuditLLM)
            llm_calls.append(llm.as_llm_call("verifier", usage))
            if not audit.sound:
                issues.extend(audit.concerns or ["classifier judged unsound by auditor"])
        except Exception as exc:
            issues.append(f"auditor LLM error: {type(exc).__name__}")

    audit_result = AuditResult(passed=len(issues) == 0, issues=issues, pii_findings=[])
    return {"audit": audit_result, "llm_calls": llm_calls}
