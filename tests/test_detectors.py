"""Tests for the independent privacy detectors.

The leakage metric is only meaningful if the detector can catch what the
orchestrator's redactor misses. These tests pin that contract for the
`regex_strong` detector against the adversarial cases in the dataset.
"""

from __future__ import annotations

import dataclasses

import pytest

from agentic_orchestrator.config import Settings
from agentic_orchestrator.graph import run_once
from agentic_orchestrator.privacy import scan_and_redact
from assessment import detectors


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    monkeypatch.setenv("LLM_ENABLED", "false")


# --- regex_strong unit tests ----------------------------------------------

@pytest.mark.parametrize(
    "text",
    [
        "contact: john [at] acme [dot] com",
        "reach mary dot smith at example dot org",
        "card 4 1 1 1  1 1 1 1  1 1 1 1  1 1 1 1",
        "card 4111.1111.1111.1111",
        "phone five five five one two three four five six seven",
        "ssn 123-45-6789",
        "iban DE89 3704 0044 0532 0130 00",
        "email jоhn@acme.com",  # cyrillic 'о'
        "ping +1 555 1234567",
        "send to user＠domain.co",  # full-width @
    ],
)
def test_regex_strong_catches_adversarial(text):
    assert detectors.detect_residual(text, "regex_strong") is True


@pytest.mark.parametrize(
    "text",
    [
        "the app crashes when I click save",
        "order ORDER-1234567890 still pending",
        "build hash a1b2c3d4e5f6 broke things",
        "ticket REF-2024-0001 closed",
        "sku SKU-001-A-2345 listed twice",
    ],
)
def test_regex_strong_no_false_positive_on_traps(text):
    assert detectors.detect_residual(text, "regex_strong") is False


def test_regex_strong_strictly_stronger_than_orchestrator_redactor():
    """The whole point of Fase 1: catch PII the redactor misses."""
    obfuscated = "contact john [at] acme [dot] com please"
    redacted = scan_and_redact(obfuscated).redacted_text
    # The orchestrator's regex does NOT catch the [at]/[dot] obfuscation,
    # so the email survives into the sanitized text.
    assert "acme" in redacted
    # But the independent detector does catch it.
    assert detectors.detect_residual(redacted, "regex_strong") is True


# --- Integration: leakage metric now discriminates -------------------------

def test_leakage_rate_is_positive_on_adversarial_baseline(tmp_path):
    """End-to-end: an adversarial PII ticket run through the orchestrator
    produces sanitized text in which the independent detector still finds
    PII. This is the proof that the privacy metric is no longer circular."""
    settings = dataclasses.replace(Settings.from_env(), runs_dir=tmp_path)
    state, _ = run_once("contact john [at] acme [dot] com about the refund", settings)
    sanitized = state.get("sanitized_text", "")
    # Orchestrator's own regex missed it...
    assert "acme" in sanitized
    # ...but the independent detector finds residual PII.
    assert detectors.detect_residual(sanitized, "regex_strong") is True


def test_default_oracle_available():
    assert detectors.get(detectors.DEFAULT_ORACLE).available()


def test_llm_judge_unavailable_offline():
    # In offline test mode the LLM seam is gated off; the judge must report
    # unavailable rather than crashing.
    assert detectors.get("llm_judge").available() is False
