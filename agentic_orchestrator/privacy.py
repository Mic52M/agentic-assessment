"""Privacy guardrail: detect and redact sensitive data in free text.

Regex-based and intentionally conservative — this is a research prototype
for *measuring* privacy behaviour, not a production DLP engine. Each finding
is reported with a masked preview only; raw values never leave this module.
"""

from __future__ import annotations

import re
from typing import NamedTuple

from .state import PiiFinding

# Patterns ordered most-specific first so e.g. an IBAN isn't caught as a phone.
_PATTERNS: dict[str, re.Pattern[str]] = {
    "email": re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
    "credit_card": re.compile(r"\b(?:\d[ -]?){13,16}\b"),
    "iban": re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{10,30}\b"),
    "phone": re.compile(r"(?<!\d)(?:\+?\d{1,3}[ .-]?)?(?:\(?\d{2,4}\)?[ .-]?){2,4}\d{2,4}(?!\d)"),
}

_REDACTION_LABELS = {
    "email": "[EMAIL]",
    "credit_card": "[CREDIT_CARD]",
    "iban": "[IBAN]",
    "phone": "[PHONE]",
}


class Detection(NamedTuple):
    findings: list[PiiFinding]
    redacted_text: str


def _mask(value: str) -> str:
    """Keep only the shape of a value: first/last char, rest masked."""
    if len(value) <= 2:
        return "*" * len(value)
    return f"{value[0]}{'*' * (len(value) - 2)}{value[-1]}"


def scan_and_redact(text: str) -> Detection:
    """Find PII and return both the findings and a redacted copy of the text.

    Redaction is applied left-to-right with non-overlapping matches; once a
    span is claimed by a more specific pattern it is not re-matched.
    """
    findings: list[PiiFinding] = []
    claimed: list[tuple[int, int]] = []

    def overlaps(start: int, end: int) -> bool:
        return any(start < c_end and end > c_start for c_start, c_end in claimed)

    for kind, pattern in _PATTERNS.items():
        for m in pattern.finditer(text):
            start, end = m.start(), m.end()
            if overlaps(start, end):
                continue
            raw = m.group()
            if kind in {"credit_card", "phone"} and sum(c.isdigit() for c in raw) < 7:
                continue  # too short to be meaningful; avoid false positives
            claimed.append((start, end))
            findings.append(
                PiiFinding(kind=kind, value_preview=_mask(raw), span=(start, end))
            )

    # Build redacted text by replacing claimed spans (right-to-left).
    redacted = text
    for (start, end), finding in sorted(
        zip(claimed, findings), key=lambda p: p[0][0], reverse=True
    ):
        label = _REDACTION_LABELS.get(finding["kind"], "[REDACTED]")
        redacted = redacted[:start] + label + redacted[end:]

    findings.sort(key=lambda f: f["span"][0])
    return Detection(findings=findings, redacted_text=redacted)
