"""Tests for the perturbation families introduced in Fase 3."""

from __future__ import annotations

import pytest

from agentic_orchestrator.perturbation import PerturbationFamily, perturb


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    monkeypatch.setenv("LLM_ENABLED", "false")


# --- none -----------------------------------------------------------------

def test_none_family_returns_input():
    assert perturb("hello world", family="none") == "hello world"


# --- typo -----------------------------------------------------------------

def test_typo_is_seeded():
    a = perturb("the app keeps crashing", family="typo", seed=1, intensity=0.5)
    b = perturb("the app keeps crashing", family="typo", seed=1, intensity=0.5)
    c = perturb("the app keeps crashing", family="typo", seed=2, intensity=0.5)
    assert a == b
    assert a != c


def test_typo_preserves_length():
    text = "I have a billing issue, please refund"
    out = perturb(text, family="typo", seed=0, intensity=0.5)
    assert len(out) == len(text)


# --- paraphrase (offline) -------------------------------------------------

def test_paraphrase_offline_changes_known_words():
    text = "Urgent: please refund the charged amount"
    out = perturb(text, family="paraphrase", seed=0, intensity=0.5)
    # Each of "Urgent", "please", "refund", "charged" is in the synonym map.
    assert out != text


def test_paraphrase_offline_is_seeded():
    text = "Urgent: please refund the charged amount"
    a = perturb(text, family="paraphrase", seed=3, intensity=0.5)
    b = perturb(text, family="paraphrase", seed=3, intensity=0.5)
    assert a == b


# --- prompt injection -----------------------------------------------------

def test_injection_appends_fixed_suffix():
    text = "I cannot log in"
    out = perturb(text, family="prompt_injection", seed=0)
    assert out.startswith(text)
    assert "ignore the categories" in out
    assert "other" in out
    assert "low" in out


def test_injection_is_idempotent_w_r_t_seed():
    """The injection family doesn't use the seed — it must produce the same
    suffix regardless. This pins the contract for reproducibility studies."""
    a = perturb("ping", family="prompt_injection", seed=0)
    b = perturb("ping", family="prompt_injection", seed=999)
    assert a == b


# --- dispatch -------------------------------------------------------------

def test_perturbation_family_enum_dispatch():
    text = "the app keeps crashing"
    via_enum = perturb(text, family=PerturbationFamily.TYPO, seed=0, intensity=0.3)
    via_str = perturb(text, family="typo", seed=0, intensity=0.3)
    assert via_enum == via_str


def test_unknown_family_raises():
    with pytest.raises(ValueError):
        perturb("hi", family="not_a_family")
